#!/usr/bin/env python3
"""Generate the class file structure section from three sources that check each other.

M2's exit criterion says BP-CLASSFILE section 2 is generated rather than written. Section
2 is the shape of the file: the header, the constant pool, the attributes and the access
flags. Every one of those is a table that has been copied by hand into a thousand blog
posts, and the copies disagree with each other in small ways that only matter when a
reader is holding a hex editor.

Three sources, the same three as the opcode table and for the same reason:

  the specification    JVMS chapter 4 is normative. It gives the structure, the constant
                       pool tags, the attribute tables with the class file version each
                       attribute appeared in, and four separate access flag tables that
                       assign different meanings to the same bits.

  the implementation   `classfile_constants.h.template` is the header the JDK ships to
                       anybody writing a native agent, and it is where the tag numbers
                       and the flag masks are numbers rather than prose.

  the platform library `java.lang.reflect.AccessFlag` knows which locations each flag is
                       legal in and knows it per class file version, which is the only
                       machine readable form of a fact the specification states in
                       sentences. `java.lang.classfile.Attributes` knows which attributes
                       the platform can model.

The counts come from `probes/classfile-census/results`, and they are the part a reader
cannot get from any document: which of these tags and attributes are in every class file
and which are in four.

  python tools/gen_classfile.py            write docs/generated/classfile.md
  python tools/gen_classfile.py --check    fail if the committed page is stale
  python tools/gen_classfile.py --print    the summary, without writing anything

The header comes from raw.githubusercontent.com at the tag in `docs/pin.json`, or from a
local checkout when `JVX_JDK_SRC` points at one. The specification chapter comes from
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
RESULTS = pathlib.Path("probes/classfile-census/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OUTPUT = pathlib.Path("docs/generated/classfile.md")

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
HEADER_PATH = "src/java.base/share/native/include/classfile_constants.h.template"
SPEC_PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"
SPEC_CHAPTER = "4"

# `<b>Table&nbsp;4.4-A.&nbsp;Constant pool tags (by section)</b>` and the table that
# follows it. Oracle numbers its tables, which makes them addressable, which is the only
# reason parsing a specification page is a reasonable thing to do at all. A long table is
# split into a first part and one or more `(cont.)` parts under the same number, and a
# regex that does not allow for that reads the first part and silently loses the rest.
TABLE_TITLE = re.compile(
    r"<b>Table&nbsp;(?P<id>[\d.]+-[A-Z])(?: \(cont\.\))?\.&nbsp;(?P<title>.*?)</b>")
TABLE_BODY = re.compile(r"<tbody>(?P<body>.*?)</tbody>", re.S)
ROW = re.compile(r"<tr>(?P<row>.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.S)

# `ClassFile {\n    u4 magic;\n ... }` inside a screen block.
STRUCT = re.compile(
    r'<pre class="screen">\s*(?P<name>\w+)\s*\{(?P<body>.*?)\}\s*</pre>', re.S)
FIELD = re.compile(
    r"^(?P<type>\w+)\s+(?P<name>\w+)(?:\[(?P<count>[^\]]*)\])?;$")

# `JVM_CONSTANT_Utf8 = 1,` and `JVM_ACC_PUBLIC = 0x0001,` inside an anonymous enum.
CONSTANT = re.compile(
    r"^\s*(?P<name>JVM_(?:CONSTANT|ACC)_\w+)\s*=\s*(?P<value>0x[0-9a-fA-F]+|\d+)\s*,?")

# Which specification table gives the flags for which part of the file, and what this
# calls that part. The names on the right are `AccessFlag.Location`, so the specification
# and the platform library can be compared without a translation table in between.
FLAG_TABLES = {
    "4.1-B": ("CLASS", "a class or interface", "4.1"),
    "4.5-A": ("FIELD", "a field", "4.5"),
    "4.6-A": ("METHOD", "a method", "4.6"),
    "4.7.6-A": ("INNER_CLASS", "a nested class", "4.7.6"),
}

# The locations the census reports, in the order a reader meets them, with the name the
# specification's own attribute table uses for each.
PLACES = {
    "CLASS": "ClassFile",
    "FIELD": "field_info",
    "METHOD": "method_info",
    "CODE": "Code",
    "RECORD_COMPONENT": "record_component_info",
}

# What each item of the ClassFile structure is for. The specification says all of this in
# several pages of prose, and a reader looking at a hex dump wants one line each.
ITEM = {
    "magic": "0xCAFEBABE, and nothing else",
    "minor_version": "0, except for a preview build, where it is 65535",
    "major_version": "the class file version, which decides what the rest may contain",
    "constant_pool_count": "one more than the number of usable pool slots",
    "constant_pool": "indexed from 1, and slot 0 does not exist",
    "access_flags": "the class's own flags, which are not the ones InnerClasses records",
    "this_class": "a CONSTANT_Class index naming this class",
    "super_class": "a CONSTANT_Class index, or 0, which only Object may use",
    "interfaces_count": "how many direct superinterfaces",
    "interfaces": "CONSTANT_Class indices, in declaration order",
    "fields_count": "how many fields this class declares, inherited ones excluded",
    "fields": "one field_info each",
    "methods_count": "how many methods this class declares",
    "methods": "one method_info each, constructors and the static initialiser included",
    "attributes_count": "how many attributes are attached to the class itself",
    "attributes": "one attribute_info each, and an unrecognised one must be skipped",
}

WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}

# What each `AttributeMapper.AttributeStability` constant means for somebody rewriting a
# class file, which is the question the enum is answering and not the words it uses. The
# generator stops on a constant that is not here rather than printing the name raw,
# because a stability nobody has described is a thing to go and read about.
STABILITY = {
    "STATELESS": ("can be copied byte for byte",
                  "can be copied byte for byte"),
    "CP_REFS": ("holds constant pool references, which a transform has to remap",
                "hold constant pool references, which a transform has to remap"),
    "LABELS": ("holds offsets into a method body, which a transform has to move",
               "hold offsets into a method body, which a transform has to move"),
    "UNSTABLE": ("cannot be copied safely, so a transform that changes the class drops it",
                 "cannot be copied safely, so a transform that changes the class drops "
                 "them"),
}


def word(count: int) -> str:
    return WORDS.get(count, str(count))


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
    """One HTML cell as the text a reader would see.

    The last two substitutions are not cosmetic. Oracle marks up a keyword inside a
    sentence as its own element, so stripping the tags leaves `Declared public ;` and
    `same nest ( 5.4.4 )`, and a table full of that reads like a bad scan rather than
    like the specification.
    """
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
        if body is None:
            continue
        rows = []
        for row in ROW.finditer(body.group("body")):
            cells = [plain(cell.group("cell")) for cell in CELL.finditer(row.group("row"))]
            if cells:
                rows.append(cells)
        found.setdefault(title.group("id"), []).extend(rows)
    if len(found) < 10:
        sys.exit(f"only {len(found)} tables on JVMS chapter {SPEC_CHAPTER}, so the page "
                 f"shape changed")
    return found


def structures(text: str) -> dict[str, list[dict[str, str]]]:
    """Every `Name { ... }` block on the page, as a list of items."""
    found: dict[str, list[dict[str, str]]] = {}
    for block in STRUCT.finditer(text):
        items = []
        for line in html.unescape(block.group("body")).splitlines():
            line = re.sub(r"\s+", " ", plain(line)).strip()
            match = FIELD.match(line)
            if match:
                items.append({"type": match.group("type"), "name": match.group("name"),
                              "count": match.group("count") or ""})
        if items:
            found.setdefault(block.group("name"), items)
    return found


def constants(text: str) -> dict[str, int]:
    """`JVM_CONSTANT_*` and `JVM_ACC_*` from the header the JDK ships to agent authors."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        match = CONSTANT.match(line.split("//")[0].split("/*")[0])
        if match:
            found[match.group("name")] = int(match.group("value"), 0)
    if "JVM_CONSTANT_Utf8" not in found or "JVM_ACC_PUBLIC" not in found:
        sys.exit(f"{HEADER_PATH}: the enums did not parse, so the header shape changed")
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


