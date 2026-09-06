#!/usr/bin/env python3
"""Generate the constant pool section from four sources that check each other.

M2's exit criterion says BP-CONSTPOOL section 2 is generated rather than written. Section
2 is the pool itself: the seventeen entry kinds, which of them `ldc` may load, what the
tag byte means once a class is in memory, and what the several hundred thousand strings
in a real pool are actually for.

Four sources, and the point of having four is that they disagree in interesting places:

  the specification    JVMS chapter 4 is normative. Table 4.4-B gives every tag with the
                       class file version it appeared in, table 4.4-C gives the loadable
                       ones with the version each became loadable at, and the chapter
                       prints a structure for each kind, several of which are shared by
                       more than one kind.

  the implementation   `classfile_constants.h.template` is the header the JDK ships to
                       anybody writing a native agent, and it is where the tag numbers
                       and the method handle reference kinds are numbers rather than
                       prose. `constantTag.hpp` and `constantTag.cpp` are the same byte
                       as HotSpot holds it at run time, which is a wider thing than the
                       format allows: eight of its values can never occur in a file.

  the platform library `java.lang.classfile` knows the tag, the slot width and the
                       loadability of every kind, including the two kinds that do not
                       occur anywhere in `java.base`, which is why the probe builds one
                       entry of each rather than looking for one.

  the counts           `probes/constpool/results` is what a real pool is made of, and it
                       is the part no document has. `probes/classfile-census/results`
                       counted the same tags separately, so the two probes are checked
                       against each other here rather than trusted.

  python tools/gen_constpool.py            write docs/generated/constpool.md
  python tools/gen_constpool.py --check    fail if the committed page is stale
  python tools/gen_constpool.py --print    the summary, without writing anything

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
RESULTS = pathlib.Path("probes/constpool/results")
CENSUS = pathlib.Path("probes/classfile-census/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OUTPUT = pathlib.Path("docs/generated/constpool.md")

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
HEADER_PATH = "src/java.base/share/native/include/classfile_constants.h.template"
TAG_HPP = "src/hotspot/share/utilities/constantTag.hpp"
TAG_CPP = "src/hotspot/share/utilities/constantTag.cpp"
SPEC_PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"
SPEC_CHAPTER = "4"

# The same table and structure parsing as tools/gen_classfile.py, kept here rather than
# imported, because a generator that can be read top to bottom is worth more than four
# saved regexes and because these two read different tables off the same page.
TABLE_TITLE = re.compile(
    r"<b>Table&nbsp;(?P<id>[\d.]+-[A-Z])(?: \(cont\.\))?\.&nbsp;(?P<title>.*?)</b>")
TABLE_BODY = re.compile(r"<tbody>(?P<body>.*?)</tbody>", re.S)
ROW = re.compile(r"<tr>(?P<row>.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.S)
STRUCT = re.compile(
    r'<pre class="screen">\s*(?P<name>\w+)\s*\{(?P<body>.*?)\}\s*</pre>', re.S)
FIELD = re.compile(r"^(?P<type>\w+)\s+(?P<name>\w+)(?:\[(?P<count>[^\]]*)\])?;$")

# `JVM_CONSTANT_Utf8 = 1,` and `JVM_REF_getField = 1,` in the header, and the same shape
# with a trailing comment in HotSpot's own enum.
CONSTANT = re.compile(
    r"^\s*(?P<name>JVM_(?:CONSTANT|REF)_\w+)\s*=\s*(?P<value>\d+)\s*,?"
    r"\s*(?:/[/*]\s*(?P<note>[^*]*?)\s*(?:\*/)?)?\s*$")

# `bool is_loadable_constant() const {` and the three shapes its body may be made of.
PREDICATE = re.compile(r"\bbool\s+(?P<name>\w+)\s*\(\)\s*const\s*\{")
IS_EQ = re.compile(r"^_tag\s*==\s*(JVM_CONSTANT_\w+)$")
IS_RANGE = re.compile(
    r"^_tag\s*>=\s*(JVM_CONSTANT_\w+)\s*&&\s*_tag\s*<=\s*(JVM_CONSTANT_\w+)$")
IS_CALL = re.compile(r"^(\w+)\(\)$")

CASE = re.compile(r"case\s+(JVM_CONSTANT_\w+)\s*:")
RETURNS_STRING = re.compile(r'return\s+"(?P<value>[^"]*)"\s*;')
RETURNS_TAG = re.compile(r"return\s+(?P<value>JVM_CONSTANT_\w+)\s*;")
RETURNS_TYPE = re.compile(r"return\s+(?P<value>T_\w+)\s*;")

WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
         13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
         17: "seventeen"}

# Kinds the specification does not print a structure for, and the structure it prints
# instead. This is the one join on this page that is written by hand, because the fact it
# encodes is a typesetting decision: three member reference kinds are one structure with
# three tags, and the page shows it once. The generator refuses if a name here stops
# resolving, so a rewritten chapter is an error rather than a missing row.
SHARED = {
    "CONSTANT_Methodref": "CONSTANT_Fieldref",
    "CONSTANT_InterfaceMethodref": "CONSTANT_Fieldref",
    "CONSTANT_Float": "CONSTANT_Integer",
    "CONSTANT_Double": "CONSTANT_Long",
    "CONSTANT_InvokeDynamic": "CONSTANT_Dynamic",
}

# What JVMS 4.4.8 requires each method handle reference kind to point at, as the class
# file API's own type names so the requirement and the measurement can be compared
# without a translation table. The interface variants of kinds 6 and 7 arrived in class
# file version 52 and the specification states them as a version condition, so this is
# the union over versions and the paragraph below the table says which ones occurred.
REFERENCE = {
    1: ({"FieldRefEntry"}, "reads an instance field"),
    2: ({"FieldRefEntry"}, "reads a static field"),
    3: ({"FieldRefEntry"}, "writes an instance field"),
    4: ({"FieldRefEntry"}, "writes a static field"),
    5: ({"MethodRefEntry"}, "calls a virtual method"),
    6: ({"MethodRefEntry", "InterfaceMethodRefEntry"}, "calls a static method"),
    7: ({"MethodRefEntry", "InterfaceMethodRefEntry"},
        "calls a private or a superclass method"),
    8: ({"MethodRefEntry"}, "calls a constructor"),
    9: ({"InterfaceMethodRefEntry"}, "calls an interface method"),
}

# What each Utf8 role is, in the words a reader of a hex dump would use. A role the probe
# reports and this does not describe stops the generator, because a string nobody can
# name is the whole question this section exists to answer.
ROLE = {
    "annotation_class": "a class named by an annotation element",
    "annotation_element_name": "the name of an annotation element",
    "annotation_enum_name": "the constant of an enum valued annotation element",
    "annotation_enum_type": "the type of an enum valued annotation element",
    "annotation_string": "a string valued annotation element",
    "annotation_type": "the type of an annotation",
    "attribute_name": "the name of an attribute, anywhere in the file",
    "class_name": "the internal name inside a CONSTANT_Class",
    "field_descriptor": "the descriptor of a declared field",
    "field_name": "the name of a declared field",
    "inner_class_name": "the simple name of a nested class",
    "local_variable_descriptor": "the descriptor of a local variable",
    "local_variable_name": "the name of a local variable",
    "local_variable_signature": "the generic signature of a local variable",
    "method_descriptor": "the descriptor of a declared method",
    "method_name": "the name of a declared method",
    "method_type_descriptor": "the descriptor inside a CONSTANT_MethodType",
    "module_hash_algorithm": "the hash algorithm named by ModuleHashes",
    "module_name": "the name inside a CONSTANT_Module",
    "module_target": "the platform named by ModuleTarget",
    "module_version": "the version of the module",
    "name_and_type_descriptor": "the descriptor inside a CONSTANT_NameAndType",
    "name_and_type_name": "the name inside a CONSTANT_NameAndType",
    "package_name": "the name inside a CONSTANT_Package",
    "parameter_name": "the name of a method parameter",
    "record_component_descriptor": "the descriptor of a record component",
    "record_component_name": "the name of a record component",
    "signature": "a generic signature of a class, a field or a method",
    "source_file": "the source file this class was compiled from",
    "string_value": "the text of a string literal",
}

# What HotSpot pushes for a tag that `ldc` accepts, from `constantTag::basic_type`. The
# names on the left are HotSpot's, the words on the right are the ones a reader of the
# specification would use for the same thing.
TYPES = {
    "T_INT": "an `int`",
    "T_FLOAT": "a `float`",
    "T_LONG": "a `long`",
    "T_DOUBLE": "a `double`",
    "T_OBJECT": "a reference",
}

# The methods every class inherits, which is the set the interface receiver duplicates
# below all turn out to be drawn from.
OBJECT_METHODS = {"getClass", "toString", "equals", "hashCode", "clone", "notify",
                  "notifyAll", "wait", "finalize"}


def word(count: int) -> str:
    return WORDS.get(count, f"{count:,}")


def listed(names: list[str]) -> str:
    """`a`, `b` and `c`, which is what a sentence wants and what a join does not give."""
    marked = [f"`{name}`" for name in names]
    if len(marked) < 2:
        return "".join(marked)
    return ", ".join(marked[:-1]) + " and " + marked[-1]


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


def plain(markup: str) -> str:
    """One HTML cell as the text a reader would see."""
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([;,.])", r"\1", text)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))


def tables(text: str) -> dict[str, list[list[str]]]:
    """Every numbered table on the page, keyed by the number Oracle gave it."""
    found: dict[str, list[list[str]]] = {}
    for title in TABLE_TITLE.finditer(text):
        body = TABLE_BODY.search(text, title.end())
        if not body:
            continue
        rows = []
        for row in ROW.finditer(body.group("body")):
            cells = [plain(cell.group("cell")) for cell in CELL.finditer(row.group("row"))]
            if cells:
                rows.append(cells)
        found.setdefault(title.group("id"), []).extend(rows)
    for wanted in ("4.4-A", "4.4-B", "4.4-C"):
        if wanted not in found:
            sys.exit(f"JVMS chapter {SPEC_CHAPTER} no longer prints table {wanted}, so "
                     f"the page shape changed")
    return found


def structures(text: str) -> dict[str, list[dict[str, str]]]:
    """Every `Name { ... }` block on the page, as a list of items.

    One block can hold two structures, because the specification prints
    `CONSTANT_Long_info` and `CONSTANT_Double_info` in the same box. The items are cut at
    the second `tag`, which is where the second structure starts.
    """
    found: dict[str, list[dict[str, str]]] = {}
    for block in STRUCT.finditer(text):
        items: list[dict[str, str]] = []
        for line in html.unescape(block.group("body")).splitlines():
            line = re.sub(r"\s+", " ", plain(line)).strip()
            match = FIELD.match(line)
            if not match:
                continue
            if match.group("name") == "tag" and items:
                break
            items.append({"type": match.group("type"), "name": match.group("name"),
                          "count": match.group("count") or ""})
        if items:
            found.setdefault(block.group("name"), items)
    return found


def constants(text: str, path: str, wanted: str) -> dict[str, tuple[int, str]]:
    """`JVM_CONSTANT_*` and `JVM_REF_*`, with whatever the line said about each.

    `wanted` is a name the file has to have parsed, because a regex that silently
    matches nothing is the failure this generator is least likely to notice.
    """
    found: dict[str, tuple[int, str]] = {}
    for line in text.splitlines():
        match = CONSTANT.match(line)
        if match:
            found[match.group("name")] = (int(match.group("value")),
                                          (match.group("note") or "").strip())
    if wanted not in found:
        sys.exit(f"{path}: the enum holds no {wanted}, so the file shape changed")
    return found


def block(text: str, at: int) -> str:
    """The braced body that starts at or after `at`."""
    start = text.index("{", at)
    depth = 0
    for end in range(start, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:end]
    sys.exit("a brace never closed, so the file is not what this parser was written for")


def split_or(expr: str) -> list[str]:
    """One boolean expression into its top level `||` terms."""
    parts: list[str] = []
    depth = 0
    current = ""
    at = 0
    while at < len(expr):
        char = expr[at]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and expr.startswith("||", at):
            parts.append(current)
            current = ""
            at += 2
            continue
        current += char
        at += 1
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def bracketed(expr: str) -> bool:
    """Whether the whole expression is inside one pair of brackets.

    `(a || b)` is, and `(a) || (b)` is not, which is the case that makes this a scan
    rather than a look at the first and last character.
    """
    if not (expr.startswith("(") and expr.endswith(")")):
        return False
    depth = 0
    for at, char in enumerate(expr):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return at == len(expr) - 1
    return False


def evaluate(expr: str, bodies: dict[str, str], values: dict[str, int],
             seen: tuple[str, ...]) -> set[int] | None:
    """One boolean expression as the set of tag values it answers true for."""
    expr = expr.strip()
    while bracketed(expr):
        expr = expr[1:-1].strip()
    terms = split_or(expr)
    if len(terms) > 1:
        found: set[int] = set()
        for term in terms:
            inner = evaluate(term, bodies, values, seen)
            if inner is None:
                return None
            found |= inner
        return found
    one = IS_EQ.match(expr)
    if one and one.group(1) in values:
        return {values[one.group(1)]}
    span = IS_RANGE.match(expr)
    if span and span.group(1) in values and span.group(2) in values:
        low, high = values[span.group(1)], values[span.group(2)]
        return {value for value in values.values() if low <= value <= high}
    call = IS_CALL.match(expr)
    if call:
        return tags_of(call.group(1), bodies, values, seen)
    return None


def tags_of(name: str, bodies: dict[str, str], values: dict[str, int],
            seen: tuple[str, ...] = ()) -> set[int] | None:
    """Which tag values one `constantTag` predicate answers true for.

    Three shapes are understood, which is every shape the file uses: a comparison against
    one tag, a range between two tags, and a call to another predicate. Anything else
    returns nothing at all rather than a wrong answer, and the caller decides whether the
    predicate it wanted was one of those.
    """
    if name in seen or name not in bodies:
        return None
    expr = " ".join(bodies[name].split())
    match = re.match(r"^return (?P<expr>.*);$", expr)
    if not match:
        return None
    return evaluate(match.group("expr"), bodies, values, seen + (name,))


def predicates(text: str, values: dict[str, int]) -> dict[str, set[int]]:
    """Every `bool is_x() const` in the header, as the set of tags it accepts."""
    bodies: dict[str, str] = {}
    for found in PREDICATE.finditer(text):
        bodies[found.group("name")] = block(text, found.end() - 1)
    answers: dict[str, set[int]] = {}
    for name in bodies:
        tags = tags_of(name, bodies, values)
        if tags is not None:
            answers[name] = tags
    for wanted in ("is_loadable_constant", "is_in_error", "has_bootstrap",
                   "is_field_or_method"):
        if wanted not in answers:
            sys.exit(f"{TAG_HPP} no longer defines {wanted} in a shape this can read")
    return answers


def switch(text: str, signature: str, returns: re.Pattern) -> dict[str, str]:
    """One `switch (_tag)` in the implementation, as tag name to whatever it returns.

    Cases pile up until a return, because the file writes several cases in a row against
    one answer. A case that reaches an assertion or the default instead of a return is
    dropped, which is how `JVM_CONSTANT_Dynamic` correctly comes back with no basic type.
    """
    at = text.find(signature)
    if at < 0:
        sys.exit(f"{TAG_CPP} no longer has `{signature}`, so the file shape changed")
    found: dict[str, str] = {}
    pending: list[str] = []
    for line in block(text, at).splitlines():
        if "assert(" in line or "ShouldNotReachHere" in line or "default" in line:
            pending = []
        for case in CASE.finditer(line):
            pending.append(case.group(1))
        answer = returns.search(line)
        if answer:
            for name in pending:
                found[name] = answer.group("value")
            pending = []
    if not found:
        sys.exit(f"{TAG_CPP}: the switch under `{signature}` parsed to nothing")
    return found


def check_the_index(sha: str) -> str:
    """Compare the chapter we just read against the one the index recorded."""
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


def one_answer(data: dict[str, dict], field: str) -> dict:
    """A field every environment must report identically, because it is a property of the
    JDK rather than of the machine."""
    answer = None
    for platform, found in sorted(data.items()):
        if answer is None:
            answer = found[field]
        elif found[field] != answer:
            sys.exit(f"{platform} reports a different {field} from another environment. "
                     f"Two JDKs, one pin.")
    return answer


def summed(data: dict[str, dict], field: str) -> dict:
    """A count, summed over every environment, for the same reason as the census next
    door: each one scanned its own copy of the module and a mean would be a count of
    nothing."""
    total: dict = {}
    for found in data.values():
        for key, value in found[field].items():
            if isinstance(value, dict):
                inner = total.setdefault(key, {})
                for name, count in value.items():
                    inner[name] = inner.get(name, 0) + count
            elif isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def share(count: int, total: int) -> str:
    if not total or not count:
        return ""
    value = 100 * count / total
    return f"{value:.2f}%" if value >= 0.01 else "<0.01%"


def section(edition: str, number: str, label: str | None = None) -> str:
    page = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    return f"[{label or ('§' + number)}]({page}#jvms-{number})"


def spec_tags(spec: dict[str, list[list[str]]], table: str) -> dict[int, list[str]]:
    """One of the two tag tables, as tag to the rest of its row."""
    found: dict[int, list[str]] = {}
    for row in spec[table]:
        if len(row) >= 2 and row[0].startswith("CONSTANT_") and row[1].isdigit():
            found[int(row[1])] = row
    if not found:
        sys.exit(f"JVMS table {table} parsed to no tags, so the page shape changed")
    return found


def verify(spec: dict, shapes: dict, header: dict, internal: dict, checks: dict,
           kinds: dict, data: dict, census: dict, tags: dict, roles: dict,
           handles: dict) -> list[str]:
    """Every check that has to pass before a page is worth writing.

    Each failure is one of the four sources having moved without the others. The right
    answer to that is a person reading a diff rather than a generator picking a winner,
    so these exit rather than warn.
    """
    notes: list[str] = []
    by_tag = spec_tags(spec, "4.4-B")
    loadable = spec_tags(spec, "4.4-C")

    # The specification's tags against the header the JDK ships.
    for number, row in sorted(by_tag.items()):
        key = "JVM_" + row[0]
        if key not in header:
            sys.exit(f"the JVMS has {row[0]} and {HEADER_PATH} has no {key}")
        if header[key][0] != number:
            sys.exit(f"the JVMS says {row[0]} is {number} and the header says "
                     f"{header[key][0]}")
    for name, (value, note) in sorted(header.items()):
        if not name.startswith("JVM_CONSTANT_") or name.endswith(("Min", "Max")):
            continue
        if value not in by_tag and not note:
            sys.exit(f"{HEADER_PATH} has {name} = {value}, the JVMS does not list that "
                     f"tag, and the header says nothing about why")

    # The class file API's answers against the specification's tags.
    if {entry["tag"] for entry in kinds.values()} != set(by_tag):
        sys.exit(f"the pinned JDK builds tags {sorted(e['tag'] for e in kinds.values())} "
                 f"and the JVMS lists {sorted(by_tag)}")
    for name, entry in sorted(kinds.items()):
        wide = by_tag[entry["tag"]][0] in ("CONSTANT_Long", "CONSTANT_Double")
        if entry["slots"] != (2 if wide else 1):
            sys.exit(f"{name} takes {entry['slots']} pool slots and "
                     f"{by_tag[entry['tag']][0]} should take {2 if wide else 1}")

    # Loadability, three ways. The specification's list, the API's marker interface, and
    # HotSpot's predicate with its internal tags taken out, because a tag no class file
    # may contain cannot be part of a claim about class files.
    api_loadable = {entry["tag"] for entry in kinds.values() if entry["loadable"]}
    if api_loadable != set(loadable):
        sys.exit(f"the JVMS says tags {sorted(loadable)} are loadable and this JDK's "
                 f"LoadableConstantEntry covers {sorted(api_loadable)}")
    hotspot_loadable = {tag for tag in checks["is_loadable_constant"]
                        if tag not in internal}
    if hotspot_loadable != set(loadable):
        sys.exit(f"the JVMS says tags {sorted(loadable)} are loadable and HotSpot's "
                 f"is_loadable_constant accepts {sorted(hotspot_loadable)}")

    # Two groupings that all three sources have a way of saying, which is the reason to
    # check them: a structure the specification prints once, a supertype in the API, and
    # a predicate in HotSpot.
    api_member = {entry["tag"] for entry in kinds.values() if entry["member_ref"]}
    if api_member != {tag for tag in checks["is_field_or_method"] if tag not in internal}:
        sys.exit(f"this JDK calls tags {sorted(api_member)} member references and "
                 f"HotSpot's is_field_or_method disagrees")
    api_dynamic = {entry["tag"] for entry in kinds.values() if entry["dynamic"]}
    if "has_bootstrap" in checks:
        bootstrap = {tag for tag in checks["has_bootstrap"] if tag not in internal}
        if api_dynamic != bootstrap:
            sys.exit(f"this JDK calls tags {sorted(api_dynamic)} dynamic and HotSpot's "
                     f"has_bootstrap accepts {sorted(bootstrap)}")

    # Every kind the page prints a structure for.
    for row in by_tag.values():
        if row[0] not in shapes and SHARED.get(row[0], "") + "_info" not in shapes:
            if row[0] + "_info" not in shapes:
                sys.exit(f"JVMS chapter {SPEC_CHAPTER} prints no structure for {row[0]} "
                         f"and this generator has no other one to use for it")

    # The counts, against the tags that exist and against the other probe.
    for tag, count in sorted(tags.items()):
        if int(tag) not in by_tag:
            sys.exit(f"tag {tag} occurs {count} times in the scanned code and the JVMS "
                     f"does not list it")
    for platform, found in sorted(data.items()):
        scan = found["scan"]
        total = scan["entries"] + scan["second_slots"] + scan["classes"]
        if total != scan["pool_count_sum"]:
            sys.exit(f"{platform} counted {scan['entries']:,} entries and "
                     f"{scan['second_slots']:,} second slots over {scan['classes']:,} "
                     f"classes, which is {total:,}, and the sum of constant_pool_count "
                     f"is {scan['pool_count_sum']:,}")
        other = census.get(platform)
        if other and other["tags"] != found["tags"]:
            sys.exit(f"probes/constpool and probes/classfile-census disagree about the "
                     f"tag counts on {platform}, which two programs walking the same "
                     f"module through the same API cannot both be right about")
    shared = sorted(set(data) & set(census))
    if not shared:
        notes.append(
            "There are no environments where both this probe and the class file census "
            "ran, so the tag counts below are checked against the specification and "
            "against nothing else."
        )

    for name in sorted(roles):
        if name not in ROLE:
            sys.exit(f"the probe reports a Utf8 role called {name} and this generator "
                     f"has no description for it")
    for kind, points in sorted(handles.items()):
        if int(kind) not in REFERENCE:
            sys.exit(f"a method handle in the scanned code has reference kind {kind}, "
                     f"which JVMS {section('', '4.4.8')} does not define")
        for target in points:
            if target not in REFERENCE[int(kind)][0]:
                sys.exit(f"a reference kind {kind} method handle points at a {target} "
                         f"and the specification does not allow that")
    return notes


def entry_shape(shapes: dict, name: str) -> list[dict[str, str]]:
    """The items after the tag byte, for one kind."""
    for key in (name + "_info", SHARED.get(name, "") + "_info"):
        if key in shapes:
            return [item for item in shapes[key] if item["name"] != "tag"]
    sys.exit(f"no structure on the page describes {name}")


def build(pinned: dict, spec: dict, shapes: dict, header: dict, internal: dict,
          named: dict, errors: dict, basic: dict, checks: dict, kinds: dict,
          data: dict, census: dict, tags: dict, roles: dict, handles: dict,
          dynamic: dict, duplicates: dict, notes: list[str],
          hashes: dict[str, str]) -> str:
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]
    by_tag = spec_tags(spec, "4.4-B")
    loadable = spec_tags(spec, "4.4-C")
    sections = {int(row[1]): row[2].replace("§", "")
                for row in spec["4.4-A"]
                if len(row) > 2 and row[0].startswith("CONSTANT_") and row[1].isdigit()}
    api_by_tag = {entry["tag"]: name for name, entry in kinds.items()}

    platforms = ", ".join(f"`{name}`" for name in sorted(data))
    builds = sorted({found["java_build"] for found in data.values()})
    measured = sorted({found["measured"] for found in data.values()})
    scanned = sorted({found["scan"]["module"] for found in data.values()})
    classes = sum(found["scan"]["classes"] for found in data.values())
    entries = sum(found["scan"]["entries"] for found in data.values())
    seconds = sum(found["scan"]["second_slots"] for found in data.values())
    counted = sum(found["scan"]["pool_count_sum"] for found in data.values())
    total = sum(tags.values())

    out: list[str] = []
    out.append("# BP-CONSTPOOL section 2. The constant pool")
    out.append("")
    out.append(
        f"Generated by `tools/gen_constpool.py`. Do not edit it, edit a source. There "
        f"are four: JVMS {edition} chapter 4 for what the pool is, `{HEADER_PATH}` at "
        f"`{tag}` for the tag numbers as the JDK writes them for native agents, "
        f"`{TAG_HPP}` with `{TAG_CPP}` at the same tag for what HotSpot puts in the tag "
        f"byte once a class is in memory, and `java.lang.classfile` read off the pinned "
        f"JDK for what the platform will build. The counts come from "
        f"`probes/constpool/results`."
    )
    out.append("")
    out.append(
        f"Counted over {', '.join(f'`{name}`' for name in scanned)} on {platforms}, java "
        f"{', '.join(builds)}, on {', '.join(measured)}: {classes:,} class files, "
        f"{entries:,} constant pool entries, {seconds:,} of the unusable second slots "
        f"that follow a `CONSTANT_Long` or a `CONSTANT_Double`."
    )
    out.append("")
    out.append(
        f"Every count on this page is a sum over those {word(len(data))} environments "
        f"rather than an average of them, because each one scanned its own copy of the "
        f"module and the copies are not byte for byte identical. A class that is in both "
        f"images is counted twice, on purpose: a sum is a count of things that were "
        f"actually seen and a mean would be a count of nothing."
    )
    out.append("")
    for note in notes:
        out.append(note)
        out.append("")
    if set(data) & set(census):
        out.append(
            f"The tag counts are also counted by `probes/classfile-census`, which is a "
            f"different program that walks the same module through the same API, and "
            f"this generator refuses to write a page where the two disagree. That is "
            f"worth having because a count is the one kind of claim here that no "
            f"document can check: on "
            + ", ".join(f"`{name}`" for name in sorted(set(data) & set(census)))
            + " the two probes agree entry for entry."
        )
        out.append("")

    out.append(f"## 2.1 The {word(len(by_tag))} kinds of entry")
    out.append("")
    out.append(
        f"Every entry is a one byte tag and then a payload whose shape the tag decides. "
        f"`u1`, `u2` and `u4` are unsigned big endian integers, an index is an index into "
        f"this same pool, and the pool is indexed from 1. The pool itself is "
        f"{section(edition, '4.4')}."
    )
    out.append("")
    out.append("| tag | kind | after the tag | slots | since | Java SE | API type | seen "
               "| share | JVMS |")
    out.append("|---:|---|---|---:|---:|---:|---|---:|---:|---|")
    for number, row in sorted(by_tag.items()):
        items = entry_shape(shapes, row[0])
        payload = ", ".join(
            f"`{item['type']} {item['name']}"
            + (f"[{item['count']}]" if item["count"] else "") + "`"
            for item in items) or "nothing"
        name = api_by_tag[number]
        count = tags.get(str(number), 0)
        where = sections.get(number, "")
        out.append(
            f"| {number} | `{row[0]}` | {payload} | {kinds[name]['slots']} | {row[2]} | "
            f"{row[3]} | `{name}` | {count:,} | {share(count, total)} | "
            f"{section(edition, where) if where else ''} |"
        )
    out.append("")
    grouped: dict[str, list[str]] = {}
    for number, row in sorted(by_tag.items()):
        for key in (row[0] + "_info", SHARED.get(row[0], "") + "_info"):
            if key in shapes:
                grouped.setdefault(key, []).append(row[0])
                break
    reused = {key: names for key, names in grouped.items() if len(names) > 1}
    out.append(
        f"The specification prints {word(len(grouped))} structures for those "
        f"{word(len(by_tag))} kinds, because "
        + "; ".join(listed(names) + " are one shape" for names in reused.values())
        + ". That is the first thing a reader of a hex dump has to hold on to: the tag "
        "is not a hint about the payload, it is the only thing that distinguishes two "
        "entries that are byte for byte the same length and shape."
    )
    out.append("")
    holes = sorted(set(range(1, max(by_tag) + 1)) - set(by_tag))
    named_holes = {value: (name, note) for name, (value, note) in header.items()
                   if name.startswith("JVM_CONSTANT_") and value in holes}
    left = [number for number in holes if number not in named_holes]
    out.append(
        f"The numbering has {word(len(holes))} holes in it, at "
        + ", ".join(str(number) for number in holes)
        + ". "
        + " ".join(
            f"Tag {number} is `{named_holes[number][0].removeprefix('JVM_')}` in "
            f"`{HEADER_PATH}`, marked {named_holes[number][1]}, which is the JDK "
            f"remembering a kind the format never shipped."
            for number in holes if number in named_holes)
        + (f" {'Tag' if len(left) == 1 else 'Tags'} "
           + " and ".join(str(number) for number in left)
           + f" {'was' if len(left) == 1 else 'were'} never assigned at all."
           if left else "")
        + " A parser that switches on the tag needs a default branch for them rather "
        "than an array indexed by tag, which is the mistake the holes exist to catch."
    )
    out.append("")
    absent = [row[0] for number, row in sorted(by_tag.items())
              if not tags.get(str(number), 0)]
    if absent:
        out.append(
            f"{word(len(absent)).capitalize()} of the {word(len(by_tag))} kinds "
            f"{'does' if len(absent) == 1 else 'do'} not occur anywhere in "
            f"the scanned code: "
            + ", ".join(f"`{name}`" for name in absent)
            + f". The row above is still measured rather than retyped, because the probe "
            f"builds one entry of every kind through `java.lang.classfile` and reads the "
            f"platform's own answer back. A table that could only describe the kinds that "
            f"happened to be in `{scanned[0]}` would be a table about "
            f"`{scanned[0]}`."
        )
        out.append("")

    out.append("## 2.2 Why constant_pool_count is one more than the number of entries")
    out.append("")
    largest = max(found["scan"]["largest_pool"] for found in data.values())
    largest_class = sorted(found["scan"]["largest_pool_class"] for found in data.values()
                           if found["scan"]["largest_pool"] == largest)[0]
    out.append(
        f"There is no entry at index 0, and a `CONSTANT_Long` or a `CONSTANT_Double` "
        f"takes two slots with nothing usable in the second one. Both of those are in "
        f"the count, and the arithmetic is checkable rather than quotable: over the "
        f"scanned code, {entries:,} entries plus {seconds:,} second slots plus "
        f"{classes:,} missing slot zeroes is {entries + seconds + classes:,}, and the "
        f"sum of every `constant_pool_count` field is {counted:,}. This generator "
        f"refuses to write the page if those two numbers differ."
    )
    out.append("")
    out.append(
        f"The largest pool in the scanned code has {largest:,} slots and belongs to "
        f"`{largest_class}`. The 16 bit count is a real limit rather than a theoretical "
        f"one: a class needs {65535 - largest:,} more entries than that one has before "
        f"it stops being expressible, and a generated class or a very large switch gets "
        f"there."
    )
    out.append("")

    out.append("## 2.3 Which of them `ldc` can load")
    out.append("")
    out.append(
        f"A loadable constant is one that `ldc`, `ldc_w` or `ldc2_w` can push onto the "
        f"stack, which is {word(len(loadable))} of the {word(len(by_tag))} kinds. The "
        f"specification lists them in table 4.4-C with a class file version against "
        f"each, the class file API marks them with a `LoadableConstantEntry` supertype, "
        f"and HotSpot has a predicate. All three agree about which kinds, and this "
        f"generator refuses to write the page if they stop agreeing."
    )
    out.append("")
    out.append("| tag | kind | loadable since | Java SE | what `ldc` pushes | seen |")
    out.append("|---:|---|---:|---:|---|---:|")
    for number, row in sorted(loadable.items()):
        internal_name = [key for key, value in header.items() if value[0] == number
                         and key.startswith("JVM_CONSTANT_")][0]
        pushes = basic.get(internal_name, "")
        out.append(
            f"| {number} | `{row[0]}` | {row[2]} | {row[3]} | "
            f"{TYPES.get(pushes, 'whatever the bootstrap method returns')} | "
            f"{tags.get(str(number), 0):,} |"
        )
    out.append("")
    out.append(
        f"The last column but one is HotSpot's, from `constantTag::basic_type`, which is "
        f"the function that decides what an `ldc` of an entry leaves on the stack. "
        f"`CONSTANT_Dynamic` is the one it will not answer for, because a dynamic "
        f"constant has whatever type its bootstrap method produced and there is nothing "
        f"in the entry to read it off."
    )
    out.append("")
    by_version: dict[str, list[str]] = {}
    for row in loadable.values():
        by_version.setdefault(row[2], []).append(row[0])
    order = sorted(by_version,
                   key=lambda text: tuple(int(part) for part in text.split(".")))
    out.append(
        f"The version column is the disagreement worth knowing about. The specification "
        f"makes loadability depend on the class file version, and the "
        f"{word(len(loadable))} kinds became loadable across "
        f"{word(len(by_version))} different versions: "
        + ", ".join(
            (listed(by_version[version]) if len(by_version[version]) <= 2
             else f"{word(len(by_version[version]))} of them")
            + f" at {version}"
            for version in order)
        + ". HotSpot's `is_loadable_constant` is a comparison against the tag byte and "
        "nothing else, so it answers the same way for a version 45 class file as for a "
        "version 71 one. The check that a class file older than version 49 may not `ldc` "
        "a `CONSTANT_Class` lives in the verifier and in the class file parser rather than "
        "here, which is worth knowing before reading either."
    )
    out.append("")
    not_loadable = [row[0] for number, row in sorted(by_tag.items())
                    if number not in loadable]
    out.append(
        "The other "
        + word(len(not_loadable))
        + " cannot be pushed at all: "
        + ", ".join(f"`{name}`" for name in not_loadable)
        + ". Some of them are structure rather than value, and `CONSTANT_InvokeDynamic` "
        "is the interesting exclusion, because it names a call site rather than a "
        "constant and `invokedynamic` is the only instruction that may refer to one."
    )
    out.append("")

    out.append("## 2.4 What HotSpot puts in the tag byte")
    out.append("")
    external = max(value for value, _ in header.values()
                   if value in by_tag)
    out.append(
        f"The tag byte in a class file has {word(len(by_tag))} legal values. The tag "
        f"byte in memory has more, because HotSpot reuses the field to record where the "
        f"entry is in its own life cycle. A class is parsed with its class references "
        f"unresolved, they become resolved as the code that needs them runs, and a "
        f"resolution that threw keeps the exception rather than being retried. All three "
        f"of those states are the same byte."
    )
    out.append("")
    out.append("| value | name | what HotSpot calls it | what it is |")
    out.append("|---:|---|---|---|")
    for value, (name, note) in sorted(internal.items()):
        out.append(f"| {value} | `{name.removeprefix('JVM_CONSTANT_')}` | "
                   f"`{named.get(name, '')}` | {note or ''} |")
    out.append("")
    above = min(value for value in internal if value)
    out.append(
        f"Every one of those except `Invalid` is at {above} or above and none is past "
        f"{max(internal)}, and the format's own tags stop at {external}, so a value "
        f"HotSpot invented can never be mistaken for a tag a class file may contain. "
        f"`Invalid` is the value a freshly constructed tag has, which is how a partly "
        f"built pool is distinguishable from a finished one."
    )
    out.append("")
    if errors:
        out.append(
            f"{word(len(errors)).capitalize()} kinds can fail to resolve, and each one "
            f"has its own error tag rather than a shared one, so the failure that is "
            f"remembered is a failure of the right kind: "
            + ", ".join(
                f"`{was.removeprefix('JVM_CONSTANT_')}` becomes "
                f"`{name.removeprefix('JVM_CONSTANT_')}`"
                for was, name in sorted(errors.items()))
            + ". A second attempt to use the entry throws the same exception it threw "
            "the first time rather than running the resolution again, which is the "
            "behaviour the specification requires and the reason the tag has to carry "
            "the error at all."
        )
        out.append("")
    grouping = {name: found for name, found in checks.items() if len(found) > 1}
    if grouping:
        out.append(
            f"The predicates that accept more than one tag are the ones worth reading, "
            f"because each is a question the rest of the VM asks about an entry, and "
            f"together they are the vocabulary HotSpot has for talking about a pool."
        )
        out.append("")
        out.append("| predicate | accepts |")
        out.append("|---|---|")
        by_value = {value: name for name, (value, _) in header.items()
                    if name.startswith("JVM_CONSTANT_")}
        by_value.update({value: name for value, (name, _) in internal.items()})
        for name in sorted(grouping):
            accepted = ", ".join(
                f"`{by_value[value].removeprefix('JVM_CONSTANT_')}`"
                for value in sorted(grouping[name]) if value in by_value)
            out.append(f"| `{name}` | {accepted} |")
        out.append("")

    out.append("## 2.5 What all the strings are")
    out.append("")
    utf8 = tags.get("1", 0)
    uses = sum(roles.values())
    unaccounted = sum(len(found["scan"]["utf8_unaccounted"]) for found in data.values())
    out.append(
        f"`CONSTANT_Utf8` is {share(utf8, total)} of every entry in the scanned code, "
        f"which makes it the pool by volume, and the format gives no way at all to tell "
        f"one from another: a class name, a method descriptor, the text of a string "
        f"literal and the name of an attribute are the same structure. The only way to "
        f"know what a string is for is to ask what points at it. That is what the probe "
        f"does, and the table below is the answer."
    )
    out.append("")
    out.append("| what points at it | uses | share of uses |")
    out.append("|---|---:|---:|")
    for name, count in sorted(roles.items(), key=lambda item: (-item[1], item[0])):
        out.append(f"| {ROLE[name]} | {count:,} | {share(count, uses)} |")
    out.append("")
    out.append(
        f"Those are uses rather than entries. There are {uses:,} of them against "
        f"{utf8:,} `CONSTANT_Utf8` entries, because a pool holds one copy of each "
        f"distinct string and every use of it is an index. `V` is one entry in a class "
        f"with two hundred `void` methods."
    )
    out.append("")
    if unaccounted:
        out.append(
            f"{word(unaccounted).capitalize()} "
            f"{'string is' if unaccounted == 1 else 'strings are'} pointed at by nothing "
            f"the probe walks, which is a number to explain rather than a number to "
            f"trust: it walks the attributes this JDK can model and skips the ones whose "
            f"payload holds no text."
        )
    else:
        out.append(
            f"Nothing is left over. Every `CONSTANT_Utf8` entry in the scanned code is "
            f"pointed at by at least one of the roles above, which is a stronger result "
            f"than it sounds: the last {word(2)} to be accounted for were the platform "
            f"string in `ModuleTarget` and the algorithm name in `ModuleHashes`, and "
            f"neither of those attributes is defined anywhere in the specification."
        )
    out.append("")

    out.append("## 2.6 Method handles")
    out.append("")
    kinds_seen = sorted(int(kind) for kind in handles)
    out.append(
        f"A `CONSTANT_MethodHandle` is a reference kind and an index, and the reference "
        f"kind decides both what the handle does and what kind of entry the index is "
        f"allowed to point at. {section(edition, '4.4.8')} states that as a constraint "
        f"per kind, and it is the constraint a hand written class file gets wrong first."
    )
    out.append("")
    out.append("| kind | name in the header | what it does | must point at | seen |")
    out.append("|---:|---|---|---|---|")
    for number, (allowed, does) in sorted(REFERENCE.items()):
        name = [key for key, value in header.items()
                if key.startswith("JVM_REF_") and value[0] == number]
        points = handles.get(str(number), {})
        seen = ", ".join(f"{count:,} at `{target}`"
                         for target, count in sorted(points.items())) or ""
        out.append(
            f"| {number} | `{name[0] if name else ''}` | {does} | "
            + " or ".join(f"`{target}`" for target in sorted(allowed))
            + f" | {seen} |"
        )
    out.append("")
    both = sorted(kind for kind in kinds_seen
                  if len(handles.get(str(kind), {})) > 1)
    out.append(
        f"Only {word(len(kinds_seen))} of the {word(len(REFERENCE))} occur in the "
        f"scanned code, at "
        + ", ".join(str(kind) for kind in kinds_seen)
        + ". "
        + (f"Reference kind {both[0]} is the one to look at: it points at a "
           f"`MethodRefEntry` and at an `InterfaceMethodRefEntry`, both of which are "
           f"legal, because class file version 52 allowed a static or private interface "
           f"method to be the target of a handle and the specification states that as a "
           f"version condition on this one row. A reader who takes the constraint as "
           f"`Methodref` alone will reject a class file that every JVM accepts."
           if both else "")
    )
    out.append("")
    sites = dynamic.get("call_site", 0)
    constants_ = dynamic.get("constant", 0)
    out.append(
        f"The other two kinds that name a bootstrap method are counted the same way, and "
        f"they are lopsided: {sites:,} `CONSTANT_InvokeDynamic` call sites against "
        f"{constants_:,} `CONSTANT_Dynamic` constants. Both hand a name and a type to a "
        f"bootstrap method, and the difference is what comes back. A call site is linked "
        f"once and then keeps calling a target that may change, while a dynamic constant "
        f"is computed once and then is a value forever. "
        + ("The compiler that built the scanned code emits the first and not the second, "
           "which is worth knowing before reading a class file looking for one."
           if not constants_ else
           "Both occur in the scanned code.")
    )
    out.append("")

    out.append("## 2.7 The same constant twice")
    out.append("")
    classes_with = duplicates.get("classes", 0)
    repeated = duplicates.get("entries", 0)
    out.append(
        f"A pool is supposed to be a set. Nothing in the format requires it to be, and a "
        f"class file with two entries that mean exactly the same thing is legal, loads, "
        f"and runs. In the scanned code {classes_with:,} classes hold at least one "
        f"repeat, {repeated:,} entries in total, which is {share(repeated, total)} of "
        f"the pool. The probe finds them by rendering each entry to the thing it means "
        f"and looking for two that render the same, which is a different test from "
        f"comparing bytes."
    )
    out.append("")
    families: dict[str, list[dict[str, str]]] = {}
    for found in sorted(data.values(), key=lambda item: item["platform"]):
        for entry in found["duplicates"]["entries_seen"]:
            families.setdefault(entry["entry"].split("|", 1)[0], []).append(entry)
    out.append("| tag | kind | repeats | for example |")
    out.append("|---:|---|---:|---|")
    for number in sorted(families, key=int):
        example = families[number][0]
        out.append(
            f"| {number} | `{by_tag[int(number)][0]}` | {len(families[number]):,} | "
            f"`{example['entry'].split('|', 1)[1]}` in `{example['class']}` |"
        )
    out.append("")
    methods: dict[str, int] = {}
    receivers: dict[str, int] = {}
    for entries_here in families.values():
        for entry in entries_here:
            body = entry["entry"].split("|", 1)[1]
            methods[body.split(".")[-1].split(":")[0]] = 1 + methods.get(
                body.split(".")[-1].split(":")[0], 0)
            receivers[body.rsplit(".", 1)[0]] = 1 + receivers.get(
                body.rsplit(".", 1)[0], 0)
    inherited = sorted(name for name in methods if name in OBJECT_METHODS)
    polymorphic = sorted(name for name in receivers
                         if name.startswith("java/lang/invoke/"))
    if inherited:
        out.append(
            f"Every repeat in the scanned code is one of {word(2)} things, and both are "
            f"the compiler being unable to see that two references are the same. The "
            f"first is a method `java.lang.Object` declares, called on a receiver whose "
            f"static type is an interface: "
            + ", ".join(f"`{name}`" for name in inherited)
            + f". Those are {sum(methods[name] for name in inherited):,} of the repeats. "
            f"An interface does not declare them, so each call site names them on the "
            f"interface it is calling through, and two different interfaces in one class "
            f"produce two entries that a reader would call the same call."
        )
        out.append("")
    if polymorphic:
        out.append(
            f"The second is a signature polymorphic call, on "
            + ", ".join(f"`{name}`" for name in polymorphic)
            + f", which is {sum(receivers[name] for name in polymorphic):,} of them. "
            f"A signature polymorphic method has whatever descriptor the call site says "
            f"it has, so two call sites with the same descriptor produce two entries "
            f"only when something else about them differs, and the compiler does not "
            f"look. This is the case where the duplicate is arguably load bearing: the "
            f"descriptor is the argument."
        )
        out.append("")
    only_here: dict[str, list[str]] = {}
    for platform, found in sorted(data.items()):
        here = {entry["class"] for entry in found["duplicates"]["entries_seen"]}
        elsewhere = set()
        for other, more in data.items():
            if other != platform:
                elsewhere |= {entry["class"] for entry in
                              more["duplicates"]["entries_seen"]}
        if here - elsewhere:
            only_here[platform] = sorted(here - elsewhere)
    if only_here:
        out.append(
            "The scanned images are not identical, and the repeats say so: "
            + "; ".join(
                f"{', '.join(f'`{name}`' for name in names)} "
                f"{'is' if len(names) == 1 else 'are'} only in `{platform}`"
                for platform, names in sorted(only_here.items()))
            + ". That is a platform specific class rather than a platform specific "
            "compiler, and it is the reason the counts here are sums over environments "
            "rather than one environment's answer."
        )
        out.append("")

    out.append("## 2.8 Provenance")
    out.append("")
    out.append(
        "A hash of each source as it was read, for the same reason every other generated "
        "page here carries one: a source that was rewritten underneath a table should be "
        "an event somebody looks at rather than a table that quietly says something else "
        "next Tuesday."
    )
    out.append("")
    out.append("| source | sha256 |")
    out.append("|---|---|")
    for name, digest in sorted(hashes.items()):
        out.append(f"| {name} | `{digest}` |")
    out.append("")
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

    header_text, header_sha = source(tag, HEADER_PATH)
    header = constants(header_text, HEADER_PATH, "JVM_CONSTANT_Utf8")
    hpp_text, hpp_sha = source(tag, TAG_HPP)
    cpp_text, cpp_sha = source(tag, TAG_CPP)

    hotspot = constants(hpp_text, TAG_HPP, "JVM_CONSTANT_UnresolvedClass")
    values = {name: value for name, (value, _) in header.items()}
    values.update({name: value for name, (value, _) in hotspot.items()})
    # The two bounds are aliases for the first and the last implementation tag rather
    # than tags of their own, so they are dropped before anything is printed.
    internal = {value: (name, note) for name, (value, note) in hotspot.items()
                if not name.endswith(("Min", "Max"))}
    checks = predicates(hpp_text, values)
    named = switch(cpp_text, "constantTag::internal_name", RETURNS_STRING)
    errors = switch(cpp_text, "constantTag::error_value", RETURNS_TAG)
    basic = switch(cpp_text, "constantTag::basic_type", RETURNS_TYPE)

    url = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    body = fetch(url)
    page = body.decode("utf-8", errors="replace")
    spec_sha = hashlib.sha256(body).hexdigest()
    index_says = check_the_index(spec_sha)
    spec = tables(page)
    shapes = structures(page)

    data = read(RESULTS, "probes/constpool/run.py")
    census = read(CENSUS, "probes/classfile-census/run.py")
    kinds = one_answer(data, "kinds")
    tags = summed(data, "tags")
    roles = summed(data, "utf8_roles")
    handles = summed(data, "method_handles")
    dynamic = summed(data, "dynamic")
    duplicates = summed(data, "duplicates")

    notes = verify(spec, shapes, header, internal, checks, kinds, data, census, tags,
                   roles, handles)

    hashes = {
        f"JVMS {edition} chapter {SPEC_CHAPTER}, which {index_says}": spec_sha,
        f"`{HEADER_PATH}` at `{tag}`": header_sha,
        f"`{TAG_HPP}` at `{tag}`": hpp_sha,
        f"`{TAG_CPP}` at `{tag}`": cpp_sha,
    }

    out = build(pinned, spec, shapes, header, internal, named, errors, basic, checks,
                kinds, data, census, tags, roles, handles, dynamic, duplicates, notes,
                hashes)

    if args.show:
        print(f"{len(spec_tags(spec, '4.4-B'))} entry kinds, "
              f"{len(spec_tags(spec, '4.4-C'))} of them loadable, "
              f"{len(internal)} tags HotSpot uses that no class file may contain")
        print(f"{sum(tags.values()):,} pool entries counted over {len(data)} "
              f"environments, {sum(roles.values()):,} uses of a CONSTANT_Utf8")
        print(f"{duplicates.get('entries', 0):,} entries repeat a constant the same pool "
              f"already holds, in {duplicates.get('classes', 0):,} classes")
        return 0

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_constpool.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != out:
            print(f"{OUTPUT} does not match its sources, run tools/gen_constpool.py",
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
