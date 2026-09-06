#!/usr/bin/env python3
"""Generate the assignability section from four sources that check each other.

M2's exit criterion says BP-VERIFY section 2 is generated rather than written. Section 2
is the type system the verifier actually has: which verification type may stand in for
which other, what kind of relation that turns out to be, and where the guarantee it gives
runs out.

Four sources, and the point of having four is that they disagree in interesting places:

  the specification    JVMS 4.10.1.2 is normative and it gives the relation as Prolog:
                       a hierarchy diagram of fourteen type terms, a page of `isAssignable`
                       clauses, and a handful of `isWideningReference` clauses that
                       carry every interesting case. Nobody has ever run these clauses, and one of
                       them does not parse.

  the implementation   `verificationType.cpp` is the same relation as a hundred lines of
                       C++, and `resolve_and_check_assignability` states in a comment what
                       the Prolog states in a rule head. `verifier.hpp` and `verifier.cpp`
                       hold the three class file versions that decide which verifier runs
                       and whether a refusal by one is final.

  the measurements     `probes/verify/results` is one class file per ordered pair of
                       sixteen types, loaded into a JVM, and the relation is which of them
                       loaded. It is the only one of the four sources that is an answer
                       rather than a description of how to get one.

  the arithmetic       three properties recomputed here from the raw cells, because the
                       probe computes them too and a claim about the shape of a relation
                       is worth nothing if two readers of the same 256 cells disagree.

  python tools/gen_verify.py            write docs/generated/verify.md
  python tools/gen_verify.py --check    fail if the committed page is stale
  python tools/gen_verify.py --print    the summary, without writing anything

The JDK files come from raw.githubusercontent.com at the tag in `docs/pin.json`, or from
a local checkout when `JVX_JDK_SRC` points at one. The specification chapter comes from
docs.oracle.com at the pinned edition and its hash is checked against the one
`tools/gen_jvms_index.py` recorded, so a chapter that was rewritten underneath us stops
this rather than quietly changing a table.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = ROOT / "docs" / "pin.json"
RESULTS = pathlib.Path("probes/verify/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OUTPUT = pathlib.Path("docs/generated/verify.md")

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
TYPE_CPP = "src/hotspot/share/classfile/verificationType.cpp"
TYPE_HPP = "src/hotspot/share/classfile/verificationType.hpp"
VERIFIER_HPP = "src/hotspot/share/classfile/verifier.hpp"
VERIFIER_CPP = "src/hotspot/share/classfile/verifier.cpp"
SPEC_PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"
SPEC_CHAPTER = "4"
SPEC_SECTION = "4.10.1.2"
SPEC_NEXT = "4.10.1.3"

LISTING = re.compile(r'<pre class="programlisting">(?P<body>.*?)</pre>', re.S)
# `isAssignable(class(_, _), X) :- isAssignable(reference, X).` and the fact form with no
# body at all. The head is everything up to `:-` or the closing period.
CLAUSE = re.compile(
    r"^(?P<name>isAssignable|isWideningReference)\((?P<args>.*?)\)\s*"
    r"(?::-(?P<body>.*?))?\.$", re.S)
# `STACKMAP_ATTRIBUTE_MAJOR_VERSION    = 50,` in the enum, and
# `#define NOFAILOVER_MAJOR_VERSION    51` in the .cpp, which are the same kind of number
# written two ways in two files.
ENUM_VERSION = re.compile(r"^\s*(?P<name>[A-Z][A-Z0-9_]*_VERSION)\s*=\s*(?P<value>\d+)\s*,?\s*$")
DEFINE_VERSION = re.compile(r"^#define\s+(?P<name>[A-Z][A-Z0-9_]*_VERSION)\s+(?P<value>\d+)\s*$")

# The fourteen atoms the hierarchy diagram draws, and which of them a class file can name.
# A term the diagram prints and this does not classify stops the generator, because a
# fifteenth type term is the whole subject changing rather than a table gaining a row.
WRITABLE = {
    "top": True, "oneWord": False, "twoWord": False,
    "int": True, "float": True, "long": True, "double": True,
    "reference": False, "null": True,
    "uninitialized": False, "uninitializedThis": True, "uninitialized(Offset)": True,
}
# The diagram draws these two as a box rather than as atoms, so they are named here.
BOXED = ["class(N, L)", "arrayOf(X)"]

# What each measured type is, in the words the grid needs and the specification does not
# use. A type in the results this does not describe stops the generator.
MEANS = {
    "top": "the type of a location the verifier will not let you read",
    "int": "an `int`, and every smaller integral type",
    "float": "a `float`",
    "long": "a `long`, in the first of its two locations",
    "double": "a `double`, in the first of its two locations",
    "null": "the `null` reference",
    "uninitializedThis": "`this`, in a constructor that has not chained yet",
    "uninitialized(Offset)": "an object the `new` at that offset made, unconstructed",
    "Object": "`java.lang.Object`, the top of the reference hierarchy",
    "String": "`java.lang.String`, a final class",
    "Runnable": "`java.lang.Runnable`, an interface `String` does not implement",
    "Cloneable": "`java.lang.Cloneable`, an interface arrays implement",
    "Serializable": "`java.io.Serializable`, the other interface arrays implement",
    "Object[]": "an array of references",
    "String[]": "an array of a narrower reference type",
    "int[]": "an array of a primitive",
}

# Which instructions in the census carry a check the verifier declined to make, and what
# each one throws when the check fails. An opcode in the results this does not describe
# stops the generator rather than appearing in the table with an empty cell.
COSTS = {
    "INVOKEINTERFACE": ("the receiver implements the interface",
                        "`IncompatibleClassChangeError`"),
    "INVOKEVIRTUAL": ("the receiver has the method", "`AbstractMethodError`"),
    "INVOKESPECIAL": ("the resolved method is callable here", "`AbstractMethodError`"),
    "INVOKESTATIC": ("the class resolves", "`NoSuchMethodError`"),
    "INVOKEDYNAMIC": ("the call site links", "`BootstrapMethodError`"),
    "CHECKCAST": ("the reference is of the named type", "`ClassCastException`"),
    "INSTANCEOF": ("the reference is of the named type", "nothing, it answers"),
    "AASTORE": ("the element fits the array's real component type",
                "`ArrayStoreException`"),
    "ATHROW": ("the thrown reference is a `Throwable`", "`VerifyError`, at verify time"),
    "NEW": ("the class resolves", "`NoClassDefFoundError`"),
}

WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
         13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen"}


def word(count: int) -> str:
    return WORDS.get(count, f"{count:,}")


def pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def source(tag: str, path: str) -> tuple[str, str]:
    """One JDK file, from a local checkout when there is one and the network when there
    is not, with the hash of what was read either way."""
    local = os.environ.get("JVX_JDK_SRC")
    if local:
        found = pathlib.Path(local) / path
        if not found.is_file():
            sys.exit(f"JVX_JDK_SRC is set but {found} is not there")
        body = found.read_bytes()
    else:
        body = fetch(RAW.format(tag=tag, path=path))
    return body.decode("utf-8", errors="replace"), hashlib.sha256(body).hexdigest()


def section_of(page: str) -> str:
    """The part of the chapter that is 4.10.1.2 and nothing else.

    Chapter 4 prints Prolog in a dozen sections and `isAssignable` is called from several
    of them, so cutting first is what stops a clause from the method type checker being
    read as a clause of the assignability relation.
    """
    start = page.find(f'<a name="jvms-{SPEC_SECTION}"></a>')
    if start < 0:
        sys.exit(f"JVMS chapter {SPEC_CHAPTER} has no section {SPEC_SECTION} anchor, so "
                 f"the page shape changed")
    after = page.find(f'<a name="jvms-{SPEC_NEXT}"></a>', start)
    if after < 0:
        sys.exit(f"JVMS {SPEC_SECTION} no longer ends at {SPEC_NEXT}, so the page shape "
                 f"changed")
    return page[start:after]


def listings(text: str) -> list[str]:
    """Every `programlisting` box in the section, as plain text."""
    found = []
    for block in LISTING.finditer(text):
        body = html.unescape(re.sub(r"<[^>]+>", "", block.group("body")))
        if body.strip():
            found.append(body.strip("\n"))
    if not found:
        sys.exit(f"JVMS {SPEC_SECTION} prints no program listings, so the page shape "
                 f"changed")
    return found


def hierarchy(boxes: list[str]) -> list[str]:
    """The type terms the hierarchy diagram draws, in the order it draws them.

    The diagram is ASCII art, so this takes the words out of it rather than parsing it.
    Every word that is not part of the drawing is a type term, and the two reference
    shapes the diagram puts in a box instead of naming are added back by hand.
    """
    drawn = next((one for one in boxes if "hierarchy" in one.split("\n")[0]), None)
    if drawn is None:
        sys.exit(f"JVMS {SPEC_SECTION} no longer opens with a type hierarchy diagram")
    seen: list[str] = []
    for token in re.findall(r"[A-Za-z]+(?:\([A-Za-z]+\))?", drawn):
        if token in ("Verification", "type", "hierarchy", "reference") and token not in WRITABLE:
            continue
        if token not in seen:
            seen.append(token)
    terms = [one for one in seen if one in WRITABLE]
    missing = [one for one in WRITABLE if one not in terms]
    if missing:
        sys.exit(f"JVMS {SPEC_SECTION}'s hierarchy diagram no longer draws {missing}, so "
                 f"the type hierarchy changed and this generator's table is wrong")
    return terms


def clauses(boxes: list[str]) -> list[dict]:
    """Every `isAssignable` or `isWideningReference` clause in the section.

    A Prolog clause is a head, an optional body of comma separated goals, and a period.
    This keeps the body as written rather than as parsed, because the interesting thing
    about one of these clauses is exactly that it does not parse.
    """
    found: list[dict] = []
    for box in boxes:
        for chunk in re.split(r"\n\s*\n", box):
            for one in re.findall(r"(?:isAssignable|isWideningReference)\(.*?\.(?=\s|$)",
                                  chunk, re.S):
                match = CLAUSE.match(" ".join(one.split()).replace(" .", "."))
                if not match:
                    continue
                body = (match.group("body") or "").strip()
                found.append({
                    "name": match.group("name"),
                    "head": f"{match.group('name')}({match.group('args')})",
                    "body": body,
                    "text": " ".join(one.split()),
                })
    if not found:
        sys.exit(f"JVMS {SPEC_SECTION} prints no isAssignable clauses in a shape this "
                 f"can read, so the page shape changed")
    return found


def unparsable(found: list[dict]) -> list[dict]:
    """Clauses whose body is not a comma separated list of goals.

    Prolog separates the goals of a clause body with commas. A body in which one goal
    ends and the next begins with nothing between them is not a clause, and the reason
    this looks is that the published SE25 text contains one.
    """
    bad = []
    for clause in found:
        if not clause["body"]:
            continue
        # Goals nest parentheses, so this walks rather than splits: at depth zero the only
        # thing allowed between the end of one goal and the start of the next is a comma.
        depth = 0
        body = clause["body"]
        for at, ch in enumerate(body):
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
                rest = body[at + 1:].lstrip()
                if depth == 0 and rest and not rest.startswith((",", ";", ".")):
                    bad.append({**clause, "at": rest.split()[0]})
                    break
    return bad


def versions(text: str, pattern: re.Pattern, path: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for line in text.splitlines():
        match = pattern.match(line.rstrip())
        if match:
            found[match.group("name")] = int(match.group("value"))
    if not found:
        sys.exit(f"{path} holds no class file version constants in a shape this can read")
    return found


def quoted(text: str, marker: str, path: str, lines: int) -> tuple[int, list[str]]:
    """A run of lines from a source file, with the line number the run starts at."""
    body = text.splitlines()
    for number, line in enumerate(body, start=1):
        if marker in line:
            return number, body[number - 1:number - 1 + lines]
    sys.exit(f"{path} no longer contains `{marker}`, so the file shape changed")


def check_the_index(sha: str) -> str:
    if not JVMS_INDEX.is_file():
        return "no index to check against"
    index = json.loads(JVMS_INDEX.read_text(encoding="utf-8"))
    for chapter in index.get("chapters", []):
        if chapter.get("chapter") == SPEC_CHAPTER:
            if chapter.get("sha256") == sha:
                return "matches the hash in docs/generated/jvms-index.json"
            sys.exit(
                f"JVMS chapter {SPEC_CHAPTER} hashes to {sha[:12]} and the index says "
                f"{str(chapter.get('sha256'))[:12]}. The chapter changed. Run "
                f"tools/gen_jvms_index.py, read the diff, then run this again."
            )
    return "the index does not carry this chapter"


def read(where: pathlib.Path, what: str) -> dict[str, dict]:
    files = sorted(where.glob("*.json"))
    if not files:
        sys.exit(f"no results in {where}, run {what}")
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def one_answer(data: dict[str, dict], field: str):
    """A field every environment must report identically, because it is a property of the
    JDK rather than of the machine."""
    answer = None
    for platform, found in sorted(data.items()):
        if answer is None:
            answer = found[field]
        elif found[field] != answer:
            sys.exit(f"{platform} reports a different {field} from another environment. "
                     f"The relation is a property of the JDK, so two answers under one "
                     f"pin means two JDKs.")
    return answer


def summed(data: dict[str, dict], field: str) -> dict[str, int]:
    """A count, summed over every environment, because each one scanned its own copy of
    the module and a mean would be a count of nothing."""
    total: dict[str, int] = {}
    for found in data.values():
        for key, value in found[field].items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def shape(cells: dict[str, int], order: list[str]) -> dict:
    """The three structural properties, recomputed here from the raw cells.

    The probe computes these in Java over a map it built as it went. This computes them
    again from the cells as they were parsed back out of a results file, which shares no
    code with the first reader, and `verify` refuses a page where the two differ.
    """
    yes = {name for name, got in cells.items() if got == 1}
    broken = [f"{a}->{b}->{c}"
              for a in order for b in order for c in order
              if f"{a}->{b}" in yes and f"{b}->{c}" in yes and f"{a}->{c}" not in yes]
    mutual = [f"{a} and {b}"
              for i, a in enumerate(order) for b in order[i + 1:]
              if f"{a}->{b}" in yes and f"{b}->{a}" in yes]
    return {
        "reflexive": sum(1 for one in order if f"{one}->{one}" in yes),
        "nontransitive": len(broken),
        "mutual": len(mutual),
        "triples": broken,
        "pairs": mutual,
        "assignable": len(yes),
    }


def classes(pairs: list[str], order: list[str]) -> list[list[str]]:
    """The mutually assignable pairs, closed into groups.

    Two types that are each assignable to the other are the same type as far as the
    verifier is concerned, and a list of pairs hides how large the largest such group is.
    """
    groups: list[set[str]] = []
    for pair in pairs:
        a, b = pair.split(" and ")
        joined = {a, b}
        rest = []
        for group in groups:
            if group & joined:
                joined |= group
            else:
                rest.append(group)
        groups = rest + [joined]
    return [sorted(group, key=order.index) for group in
            sorted(groups, key=lambda g: (-len(g), sorted(g)))]


def predict(major: int, constants: dict[str, int]) -> str:
    """What the two version constants say should happen to a class with a wrong frame.

    The measurement is five class files at five class file versions. The constants are
    two numbers in two HotSpot files. Neither is evidence on its own and the pair is.
    """
    stackmap = constants["STACKMAP_ATTRIBUTE_MAJOR_VERSION"]
    nofailover = constants["NOFAILOVER_MAJOR_VERSION"]
    if major < stackmap:
        return "loads"          # the old verifier runs and never reads the attribute
    if major < nofailover:
        return "loads"          # the new verifier refuses and the old one is asked again
    return "refused"            # the new verifier refuses and that is the answer


def verify(terms: list[str], found: list[dict], order: list[str], cells: dict[str, int],
           told: dict[str, int], got: dict, hole: dict, failover: dict,
           constants: dict[str, int], ops: dict[str, int], data: dict) -> list[str]:
    """Every check that has to pass before a page is worth writing.

    Each failure is one of the four sources having moved without the others. The right
    answer to that is a person reading a diff rather than a generator picking a winner,
    so these exit rather than warn.
    """
    notes: list[str] = []

    # The measured grid, against the specification's list of type terms. Every type the
    # probe put in a class file has to be a term the hierarchy draws, or a class(_, _) or
    # arrayOf(_) instance, and a type the probe measures and this cannot place is a grid
    # measuring something other than the relation the section defines.
    for name in order:
        if name not in MEANS:
            sys.exit(f"the probe measured a type called {name} and this generator has no "
                     f"description for it")
        if name not in terms and not (name[0].isupper() or name.endswith("[]")):
            sys.exit(f"the probe measured `{name}`, which JVMS {SPEC_SECTION} draws as no "
                     f"atom and which is not the name of a class or an array either, so "
                     f"the grid and the section are about different things")
    # Every atom the grid measures has to be one a class file can name. Measuring
    # `oneWord` is not possible and a grid claiming to have done it is a broken harness.
    unwritable = [one for one in order if one in terms and not WRITABLE[one]]
    if unwritable:
        sys.exit(f"the grid claims to have measured {unwritable}, and no "
                 f"verification_type_info can name {'them' if len(unwritable) > 1 else 'it'}")

    # The three structural properties, computed twice over the same 256 cells by two
    # readers that share no code. A claim about the shape of a relation is worth nothing
    # if the two of them disagree.
    for name in ("reflexive", "nontransitive", "mutual"):
        if got[name] != told.get(name):
            sys.exit(f"the probe says {name} is {told.get(name)} and a recount of the "
                     f"same cells here says {got[name]}, so one of the two is wrong")
    if got["assignable"] != one_answer(data, "grid")["assignable"]:
        sys.exit(f"the grid header says {one_answer(data, 'grid')['assignable']} "
                 f"assignable cells and the cells come to {got['assignable']}")

    # Every ordered pair answered, because a missing cell is a hole in a table that is
    # read as a complete relation.
    for a in order:
        for b in order:
            if cells.get(f"{a}->{b}") not in (0, 1):
                sys.exit(f"the grid has no answer for {a} to {b}")

    # Four rows the implementation states outright, checked against what was measured.
    # `verificationType.cpp` returns true for null to any reference before it looks at
    # anything else, returns true for any reference to java.lang.Object, and allows an
    # array to reach only two of the interfaces. If the measurement disagrees with the
    # source then the probe is not measuring the function this page is about.
    # The concrete reference types are the ones the hierarchy does not draw as atoms,
    # because a `class(N, L)` and an `arrayOf(X)` are shapes rather than terms. Deriving
    # the set this way rather than listing it means a type added to the probe joins these
    # checks without being added here twice.
    concrete = [one for one in order if one not in terms]
    if len(concrete) < 3:
        sys.exit(f"the grid holds {len(concrete)} concrete reference types, which is not "
                 f"enough to check the implementation's three outright claims against")
    for name in concrete:
        if cells.get(f"null->{name}") != 1:
            sys.exit(f"{TYPE_CPP} returns true for null to any reference and the grid "
                     f"says null is not assignable to {name}")
        if cells.get(f"{name}->Object") != 1:
            sys.exit(f"{TYPE_CPP} returns true for any reference to java.lang.Object and "
                     f"the grid says {name} is not assignable to Object")
    for name in [one for one in concrete if one.endswith("[]")]:
        for target, want in (("Cloneable", 1), ("Serializable", 1), ("Runnable", 0)):
            if cells.get(f"{name}->{target}") != want:
                sys.exit(f"{TYPE_CPP} allows an array to reach Cloneable and Serializable "
                         f"and no other interface, and the grid says {name} to {target} "
                         f"is {cells.get(f'{name}->{target}')}")

    # The specification's clauses. Four `isWideningReference` clauses carry every
    # interesting case in the relation and a section with none of them is a section that
    # was rewritten.
    widening = [one for one in found if one["name"] == "isWideningReference"]
    if not widening:
        sys.exit(f"JVMS {SPEC_SECTION} prints no isWideningReference clauses, so the "
                 f"section was rewritten and this page describes the old one")

    # The failover, against the two version constants. The measurement is five class
    # files and the constants are two numbers, and each is only worth reporting because
    # the other agrees with it.
    for major, seen in sorted(failover.items(), key=lambda kv: int(kv[0])):
        if seen.get("right_loads") != 1:
            sys.exit(f"a class with a correct frame did not load at major version "
                     f"{major}, so the failover measurement is measuring the wrong thing")
        wanted = predict(int(major), constants)
        measured = "loads" if seen.get("wrong_loads") else "refused"
        if measured != wanted:
            sys.exit(
                f"a class with a wrong frame at major version {major} {measured}, and "
                f"STACKMAP_ATTRIBUTE_MAJOR_VERSION="
                f"{constants['STACKMAP_ATTRIBUTE_MAJOR_VERSION']} with "
                f"NOFAILOVER_MAJOR_VERSION={constants['NOFAILOVER_MAJOR_VERSION']} says "
                f"it should have been {wanted}"
            )

    # The hole. Three facts, and the page's headline claim needs all three.
    if hole.get("verifies") != 1 or hole.get("call_verifies") != 1:
        sys.exit("the class that puts a String in an interface local no longer verifies")
    if "IncompatibleClassChangeError" not in str(hole.get("throws", "")):
        sys.exit(f"the interface hole threw {hole.get('throws')} rather than the error "
                 f"the interface dispatch check raises")
    if hole.get("control_verifies") != 0:
        sys.exit("the control class verified, so the hole is not about interfaces")

    for name in ops:
        if name not in COSTS:
            sys.exit(f"the census counts {name} and this generator does not say what "
                     f"check it carries")

    notes.append(f"{len(data)} environment{'s' if len(data) != 1 else ''} measured the "
                 f"same {len(cells)} cells and agreed on every one")
    return notes


def grid_table(order: list[str], cells: dict[str, int]) -> list[str]:
    """The whole relation, as one table.

    Sixteen type names across the top is wider than a terminal, so the columns are
    numbered and the numbers are the rows. A reader looks up a row and counts along.
    """
    head = "| from \\ to | " + " | ".join(str(n) for n in range(1, len(order) + 1)) + " |"
    rule = "| --- | " + " | ".join("---" for _ in order) + " |"
    rows = [head, rule]
    for number, a in enumerate(order, start=1):
        marks = ["y" if cells.get(f"{a}->{b}") == 1 else "." for b in order]
        rows.append(f"| {number}. `{a}` | " + " | ".join(marks) + " |")
    return rows


def build(pinned: dict, terms: list[str], found: list[dict], bad: list[dict],
          order: list[str], kinds: dict[str, str], cells: dict[str, int], got: dict,
          groups: list[list[str]], shapes: dict[str, int], worked: dict[str, int],
          hole: dict, failover: dict, constants: dict[str, int], where: dict,
          census: dict, ops: dict[str, int], notes: list[str],
          hashes: dict[str, str]) -> str:
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]
    total = len(order) * len(order)
    out: list[str] = []
    add = out.append

    add("# BP-VERIFY section 2. The verifier's type system")
    add("")
    add(f"<!-- generated: tools/gen_verify.py from JVMS {SPEC_SECTION}@{edition}, "
        f"`{TYPE_CPP}`, `{VERIFIER_HPP}` and `{VERIFIER_CPP}` at `{tag}`, and "
        f"{RESULTS} -->")
    add("")
    add(f"JVMS {SPEC_SECTION}@{edition} defines which verification type may stand in for "
        f"which other, and it defines it as {word(len(found))} Prolog clauses over "
        f"{word(len(terms))} type terms. This page does not paraphrase them. It reports "
        f"one class file per ordered pair of {word(len(order))} types, built and handed "
        f"to the pinned JVM, and the relation is which of the {total} loaded.")
    add("")

    add("## The types the grid measures")
    add("")
    add("| # | type | what it is | in the hierarchy |")
    add("| --- | --- | --- | --- |")
    for number, name in enumerate(order, start=1):
        drawn = "an atom the diagram draws" if name in terms else "a `class(N, L)`" \
            if not name.endswith("[]") else "an `arrayOf(X)`"
        add(f"| {number} | `{name}` | {MEANS[name]} | {drawn} |")
    add("")
    atoms = sum(1 for one in terms if WRITABLE[one])
    add(f"The hierarchy draws {word(len(terms))} atoms and puts the two reference shapes, "
        f"{' and '.join('`' + one + '`' for one in BOXED)}, in a box rather than naming "
        f"them, which is {word(len(terms) + len(BOXED))} type terms in all. "
        f"{word(atoms + len(BOXED)).capitalize()} of them can be written into a class "
        f"file. `oneWord`, `twoWord`, `reference` and the abstract `uninitialized` exist "
        f"to make the rules shorter, and no verification_type_info can name any of the "
        f"four, which is why the grid measures the ones it does.")
    add("")

    add("## The relation")
    add("")
    add("`y` means a class file that produced the row's type and declared the column's "
        "type in a stack map frame was loaded. `.` means it was refused with "
        "`VerifyError`.")
    add("")
    out.extend(grid_table(order, cells))
    add("")
    add(f"{got['assignable']} of {total} cells are assignable. Every one of the "
        f"{total - got['assignable']} refusals came back in "
        f"{word(len(shapes))} sentence shape:")
    add("")
    for text, count in sorted(shapes.items(), key=lambda kv: -kv[1]):
        add(f"- {count:,} times: `{text}`")
    add("")

    add("## What kind of relation this is")
    add("")
    add(f"Everybody who writes about a type hierarchy assumes an order, and an order is "
        f"reflexive, transitive and antisymmetric. This one is reflexive on all "
        f"{word(got['reflexive'])} types and it is neither of the other two.")
    add("")
    add(f"It is not transitive. {word(got['nontransitive']).capitalize()} triples have "
        f"`a` assignable to `b` and `b` assignable to `c` and `a` not assignable to `c`:")
    add("")
    for triple in got["triples"]:
        a, b, c = triple.split("->")
        add(f"- `{a}` to `{b}` to `{c}`")
    add("")
    add(f"It is not antisymmetric. {word(got['mutual']).capitalize()} unordered pairs are "
        f"assignable both ways, which closes into "
        f"{word(len(groups))} group{'s' if len(groups) != 1 else ''} of types the "
        f"verifier cannot tell apart:")
    add("")
    for group in groups:
        add(f"- {', '.join('`' + one + '`' for one in group)}")
    add("")
    add("So it is a preorder. The largest group has "
        f"{word(len(groups[0]) if groups else 0)} members and it contains "
        f"`java.lang.Object`, which is the finding: to the verifier's type system, "
        f"`Object` and an interface are the same type.")
    add("")

    add("## Where the implementation says the same thing")
    add("")
    number, lines = where["interfaces"]
    add(f"{TYPE_CPP}:{number}@{tag} decides every reference to reference cell in the "
        f"grid, and it says in a comment what the grid measures:")
    add("")
    add("```c++")
    out.extend(lines)
    add("```")
    add("")
    add(f"The last clause is the array rows. An array reaches `java.lang.Cloneable` and "
        f"`java.io.Serializable` because they are named, and no other interface, which "
        f"is why every non transitive triple in this grid ends at an interface an array "
        f"cannot reach.")
    add("")

    add("## The clauses this is a measurement of")
    add("")
    widening = [one for one in found if one["name"] == "isWideningReference"]
    add(f"JVMS {SPEC_SECTION}@{edition} gives the relation as {word(len(found))} clauses. "
        f"{word(len(widening)).capitalize()} of them are `isWideningReference`, and those "
        f"carry every case the grid finds interesting:")
    add("")
    add("```prolog")
    for clause in found:
        if clause["name"] == "isWideningReference":
            add(clause["text"])
    add("```")
    add("")
    if bad:
        add(f"{word(len(bad)).capitalize()} of those clauses does not parse. Prolog "
            f"separates the "
            f"goals of a clause body with commas, and this one has none between the "
            f"parenthesised disjunction and the goal after it, which is where the text "
            f"reads `{bad[0]['at']}`. A Prolog system asked to consult "
            f"{SPEC_SECTION}@{edition} as written rejects it. Nothing depends on the "
            f"text being executable, which is how a defect survives in a normative "
            f"document: nobody has ever run it.")
        add("")

    add("## Two verifiers, and which one answers")
    add("")
    add(f"A class whose stack map frame is wrong is not always refused. "
        f"{VERIFIER_HPP}:{where['stackmap'][0]}@{tag} and "
        f"{VERIFIER_CPP}:{where['nofailover'][0]}@{tag} hold the two numbers that "
        f"decide it:")
    add("")
    for name, value in sorted(constants.items(), key=lambda kv: kv[1]):
        add(f"- `{name}` is {value}")
    add("")
    add("| major | wrong frame | correct frame | which verifier answered |")
    add("| --- | --- | --- | --- |")
    for major in sorted(failover, key=int):
        seen = failover[major]
        wrong = "loads" if seen.get("wrong_loads") else f"`{seen.get('rejected_by')}`"
        right = "loads" if seen.get("right_loads") else "refused"
        low = constants["STACKMAP_ATTRIBUTE_MAJOR_VERSION"]
        high = constants["NOFAILOVER_MAJOR_VERSION"]
        which = ("the old one, which never reads the attribute" if int(major) < low
                 else "the new one, then the old one" if int(major) < high
                 else "the new one, and that is final")
        add(f"| {major} | {wrong} | {right} | {which} |")
    add("")
    add(f"The two rows that load are the two rows where nothing is reported to the "
        f"program. A class file at major {constants['STACKMAP_ATTRIBUTE_MAJOR_VERSION']} "
        f"with a frame the new verifier refuses is verified again by the old one and "
        f"loads, and the only way to see that happen is `-Xlog:verification=info`.")
    add("")

    add("## What the accepted half costs")
    add("")
    add(f"A class that puts a `java.lang.String` in a local its stack map frame declares "
        f"as `java.lang.Runnable` verifies. So does the same class with an "
        f"`invokeinterface` of `Runnable.run()` on that local. Running it throws:")
    add("")
    add("```")
    add(str(hole.get("throws")))
    add("```")
    add("")
    add(f"The control is the same class with `java.lang.Thread`, a class rather than an "
        f"interface, where `Runnable` was. It is refused:")
    add("")
    add("```")
    add(str(hole.get("control_rejected_by")))
    add("```")
    add("")

    add("## How much of a module rests on that")
    add("")
    add(f"Every instruction below carries a check the verifier declined to make. Counted "
        f"over `{census['module']}` in {word(census['environments'])} "
        f"environments, {census['classes']:,} classes and "
        f"{census['methods_with_code']:,} methods with code, "
        f"{census['instructions']:,} instructions in total.")
    add("")
    add("| instruction | count | what is checked at run time | what it throws |")
    add("| --- | --- | --- | --- |")
    for name, count in sorted(ops.items(), key=lambda kv: -kv[1]):
        checked, throws = COSTS[name]
        add(f"| `{name.lower()}` | {count:,} | {checked} | {throws} |")
    add("")
    add(f"`invokeinterface` is the one this page is about: {ops['INVOKEINTERFACE']:,} "
        f"sites where the verifier let a reference through on the strength of a type it "
        f"cannot distinguish from `Object`, each one resting on a check made again when "
        f"the call runs.")
    add("")

    add("## Cells worked out by hand")
    add("")
    add(f"The grid is {total} answers from a machine, and nothing in it would notice if "
        f"the harness built the same class {total} times. These "
        f"{word(len([one for one in worked if one != 'wrong']))} are the ones a reader of "
        f"JVMS {SPEC_SECTION} can settle on paper. The probe writes no results file when "
        f"a measured answer differs from the worked one.")
    add("")
    add("| pair | worked on paper | measured | agrees |")
    add("| --- | --- | --- | --- |")
    for name in sorted([one for one in worked if one != "wrong"],
                       key=lambda one: (order.index(one.split("->")[0]),
                                        order.index(one.split("->")[1]))):
        a, b = name.split("->")
        measured = "assignable" if cells.get(name) == 1 else "refused"
        # The worked answer is not recorded on its own, because the only thing the probe
        # keeps is whether the two matched. Where they matched it is the measured one, and
        # where they did not it is the other, which is the row a reader needs to see.
        paper = measured if worked[name] else (
            "refused" if measured == "assignable" else "assignable")
        add(f"| `{a}` to `{b}` | {paper} | {measured} | "
            f"{'yes' if worked[name] else 'no'} |")
    add("")

    add("## What this was read from")
    add("")
    for what, sha in hashes.items():
        add(f"- {what}: `{sha[:16]}`")
    add("")
    for note in notes:
        add(f"- {note}")
    add("")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed page is stale")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the summary without writing")
    args = ap.parse_args(argv)

    pinned = pin()
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]

    type_cpp, type_sha = source(tag, TYPE_CPP)
    verifier_hpp, hpp_sha = source(tag, VERIFIER_HPP)
    verifier_cpp, cpp_sha = source(tag, VERIFIER_CPP)
    constants = versions(verifier_hpp, ENUM_VERSION, VERIFIER_HPP)
    constants.update(versions(verifier_cpp, DEFINE_VERSION, VERIFIER_CPP))
    for name in ("STACKMAP_ATTRIBUTE_MAJOR_VERSION", "NOFAILOVER_MAJOR_VERSION"):
        if name not in constants:
            sys.exit(f"neither {VERIFIER_HPP} nor {VERIFIER_CPP} defines {name} any more")
    constants = {name: constants[name] for name in
                 ("STACKMAP_ATTRIBUTE_MAJOR_VERSION", "NOFAILOVER_MAJOR_VERSION")}

    where = {
        "interfaces": quoted(type_cpp, "if (is_intf &&", TYPE_CPP, 10),
        "stackmap": quoted(verifier_hpp, "STACKMAP_ATTRIBUTE_MAJOR_VERSION",
                           VERIFIER_HPP, 1),
        "nofailover": quoted(verifier_cpp, "#define NOFAILOVER_MAJOR_VERSION",
                             VERIFIER_CPP, 1),
    }
    url = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    raw = fetch(url)
    page = raw.decode("utf-8", errors="replace")
    spec_sha = hashlib.sha256(raw).hexdigest()
    index_says = check_the_index(spec_sha)
    only = section_of(page)
    boxes = listings(only)
    terms = hierarchy(boxes)
    found = clauses(boxes)
    bad = unparsable(found)

    data = read(RESULTS, "probes/verify/run.py")
    order = one_answer(data, "order")
    kinds = one_answer(data, "types")
    cells = one_answer(data, "cells")
    told = one_answer(data, "properties")
    shapes = one_answer(data, "refusal_shapes")
    worked = one_answer(data, "worked_by_hand")
    hole = one_answer(data, "hole")
    failover = one_answer(data, "failover")
    ops = summed(data, "ops")
    census = summed(data, "census")
    # The module is not the same file on every platform, so the counts sum and the name
    # of what was counted has to agree.
    modules = {found["census"]["module"] for found in data.values()}
    if len(modules) != 1:
        sys.exit(f"the environments counted different modules: {sorted(modules)}")
    census["module"] = modules.pop()
    census["environments"] = len(data)

    got = shape(cells, order)
    groups = classes(got["pairs"], order)
    notes = verify(terms, found, order, cells, told, got, hole, failover, constants,
                   ops, data)

    hashes = {
        f"JVMS {edition} chapter {SPEC_CHAPTER}, which {index_says}": spec_sha,
        f"`{TYPE_CPP}` at `{tag}`": type_sha,
        f"`{VERIFIER_HPP}` at `{tag}`": hpp_sha,
        f"`{VERIFIER_CPP}` at `{tag}`": cpp_sha,
    }

    out = build(pinned, terms, found, bad, order, kinds, cells, got, groups, shapes,
                worked, hole, failover, constants, where, census, ops, notes, hashes)

    if args.show:
        print(f"{len(order)} types, {len(cells)} cells, {got['assignable']} assignable")
        print(f"reflexive on {got['reflexive']}, {got['nontransitive']} non transitive "
              f"triples, {got['mutual']} mutually assignable pairs")
        print(f"largest group of types the verifier cannot tell apart: "
              f"{', '.join(groups[0]) if groups else 'none'}")
        print(f"{len(found)} clauses in JVMS {SPEC_SECTION}, {len(bad)} of which do not "
              f"parse")
        return 0

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_verify.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != out:
            print(f"{OUTPUT} does not match its sources, run tools/gen_verify.py",
                  file=sys.stderr)
            return 1
        print(f"{OUTPUT} matches all four sources")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