def results() -> dict[str, dict]:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}, run probes/classfile-census/run.py")
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def one_answer(data: dict[str, dict], field: str) -> dict:
    """A field every environment must report identically, because it is a property of
    the JDK rather than of the machine."""
    answer = None
    for platform, found in sorted(data.items()):
        if answer is None:
            answer = found[field]
        elif found[field] != answer:
            sys.exit(f"{platform} reports a different {field} from another environment. "
                     f"Two JDKs, one pin.")
    return answer


def summed(data: dict[str, dict], field: str) -> dict:
    """A count, summed over every environment.

    Summed rather than averaged, because each environment scanned its own copy of
    java.base and the copies are not identical. A sum is a count of things actually seen
    and a mean is a count of nothing.
    """
    total: dict = {}
    for found in data.values():
        for key, value in found[field].items():
            if isinstance(value, dict):
                inner = total.setdefault(key, {})
                for name, count in value.items():
                    inner[name] = inner.get(name, 0) + count
            else:
                total[key] = total.get(key, 0) + value
    return total


def allowed_places(rows: list[list[str]]) -> dict[str, set[str]]:
    """Table 4.7-C as attribute to the structures it may be attached to.

    Both cells hold lists. One row can name three attributes and five locations, which is
    the specification saving a page and a parser having to split on the comma twice.
    """
    found: dict[str, set[str]] = {}
    for row in rows:
        if len(row) < 2:
            continue
        for name in (part.strip() for part in row[0].split(",")):
            for place in (part.strip() for part in row[1].split(",")):
                if name and place:
                    found.setdefault(name, set()).add(place)
    return found


def pooled(data: dict[str, dict], field: str) -> dict[str, list[str]]:
    """Example names from every environment, merged rather than made to agree.

    Two environments scanned two copies of java.base and the copies differ, so the
    examples are allowed to differ. Capped at five per key, because the point of an
    example is that a reader goes and looks at one.
    """
    total: dict[str, list[str]] = {}
    for found in sorted(data.values(), key=lambda item: item["platform"]):
        for key, names_ in found[field].items():
            here = total.setdefault(key, [])
            for name in names_:
                if name not in here and len(here) < 5:
                    here.append(name)
    return total


def flag_names(rows: list[list[str]]) -> dict[int, tuple[str, str]]:
    """One access flag table, as mask to name and meaning."""
    found: dict[int, tuple[str, str]] = {}
    for row in rows:
        if len(row) < 3 or not row[0].startswith("ACC_"):
            continue
        found[int(row[1], 16)] = (row[0], row[2])
    return found


def verify(spec: dict[str, list[list[str]]], shapes: dict, header: dict[str, int],
           flags: dict, tags: dict, attribute_counts: dict, pinned: dict,
           data: dict) -> list[str]:
    """Every check that has to pass before a page is worth writing.

    Each failure is a source having moved. The right answer to that is a person reading a
    diff rather than a generator picking a winner, so these exit rather than warn.
    """
    notes: list[str] = []

    if "ClassFile" not in shapes:
        sys.exit("JVMS chapter 4 no longer prints a ClassFile structure")
    listed = [item["name"] for item in shapes["ClassFile"]]
    if listed != list(ITEM):
        sys.exit(f"the ClassFile structure is now {listed}, which is not what this "
                 f"describes item by item")

    # The specification's tag numbers against the header's.
    for row in spec["4.4-A"]:
        if not row or not row[0].startswith("CONSTANT_"):
            continue
        name, number = row[0], int(row[1])
        key = "JVM_" + name
        if key not in header:
            sys.exit(f"the JVMS has {name} and {HEADER_PATH} has no {key}")
        if header[key] != number:
            sys.exit(f"the JVMS says {name} is {number} and the header says {header[key]}")

    spec_tags = {int(row[1]): row[0] for row in spec["4.4-A"]
                 if row and row[0].startswith("CONSTANT_")}
    for tag, count in sorted(tags.items()):
        if int(tag) not in spec_tags:
            sys.exit(f"tag {tag} occurs {count} times in the scanned code and the JVMS "
                     f"does not list it")

    # Every predefined attribute, against the platform library and against what occurred.
    attributes = {row[0] for row in spec["4.7-A"] if row}
    # Attributes the platform models that the specification does not define are not a
    # disagreement. The format's whole extension mechanism is that anybody may define
    # one, and the JDK's own tools have. Section 2.3 names them rather than hiding them.
    places = allowed_places(spec["4.7-C"])
    missing = sorted(attributes - set(places))
    if missing:
        sys.exit(f"JVMS table 4.7-A defines {', '.join(missing)} and table 4.7-C says "
                 f"nothing about where they may appear")
    for location, found in sorted(attribute_counts.items()):
        where = PLACES[location]
        for name, count in sorted(found.items()):
            if name not in attributes:
                continue
            if where not in places.get(name, set()):
                sys.exit(f"{name} occurs {count} times on a {where} and JVMS table 4.7-C "
                         f"does not allow it there")

    # The four access flag tables, against the header and against the platform library.
    history = one_answer(data, "flag_locations")
    for table, (location, _, _) in FLAG_TABLES.items():
        for mask, (name, _) in sorted(flag_names(spec[table]).items()):
            key = "JVM_" + name
            if key not in header:
                sys.exit(f"the JVMS has {name} for {location} and the header has no {key}")
            if header[key] != mask:
                sys.exit(f"{name} is 0x{mask:04x} in the JVMS and 0x{header[key]:04x} "
                         f"in the header")
            # At some version, not at this one. The specification's tables cover every
            # class file version at once and `ACC_STRICT` is the flag that proves it:
            # table 4.6-A still lists it for methods and it has meant nothing since
            # version 61. Requiring agreement at the latest version would be requiring
            # the specification to forget its own history.
            if not any(entry["mask"] == mask
                       and any(location in where
                               for where in history[flag_name].values())
                       for flag_name, entry in flags.items()):
                sys.exit(f"the JVMS allows {name} on {location} and this JDK's "
                         f"AccessFlag does not, at any class file version")

    for name, entry in sorted(flags.items()):
        for location in entry["locations"]:
            if location not in FLAG_TABLES_BY_LOCATION:
                continue
            table = FLAG_TABLES_BY_LOCATION[location]
            if entry["mask"] not in flag_names(spec[table]):
                sys.exit(f"this JDK allows {name} on {location} and JVMS table {table} "
                         f"does not list 0x{entry['mask']:04x}")

    # The pin's own numbers, which several other generated pages depend on.
    latest = one_answer(data, "api")["latest_major"]
    if latest != pinned["jdk_class_file_major"]:
        sys.exit(f"this JDK writes class file version {latest} and docs/pin.json says "
                 f"{pinned['jdk_class_file_major']}")
    if pinned["jvms_class_file_major"] > latest:
        sys.exit("the pinned specification edition describes a later class file version "
                 "than the pinned JDK writes, which cannot be right")
    if pinned["jvms_class_file_major"] < latest:
        behind = latest - pinned["jvms_class_file_major"]
        notes.append(
            f"The pinned JDK writes class file version {latest} and the pinned "
            f"specification edition describes version {pinned['jvms_class_file_major']}. "
            f"Everything below comes from the edition, so a feature added in the "
            f"{word(behind)} release{'' if behind == 1 else 's'} since it was published "
            f"is not in these tables. The counts are from the JDK, so anything that did "
            f"appear in the meantime would show up as an unlisted tag or an unknown "
            f"attribute, and neither of those is stopping this generator today."
        )

    return notes


FLAG_TABLES_BY_LOCATION = {location: table
                           for table, (location, _, _) in FLAG_TABLES.items()}


def section(edition: str, number: str, label: str | None = None) -> str:
    page = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    return f"[{label or ('§' + number)}]({page}#jvms-{number})"


def share(count: int, total: int) -> str:
    # A tag that never occurred gets an empty cell rather than `<0.01%`, because "less
    # than a hundredth of a per cent" and "not once" are different answers and the second
    # one is the interesting one.
    if not total or not count:
        return ""
    value = 100 * count / total
    return f"{value:.2f}%" if value >= 0.01 else "<0.01%"


def build(pinned: dict, spec: dict, shapes: dict, header: dict[str, int], api: dict,
          flags: dict, api_attributes: dict, data: dict, tags: dict, versions: dict,
          attribute_counts: dict, flag_counts: dict, unknown: dict, stray: dict,
          notes: list[str], hashes: dict[str, str]) -> str:
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]

    platforms = ", ".join(f"`{name}`" for name in sorted(data))
    builds = sorted({found["java_build"] for found in data.values()})
    measured = sorted({found["measured"] for found in data.values()})
    scanned = sorted({found["scan"]["module"] for found in data.values()})
    classes = sum(found["scan"]["classes"] for found in data.values())
    fields = sum(found["scan"]["fields"] for found in data.values())
    methods = sum(found["scan"]["methods"] for found in data.values())
    entries = sum(found["scan"]["pool_entries"] for found in data.values())
    pool_total = sum(tags.values())

    out: list[str] = []
    out.append("# BP-CLASSFILE section 2. The structure of a class file")
    out.append("")
    out.append(
        f"Generated by `tools/gen_classfile.py`. Do not edit it, edit a source. There "
        f"are three: JVMS {edition} chapter 4 for what the format is, "
        f"`{HEADER_PATH}` at `{tag}` for the numbers as the JDK writes them for native "
        f"agents, and `java.lang.reflect.AccessFlag` with "
        f"`java.lang.classfile.Attributes` read off the pinned JDK for what the platform "
        f"itself will accept. The counts come from `probes/classfile-census/results`."
    )
    out.append("")
    out.append(
        f"Counted over {', '.join(f'`{m}`' for m in scanned)} on {platforms}, java "
        f"{', '.join(builds)}, on {', '.join(measured)}: {classes:,} class files, "
        f"{fields:,} fields, {methods:,} methods, {entries:,} constant pool entries."
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

    out.append("## 2.1 The ClassFile structure")
    out.append("")
    out.append(
        f"Every class file is one of these, in this order, with nothing between the "
        f"items and no padding. `u1`, `u2` and `u4` are unsigned big endian integers of "
        f"one, two and four bytes. The structure itself is {section(edition, '4.1')}."
    )
    out.append("")
    out.append("| item | type | how many | what it is |")
    out.append("|---|---|---|---|")
    for item in shapes["ClassFile"]:
        count = f"`{item['count']}`" if item["count"] else "1"
        out.append(f"| `{item['name']}` | `{item['type']}` | {count} | "
                   f"{ITEM[item['name']]} |")
    out.append("")
    version_rows = sorted(versions.items(),
                          key=lambda item: (-item[1], item[0]))
    common = version_rows[0]
    out.append(
        f"The scanned code answers the first question a reader has about that table, "
        f"which is what real values look like. {common[1]:,} of the {classes:,} class "
        f"files declare version {common[0]}, which is what this JDK's own compiler emits."
    )
    out.append("")
    odd = version_rows[1:]
    if odd:
        examples = pooled(data, "version_examples")
        out.append(
            "The rest are worth naming, because a module built entirely by one compiler "
            "in one release is what a reader would assume and it is not what is there. "
            + " ".join(
                f"{word(count).capitalize()} of them "
                f"{'is' if count == 1 else 'are'} at version {number}, "
                + ("which is " if len(examples.get(number, [])) == 1 else "which are ")
                + ", ".join(f"`{name}`" for name in examples.get(number, []))
                + "."
                for number, count in odd)
        )
        out.append("")

    out.append("## 2.2 The constant pool")
    out.append("")
    largest = max(found["scan"]["largest_pool"] for found in data.values())
    largest_class = [found["scan"]["largest_pool_class"] for found in data.values()
                     if found["scan"]["largest_pool"] == largest][0]
    out.append(
        f"The pool is indexed from 1 and there is no entry 0, so `constant_pool_count` "
        f"is one more than the number of slots. A `CONSTANT_Long` or a `CONSTANT_Double` "
        f"takes two slots and the second one is unusable, which is the oldest wart in "
        f"the format. The largest pool in the scanned code has {largest:,} slots and "
        f"belongs to `{largest_class}`."
    )
    out.append("")
    out.append("| tag | kind | header constant | seen | share | JVMS |")
    out.append("|---:|---|---|---:|---:|---|")
    for row in spec["4.4-A"]:
        if not row or not row[0].startswith("CONSTANT_"):
            continue
        name, number = row[0], int(row[1])
        where = row[2].replace("§", "") if len(row) > 2 else ""
        count = tags.get(str(number), 0)
        out.append(
            f"| {number} | `{name}` | `JVM_{name}` = {header['JVM_' + name]} | "
            f"{count:,} | {share(count, pool_total)} | "
            f"{section(edition, where) if where else ''} |"
        )
    out.append("")
    absent = [row[0] for row in spec["4.4-A"]
              if row and row[0].startswith("CONSTANT_")
              and not tags.get(str(int(row[1])), 0)]
    if absent:
        out.append(
            f"{word(len(absent)).capitalize()} of them "
            f"{'never occurs' if len(absent) == 1 else 'never occur'} in the scanned "
            f"code: "
            + ", ".join(f"`{name}`" for name in absent)
            + ". That is a fact about one module rather than about the format, and it is "
            "the reason this table counts rather than asserts."
        )
        out.append("")

    out.append("## 2.3 The attributes")
    out.append("")
    out.append(
        f"An attribute is a name, a length and that many bytes, so a reader of a class "
        f"file can skip one it does not recognise without knowing anything about it. "
        f"That is the whole extension mechanism of the format. `since` is the class file "
        f"version the specification first defined the attribute in, and `API` is whether "
        f"the pinned JDK's `java.lang.classfile` can model it rather than hand it back "
        f"as bytes. The counts are split by where the attribute was attached."
    )
    out.append("")
    places = allowed_places(spec["4.7-C"])
    columns = [name for name in PLACES.values()]
    out.append("| attribute | since | Java SE | may appear on | API | "
               + " | ".join(columns) + " | JVMS |")
    out.append("|---|---:|---:|---|---|" + "---:|" * len(columns) + "---|")
    for row in spec["4.7-A"]:
        if len(row) < 4:
            continue
        name, where, since, release = row[0], row[1].replace("§", ""), row[2], row[3]
        allowed = ", ".join(f"`{place}`" for place in sorted(places.get(name, set())))
        seen = [attribute_counts.get(location, {}).get(name, 0) for location in PLACES]
        out.append(
            f"| `{name}` | {since} | {release} | {allowed or 'nothing'} | "
            f"{'yes' if name in api_attributes else 'no'} | "
            + " | ".join(f"{count:,}" if count else "" for count in seen)
            + f" | {section(edition, where)} |"
        )
    out.append("")
    listed = {row[0] for row in spec["4.7-A"] if row}
    modelled = sorted(set(api_attributes) & listed)
    extensions = sorted(set(api_attributes) - listed)
    out.append(
        (f"The pinned JDK's class file API models every one of the {len(listed)} "
         f"attributes the specification defines."
         if len(modelled) == len(listed) else
         f"The pinned JDK's class file API models {len(modelled)} of the {len(listed)} "
         f"attributes the specification defines.")
        + " Everything else it hands back as an "
        f"`UnknownAttribute`, which is the correct behaviour rather than a gap: an "
        f"attribute a reader does not recognise has to survive a round trip untouched, "
        f"and a library that parsed one it did not understand could not promise that."
    )
    out.append("")
    if extensions:
        out.append(
            f"It also models {word(len(extensions))} more that the specification does "
            f"not define, "
            + ", ".join(f"`{name}`" for name in extensions)
            + ". Those are the extension mechanism being used rather than described: "
            "anybody may define an attribute, the JDK's own tools have, and a reader who "
            "meets one in a class file has met somebody else's convention rather than a "
            "corrupt file."
        )
        out.append("")
    # Two facts the API carries per attribute that the specification states in prose, one
    # attribute at a time, in thirty different subsections.
    repeatable = sorted(name for name, entry in api_attributes.items()
                        if entry["allow_multiple"])
    if repeatable:
        out.append(
            f"{word(len(repeatable)).capitalize()} of the "
            f"{len(api_attributes)} may appear more than once on the same element: "
            + ", ".join(f"`{name}`" for name in repeatable)
            + ". Every other one is at most one per element, so a parser that keeps the "
            "last of each is right about the rest of the format and wrong about these."
        )
        out.append("")
    stability: dict[str, list[str]] = {}
    for name, entry in sorted(api_attributes.items()):
        if entry["stability"] not in STABILITY:
            sys.exit(f"{name} has attribute stability {entry['stability']}, which this "
                     f"generator has no sentence for")
        stability.setdefault(entry["stability"], []).append(name)
    out.append(
        "Each one also says what happens to it when a class file is rewritten, which is "
        "the question a reader hits the moment they stop reading class files and start "
        "producing them. "
        + " ".join(
            f"{word(len(stability[kind])).capitalize()} of them "
            f"{STABILITY[kind][0 if len(stability[kind]) == 1 else 1]}."
            for kind in sorted(stability, key=lambda kind: -len(stability[kind])))
    )
    out.append("")
    if stability.get("UNSTABLE"):
        out.append(
            "The unstable "
            + ("one is " if len(stability["UNSTABLE"]) == 1 else "ones are ")
            + ", ".join(f"`{name}`" for name in stability["UNSTABLE"])
            + ". They carry both constant pool references and offsets into code, so "
            "there is no rule that keeps them correct across an edit, and the library "
            "says so rather than preserving them and hoping."
        )
        out.append("")
    if unknown:
        out.append(
            "Attributes in the scanned code that the API did not recognise: "
            + ", ".join(f"`{name}` ({count:,})" for name, count in sorted(unknown.items()))
            + "."
        )
    else:
        out.append(
            "Nothing in the scanned code came back as an unknown attribute, so every "
            "attribute the JDK's own compiler emitted into its own runtime image is one "
            "the JDK's own class file library models."
        )
    out.append("")

    out.append("## 2.4 The access flags")
    out.append("")
    out.append(
        f"There are four separate flag tables in the specification and they assign "
        f"different meanings to the same bits, which is the single most common way a "
        f"hand written class file parser goes wrong. 0x0020 is `ACC_SUPER` on a class "
        f"and `ACC_SYNCHRONIZED` on a method. 0x0040 is `ACC_VOLATILE` on a field and "
        f"`ACC_BRIDGE` on a method. 0x0080 is `ACC_TRANSIENT` on a field and "
        f"`ACC_VARARGS` on a method. A bit means nothing until you know which structure "
        f"you are reading it out of."
    )
    out.append("")
    for table, (location, description, where) in FLAG_TABLES.items():
        rows = flag_names(spec[table])
        # The nested class flags live inside an attribute rather than in a structure the
        # walk visits, so there is no count for them. A column of zeros would read as a
        # measurement that came back empty, which is a different claim, so the column is
        # dropped instead and the paragraph below the table says why.
        counted = location in flag_counts
        out.append(f"### On {description}, {section(edition, where)}")
        out.append("")
        out.append("| mask | flag | seen | what it means |" if counted
                   else "| mask | flag | what it means |")
        out.append("|---|---|---:|---|" if counted else "|---|---|---|")
        for mask, (name, meaning) in sorted(rows.items()):
            count = flag_counts.get(location, {}).get(str(mask), 0)
            seen = f" {count:,} |" if counted else ""
            out.append(f"| `0x{mask:04x}` | `{name}` |{seen} {meaning} |")
        out.append("")
        if location == "INNER_CLASS":
            out.append(
                "Those are not counted, because they live in the `InnerClasses` "
                "attribute rather than in a structure this walk visits. They are here "
                "because they are the flags a reader is most likely to confuse with the "
                "class's own: a nested class records its real accessibility here and "
                "records something weaker in its own `access_flags`."
            )
            out.append("")

    changed = {name: entry for name, entry in flags.items()
               if len({tuple(sorted(where)) for where in
                       one_answer(data, "flag_locations")[name].values()}) > 1}
    if changed:
        releases = one_answer(data, "releases")
        out.append("### Flags whose locations changed")
        out.append("")
        out.append(
            f"{word(len(changed)).capitalize()} of the flags this JDK knows do not mean "
            f"the same thing at every class file version, and `AccessFlag` is the only "
            f"source here that carries that. A table printed without it would be right "
            f"for the current version and wrong for a file compiled in 2015."
        )
        out.append("")
        out.append("| flag | mask | where it applies, by class file version |")
        out.append("|---|---|---|")
        for name in sorted(changed):
            history = one_answer(data, "flag_locations")[name]
            out.append(f"| `ACC_{name}` | `0x{flags[name]['mask']:04x}` | "
                       f"{spans(history, releases)} |")
        out.append("")

    if stray:
        out.append("### Bits that are set and should not be")
        out.append("")
        out.append(
            f"JVMS {section(edition, '4.1')} says the flag bits it does not assign are "
            f"reserved, should be zero in a generated class file, and should be ignored "
            f"by a JVM. The scanned code does not obey the first half of that, and "
            f"finding out took a count rather than an argument."
        )
        out.append("")
        out.append("| where | bit | how many | for example |")
        out.append("|---|---|---:|---|")
        for key in sorted(stray):
            location, mask = key.rsplit(".", 1)
            entry = stray[key]
            out.append(
                f"| `{PLACES[location]}` | `0x{int(mask):04x}` | {entry['count']:,} | "
                + ", ".join(f"`{name}`" for name in entry["examples"]) + " |"
            )
        out.append("")
        out.append(
            "That is the specification's own advice working as intended. A JVM that "
            "rejected these would reject its own runtime image, and a parser somebody "
            "writes while reading section 2 will meet them on its first real input."
        )
        out.append("")

    out.append("## 2.5 Provenance")
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


def spans(history: dict[str, list[str]], releases: dict[str, int]) -> str:
    """A flag's location history folded into version ranges.

    Printed as ranges rather than as 28 rows, because the shape a reader needs is "these
    versions and not those" and 28 rows of the same answer hides it.
    """
    order = sorted(history, key=lambda name: (releases[name], name))
    out: list[str] = []
    for name in order:
        where = ", ".join(f"`{place}`" for place in sorted(history[name])) or "nowhere"
        major = releases[name]
        if out and out[-1][0] == where:
            out[-1] = (where, out[-1][1], major)
        else:
            out.append((where, major, major))
    parts = []
    for where, first, last in out:
        when = f"{first}" if first == last else f"{first} to {last}"
        parts.append(f"{where} at {when}")
    return "; ".join(parts)


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

    text, header_sha = source(tag, HEADER_PATH)
    header = constants(text)

    url = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    body = fetch(url)
    page = body.decode("utf-8", errors="replace")
    spec_sha = hashlib.sha256(body).hexdigest()
    index_says = check_the_index(spec_sha)
    spec = tables(page)
    shapes = structures(page)

    data = results()
    api = one_answer(data, "api")
    flags = one_answer(data, "flags")
    api_attributes = one_answer(data, "attributes")

    tags = summed(data, "tags")
    versions = summed(data, "versions")
    attribute_counts = summed(data, "attribute_counts")
    flag_counts = summed(data, "flag_counts")
    unknown = summed(data, "unknown_attributes")
    stray = {}
    for found in data.values():
        for key, entry in found["stray_flags"].items():
            here = stray.setdefault(key, {"count": 0, "examples": []})
            here["count"] += entry["count"]
            for name in entry["examples"]:
                if name not in here["examples"] and len(here["examples"]) < 5:
                    here["examples"].append(name)

    notes = verify(spec, shapes, header, flags, tags, attribute_counts, pinned, data)

    hashes = {
        f"`{HEADER_PATH}` at `{tag}`": header_sha,
        f"JVMS {edition} chapter {SPEC_CHAPTER}, which {index_says}": spec_sha,
    }

    out = build(pinned, spec, shapes, header, api, flags, api_attributes, data, tags,
                versions, attribute_counts, flag_counts, unknown, stray, notes, hashes)

    if args.show:
        print(f"{len(shapes['ClassFile'])} items in the ClassFile structure, "
              f"{len([r for r in spec['4.4-A'] if r and r[0].startswith('CONSTANT_')])} "
              f"constant pool tags, {len([r for r in spec['4.7-A'] if r])} attributes")
        print(f"{sum(tags.values()):,} pool entries counted over {len(data)} environments")
        print(f"{len(api_attributes)} attributes the class file API models, "
              f"{len(flags)} access flags this JDK knows")
        return 0

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_classfile.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != out:
            print(f"{OUTPUT} does not match its sources, run tools/gen_classfile.py",
                  file=sys.stderr)
            return 1
        print(f"{OUTPUT} matches all three sources")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
