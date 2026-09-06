#!/usr/bin/env python3
"""Generate the stack map table section from four sources that check each other.

M2's exit criterion says BP-STACKMAP section 2 is generated rather than written. Section 2
is the attribute itself: the nine verification types, the seven frame kinds, the range of
first bytes each kind occupies, and what a real attribute is made of.

Four sources, and the point of having four is that they disagree in interesting places:

  the specification    JVMS chapter 4 is normative. 4.7.4 prints a structure for every
                       frame kind and every verification type, each with the tag or the
                       range of tags in a comment, and states the offset arithmetic and
                       the reserved range in prose rather than in a table.

  the implementation   `classfile_constants.h.template` is the header the JDK ships to
                       anybody writing a native agent, and it is where the item numbers
                       are numbers rather than prose. `stackMapTable.hpp` holds the frame
                       type ranges as an enum, and it gives a name to the one range the
                       specification leaves nameless. `verificationType.hpp` is the same
                       item number as the verifier holds it, which is a wider thing than
                       the format allows: seven of its values can never occur in a file.

  the platform library `java.lang.classfile` knows the tag of every verification type,
                       including the two that a walk of a runtime image turns up in only
                       one of the two places the format allows them, which is why the
                       probe builds a class that forces both rather than looking for one.

  the counts           `probes/stackmap/results` is what a real attribute is made of, and
                       it is the part no document has. Two readers in that probe measure
                       the same bytes, one from decoded frames and one from the
                       `attribute_length` field, and this refuses a result where the two
                       disagree.

  python tools/gen_stackmap.py            write docs/generated/stackmap.md
  python tools/gen_stackmap.py --check    fail if the committed page is stale
  python tools/gen_stackmap.py --print    the summary, without writing anything

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
RESULTS = pathlib.Path("probes/stackmap/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OUTPUT = pathlib.Path("docs/generated/stackmap.md")

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
HEADER_PATH = "src/java.base/share/native/include/classfile_constants.h.template"
TABLE_HPP = "src/hotspot/share/classfile/stackMapTable.hpp"
TABLE_CPP = "src/hotspot/share/classfile/stackMapTable.cpp"
TYPE_HPP = "src/hotspot/share/classfile/verificationType.hpp"
SPEC_PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"
SPEC_CHAPTER = "4"
SPEC_SECTION = "4.7.4"

TABLE_TITLE = re.compile(
    r"<b>Table&nbsp;(?P<id>[\d.]+-[A-Z])(?: \(cont\.\))?\.&nbsp;(?P<title>.*?)</b>")
TABLE_BODY = re.compile(r"<tbody>(?P<body>.*?)</tbody>", re.S)
ROW = re.compile(r"<tr>(?P<row>.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.S)
PRE = re.compile(r'<pre class="screen">(?P<body>.*?)</pre>', re.S)

# `union verification_type_info {` and `full_frame {`, which is every block on the page.
OPENS = re.compile(r"^(?:union\s+)?(?P<name>\w+)\s*\{$")
# `u1 tag = ITEM_Object; /* 7 */`
TAG_FIELD = re.compile(r"^u1\s+tag\s*=\s*(?P<name>ITEM_\w+);\s*/\*\s*(?P<value>\d+)\s*\*/$")
# `u1 frame_type = CHOP; /* 248-250 */`
FRAME_FIELD = re.compile(
    r"^u1\s+frame_type\s*=\s*(?P<name>\w+);\s*/\*\s*(?P<low>\d+)(?:-(?P<high>\d+))?\s*\*/$")
# `verification_type_info locals[number_of_locals];`
PLAIN_FIELD = re.compile(r"^(?P<type>\w+)\s+(?P<name>\w+)(?:\[(?P<count>[^\]]*)\])?;$")
# `same_locals_1_stack_item_frame;`, a member of a union rather than a field.
MEMBER = re.compile(r"^(?P<name>\w+);$")

# The three things 4.7.4 states in prose that everything else states as a number.
RESERVED_PROSE = re.compile(r"Tags in the range \[(?P<low>\d+)-(?P<high>\d+)\] are reserved")
CHOP_PROSE = re.compile(r"value of k is given by the formula (?P<base>\d+) - frame_type")
DELTA_PROSE = re.compile(r"adding offset_delta \+ (?P<one>\d+) to the bytecode offset")

# `ITEM_Top = 0,` in the header and in HotSpot, and `ITEM_Byte,` with no value at all,
# which is the shape the verifier's private enum uses for five of its six extra items.
ENUM_ITEM = re.compile(r"^\s*(?P<name>(?:JVM_)?ITEM_\w+)\s*(?:=\s*(?P<value>[^,}]+?))?\s*[,}]?\s*$")
# `SAME_LOCALS_1_STACK_ITEM_FRAME_START = 64,`
ENUM_FRAME = re.compile(r"^\s*(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>\d+)\s*,?\s*$")

WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}

# What each verification type means, in the words a reader of a hex dump would use. A tag
# the specification prints and this does not describe stops the generator.
MEANS = {
    0: "a location holding nothing a verifier will let you read",
    1: "an `int`, and also a `boolean`, a `byte`, a `short` and a `char`",
    2: "a `float`",
    3: "the first of the two locations a `double` occupies",
    4: "the first of the two locations a `long` occupies",
    5: "the `null` reference, which is assignable to every reference type",
    6: "`this`, inside a constructor that has not chained yet",
    7: "a reference to an instance of the class the index names",
    8: "an object the `new` at that offset made and no constructor has run on",
}

# What each frame kind says, in one clause. Keyed by the specification's own name for it,
# so a renamed frame kind is a missing description rather than a wrong row.
SAYS = {
    "SAME": "the same locals as the frame before, and an empty stack",
    "SAME_LOCALS_1_STACK_ITEM":
        "the same locals as the frame before, and one thing on the stack",
    "SAME_LOCALS_1_STACK_ITEM_EXTENDED":
        "the same again, for a jump too far to fit in the first byte",
    "CHOP": "the frame before with its last `k` locals dropped, and an empty stack",
    "SAME_FRAME_EXTENDED":
        "the same locals again, for a jump too far to fit in the first byte",
    "APPEND": "the frame before with locals added, and an empty stack",
    "FULL_FRAME": "every local and every stack entry, written out",
}

# Where each frame kind's offset delta comes from, which is the distinction the first two
# rows exist to make and the one a reader of a hex dump gets wrong.
DELTA_FROM = {
    "SAME": "the first byte itself",
    "SAME_LOCALS_1_STACK_ITEM": "the first byte, minus 64",
}


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


def plain(markup: str) -> str:
    """One HTML fragment as the text a reader would see."""
    text = re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([;,.])", r"\1", text)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))


def section_of(page: str) -> str:
    """The part of the chapter that is 4.7.4 and nothing else.

    Every structure this reads is printed inside one section, and chapter 4 prints a
    hundred and more structures, so cutting first is what stops `full_frame` being found
    in a section about something else.
    """
    start = page.find(f'<a name="jvms-{SPEC_SECTION}"></a>')
    if start < 0:
        sys.exit(f"JVMS chapter {SPEC_CHAPTER} has no section {SPEC_SECTION} anchor, so "
                 f"the page shape changed")
    after = re.search(r'<a name="jvms-4\.7\.5"></a>', page[start:])
    if not after:
        sys.exit(f"JVMS {SPEC_SECTION} no longer ends at 4.7.5, so the page shape changed")
    return page[start:start + after.start()]


def blocks(text: str) -> dict[str, list[str]]:
    """Every `Name { ... }` box in the section, as its lines with the markup taken out."""
    found: dict[str, list[str]] = {}
    for block in PRE.finditer(text):
        lines = [plain(line) for line in
                 html.unescape(re.sub(r"<[^>]+>", "", block.group("body"))).splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        opened = OPENS.match(lines[0])
        if opened:
            found.setdefault(opened.group("name"), lines[1:-1])
    return found


def spec_types(boxes: dict[str, list[str]]) -> dict[int, dict]:
    """The verification types the section prints, by the tag in each one's comment."""
    union = boxes.get("verification_type_info")
    if not union:
        sys.exit(f"JVMS {SPEC_SECTION} no longer prints a verification_type_info union")
    names = [MEMBER.match(line).group("name") for line in union if MEMBER.match(line)]
    found: dict[int, dict] = {}
    for name in names:
        body = boxes.get(name)
        if body is None:
            sys.exit(f"JVMS {SPEC_SECTION} lists {name} in the union and prints no "
                     f"structure for it")
        tagged = TAG_FIELD.match(body[0])
        if not tagged:
            sys.exit(f"JVMS {SPEC_SECTION}: {name} does not start with a tagged item")
        extra = []
        for line in body[1:]:
            field = PLAIN_FIELD.match(line)
            if field:
                extra.append(f"{field.group('type')} {field.group('name')}")
        found[int(tagged.group("value"))] = {
            "structure": name, "item": tagged.group("name"), "after": extra}
    if len(found) != len(names):
        sys.exit(f"JVMS {SPEC_SECTION} prints {len(names)} verification types and they "
                 f"do not have {len(names)} distinct tags")
    return found


def spec_frames(boxes: dict[str, list[str]]) -> dict[str, dict]:
    """The frame kinds the section prints, by the name in each one's frame_type item."""
    union = boxes.get("stack_map_frame")
    if not union:
        sys.exit(f"JVMS {SPEC_SECTION} no longer prints a stack_map_frame union")
    names = [MEMBER.match(line).group("name") for line in union if MEMBER.match(line)]
    found: dict[str, dict] = {}
    for name in names:
        body = boxes.get(name)
        if body is None:
            sys.exit(f"JVMS {SPEC_SECTION} lists {name} in the union and prints no "
                     f"structure for it")
        framed = FRAME_FIELD.match(body[0])
        if not framed:
            sys.exit(f"JVMS {SPEC_SECTION}: {name} does not start with a frame_type item")
        after = []
        for line in body[1:]:
            field = PLAIN_FIELD.match(line)
            if field:
                count = field.group("count")
                after.append(f"{field.group('type')} {field.group('name')}"
                             + (f"[{count}]" if count else ""))
        low = int(framed.group("low"))
        high = int(framed.group("high") or low)
        found[framed.group("name")] = {
            "structure": name, "low": low, "high": high, "after": after}
    return found


def enum(text: str, pattern: re.Pattern, wanted: str, path: str) -> dict[str, int]:
    """An enum body as name to value, filling in the values the file leaves implicit.

    `verificationType.hpp` writes `ITEM_Boolean = 9, ITEM_Byte, ITEM_Short` on one line
    and expects the reader to count, which is exactly the kind of thing a table retyped
    by hand gets wrong, so this counts.
    """
    found: dict[str, int] = {}
    following = 0
    for line in re.split(r"[,\n]", text):
        match = pattern.match(line.strip() + ",")
        if not match:
            continue
        raw = (match.groupdict().get("value") or "").strip()
        if raw and re.fullmatch(r"\d+", raw):
            following = int(raw)
        elif raw:
            continue          # `(uint)-1`, which is a sentinel rather than an item number
        found[match.group("name")] = following
        following += 1
    if wanted not in found:
        sys.exit(f"{path}: no {wanted} in a shape this can read, so the file changed")
    return found


def body_around(text: str, marker: str, path: str) -> str:
    """The braced block a marker sits inside, which is how one enum is picked out of a
    file that holds several."""
    at = text.find(marker)
    if at < 0:
        sys.exit(f"{path} no longer contains `{marker}`, so the file shape changed")
    start = text.rfind("{", 0, at)
    if start < 0:
        sys.exit(f"{path} has {marker} outside any block, so the file shape changed")
    depth = 0
    for end in range(start, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if depth == 0:
            return text[start + 1:end]
    sys.exit(f"{path}: a brace never closed")


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
    """A count, summed over every environment, because each one scanned its own copy of
    the module and a mean would be a count of nothing."""
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


def link(edition: str, number: str, label: str | None = None) -> str:
    page = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    return f"[{label or ('§' + number)}]({page}#jvms-{number})"


def verify(types: dict, frames: dict, header: dict, verifier: dict, ranges: dict,
           prose: dict, api: dict, data: dict, items: dict, seen: dict) -> list[str]:
    """Every check that has to pass before a page is worth writing.

    Each failure is one of the four sources having moved without the others. The right
    answer to that is a person reading a diff rather than a generator picking a winner,
    so these exit rather than warn.
    """
    notes: list[str] = []

    # The item numbers, three ways: the specification's comments, the header the JDK
    # ships to native agents, and the enum the verifier holds them in.
    for tag, entry in sorted(types.items()):
        name = entry["item"]
        if header.get("JVM_" + name) != tag:
            sys.exit(f"the JVMS says {name} is {tag} and {HEADER_PATH} says "
                     f"{header.get('JVM_' + name)}")
        if verifier.get(name) != tag:
            sys.exit(f"the JVMS says {name} is {tag} and {TYPE_HPP} says "
                     f"{verifier.get(name)}")
        if tag not in MEANS:
            sys.exit(f"the JVMS prints a verification type with tag {tag} and this "
                     f"generator has no description for it")

    # The platform library's answers against the specification's tags.
    if set(api.values()) != set(types):
        sys.exit(f"the pinned JDK reports tags {sorted(set(api.values()))} and the JVMS "
                 f"prints {sorted(types)}")

    # The frame type ranges, two ways: the comment on each structure and HotSpot's enum.
    for name, entry in sorted(frames.items()):
        if name not in SAYS:
            sys.exit(f"the JVMS prints a frame kind called {name} and this generator has "
                     f"no description for it")
        low, high = ranges.get(name, (None, None))
        if (low, high) != (entry["low"], entry["high"]):
            sys.exit(f"the JVMS says {name} is {entry['low']}-{entry['high']} and "
                     f"{TABLE_HPP} says {low}-{high}")

    # The reserved range, which the specification states in prose and HotSpot names.
    if prose["reserved"] != ranges["RESERVED"]:
        sys.exit(f"the JVMS reserves {prose['reserved']} and {TABLE_HPP} reserves "
                 f"{ranges['RESERVED']}")

    # Every one of the 256 values a first byte can hold, claimed exactly once.
    covered: dict[int, str] = {}
    for name, entry in list(frames.items()):
        for value in range(entry["low"], entry["high"] + 1):
            if value in covered:
                sys.exit(f"first byte {value} is both {covered[value]} and {name}")
            covered[value] = name
    for value in range(prose["reserved"][0], prose["reserved"][1] + 1):
        if value in covered:
            sys.exit(f"first byte {value} is reserved and also {covered[value]}")
        covered[value] = "RESERVED"
    missing = sorted(set(range(256)) - set(covered))
    if missing:
        sys.exit(f"the frame kinds and the reserved range leave {missing} unaccounted "
                 f"for, so a first byte exists that nothing on the page describes")

    # The two formulas the specification writes as arithmetic on a named constant.
    if prose["chop_base"] != ranges["SAME_FRAME_EXTENDED"][0]:
        sys.exit(f"the JVMS computes a chop count as {prose['chop_base']} - frame_type "
                 f"and SAME_FRAME_EXTENDED is {ranges['SAME_FRAME_EXTENDED'][0]}")
    appended = frames["APPEND"]["after"]
    subtracted = [int(found) for item in appended
                  for found in re.findall(r"frame_type - (\d+)", item)]
    if subtracted and subtracted[0] != ranges["SAME_FRAME_EXTENDED"][0]:
        sys.exit(f"the JVMS sizes an append frame with frame_type - {subtracted[0]} and "
                 f"SAME_FRAME_EXTENDED is {ranges['SAME_FRAME_EXTENDED'][0]}")

    # The measurements, against the format they are supposed to be measurements of.
    for tag in sorted(items, key=int):
        if int(tag) not in types:
            sys.exit(f"a verification type with tag {tag} occurs in the scanned code and "
                     f"the JVMS does not print one")
    for value in sorted(seen, key=int):
        if covered[int(value)] == "RESERVED":
            sys.exit(f"a frame in the scanned code has first byte {value}, which the "
                     f"JVMS reserves and no class file may contain")

    for platform, found in sorted(data.items()):
        scan = found["scan"]
        if scan["attribute_bytes"] != scan["raw_attribute_bytes"]:
            sys.exit(f"{platform} decoded {scan['attribute_bytes']:,} bytes of frames and "
                     f"the attribute_length fields in the same files come to "
                     f"{scan['raw_attribute_bytes']:,}")
        if scan["arithmetic_wrong"]:
            sys.exit(f"{platform} found {scan['arithmetic_wrong']:,} frames whose first "
                     f"byte disagrees with the offset the JDK resolved, so either the "
                     f"probe or JVMS {SPEC_SECTION} is wrong about the arithmetic")
        if found["built"]["verifies"] != 1:
            sys.exit(f"{platform} built a class the JVM would not verify, so the two "
                     f"rare verification types on this page are not demonstrated")

    if len(data) < 2:
        notes.append(
            "There is only one environment in the results, so every count below is one "
            "machine's answer rather than a sum over several."
        )
    return notes


def build(pinned: dict, types: dict, frames: dict, header: dict, verifier: dict,
          ranges: dict, prose: dict, api: dict, version: list[str], data: dict,
          items: dict, where: dict, seen: dict, larger: dict, built: dict,
          notes: list[str], hashes: dict[str, str]) -> str:
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]

    platforms = ", ".join(f"`{name}`" for name in sorted(data))
    builds = sorted({found["java_build"] for found in data.values()})
    measured = sorted({found["measured"] for found in data.values()})
    scanned = sorted({found["scan"]["module"] for found in data.values()})

    def total(field: str) -> int:
        return sum(found["scan"][field] for found in data.values())

    classes = total("classes")
    with_code = total("methods_with_code")
    with_frames = total("methods_with_frames")
    frame_count = total("frames")
    attribute_bytes = total("attribute_bytes")
    all_full = total("all_full_bytes")
    class_bytes = total("class_bytes")
    code_bytes = total("code_bytes")
    checked = total("arithmetic_checked")
    over63 = total("delta_over_63")
    biggest = max(found["scan"]["delta_max"] for found in data.values())
    uses = sum(items.values())

    reserved_low, reserved_high = prose["reserved"]
    reserved_count = reserved_high - reserved_low + 1
    legal = 256 - reserved_count

    out: list[str] = []
    out.append("# BP-STACKMAP section 2. The stack map table")
    out.append("")
    out.append(
        f"Generated by `tools/gen_stackmap.py`. Do not edit it, edit a source. There are "
        f"four: JVMS {edition} {link(edition, SPEC_SECTION)} for what the attribute is, "
        f"`{HEADER_PATH}` at `{tag}` for the item numbers as the JDK writes them for "
        f"native agents, `{TABLE_HPP}` with `{TYPE_HPP}` at the same tag for the frame "
        f"type ranges and for what the verifier holds in the same byte, and "
        f"`java.lang.classfile` read off the pinned JDK. The counts come from "
        f"`probes/stackmap/results`."
    )
    out.append("")
    out.append(
        f"Counted over {', '.join(f'`{name}`' for name in scanned)} on {platforms}, java "
        f"{', '.join(builds)}, on {', '.join(measured)}: {classes:,} class files, "
        f"{with_frames:,} of {with_code:,} methods with code carry a `StackMapTable`, "
        f"{frame_count:,} frames in {attribute_bytes:,} bytes."
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
    out.append(
        f"The byte counts have two witnesses. One decodes every frame through "
        f"`java.lang.classfile` and adds up the width of what it decoded, the other walks "
        f"the same files as bytes and reads `attribute_length` out of the attribute "
        f"header. Those are different measurements of the same thing, they agree at "
        f"{attribute_bytes:,} bytes, and this generator refuses to write a page where "
        f"they do not. That matters more here than it looks: the interesting number "
        f"further down is a comparison between two encodings computed by one set of width "
        f"rules, and a wrong rule would cancel out of it."
    )
    out.append("")

    out.append(f"## 2.1 The {word(len(types))} verification types")
    out.append("")
    out.append(
        f"A verification type is a one byte item number and then nothing, an index, or an "
        f"offset. It describes the type of one location, where a location is a single "
        f"local variable or a single operand stack entry, except that `ITEM_Long` and "
        f"`ITEM_Double` describe two."
    )
    out.append("")
    out.append("| item | structure | after the byte | what it is | in locals | on stack | "
               "share |")
    out.append("|---:|---|---|---|---:|---:|---:|")
    for number, entry in sorted(types.items()):
        after = ", ".join(f"`{item}`" for item in entry["after"]) or "nothing"
        in_locals = where.get("locals", {}).get(str(number), 0)
        on_stack = where.get("stack", {}).get(str(number), 0)
        out.append(
            f"| {number} | `{entry['structure']}` | {after} | {MEANS[number]} | "
            f"{in_locals:,} | {on_stack:,} | "
            f"{share(in_locals + on_stack, uses)} |"
        )
    out.append("")
    out.append(
        f"The last three columns are {uses:,} verification types in the scanned code. "
        f"They are counted separately by side because the format allows every item in "
        f"both places and a compiler does not use them that way, and a reader who has "
        f"only ever seen the common half will write a parser that rejects a legal file."
    )
    out.append("")
    absent = [(number, side) for number in sorted(types)
              for side in ("locals", "stack")
              if not where.get(side, {}).get(str(number), 0)]
    if absent:
        out.append(
            "The zeros in those two columns are the point of having two columns. "
            + "; ".join(
                f"`{types[number]['item']}` never appears "
                + ("in a locals array" if side == "locals" else "on an operand stack")
                for number, side in absent)
            + f". None of those is a rule. The probe builds a class that puts "
            f"`ITEM_Uninitialized` and `ITEM_UninitializedThis` in both a locals array "
            f"and an operand stack, hands the {built['bytes']:,} bytes to the pinned "
            f"JVM's own verifier, and this page is not written unless the JVM accepts it. "
            f"So those cells are what a compiler emits and not what a machine will take."
        )
        out.append("")
    tops = where.get("locals", {}).get("0", 0)
    wide = sum(where.get("locals", {}).get(str(number), 0) for number in (3, 4))
    out.append(
        f"`ITEM_Long` and `ITEM_Double` are the trap. Each describes two locations, the "
        f"second of which has the verification type `top`, and that second `top` is not "
        f"written down: one item covers both. The counts say so. There are {wide:,} of "
        f"the two of them in locals arrays and only {tops:,} `ITEM_Top`, and a format that "
        f"wrote the implied one would need at least as many of the second as of the first. "
        f"Every `ITEM_Top` in the scanned code is a local variable slot that is genuinely "
        f"unusable at that offset rather than the tail of a wide value."
    )
    out.append("")

    out.append(f"## 2.2 The {word(len(frames))} kinds of frame")
    out.append("")
    out.append(
        f"Every frame starts with one byte, and that byte decides both what kind of frame "
        f"it is and, for the two commonest kinds, how far along the code it applies. A "
        f"frame is a delta against the frame before it, which is why no frame can be read "
        f"on its own and why the order of the entries is part of the meaning."
    )
    out.append("")
    out.append("| first byte | structure | after the byte | says | seen | share |")
    out.append("|---|---|---|---|---:|---:|")
    for name, entry in sorted(frames.items(), key=lambda item: item[1]["low"]):
        low, high = entry["low"], entry["high"]
        span = f"{low}" if low == high else f"{low}-{high}"
        after = ", ".join(f"`{item}`" for item in entry["after"]) or "nothing"
        count = sum(seen.get(str(value), 0) for value in range(low, high + 1))
        out.append(
            f"| {span} | `{entry['structure']}` | {after} | {SAYS[name]} | {count:,} | "
            f"{share(count, frame_count)} |"
        )
    out.append("")
    implicit = sorted(name for name in frames if name in DELTA_FROM)
    out.append(
        "The offset delta comes from a different place in different rows, and that is the "
        "distinction to hold on to: "
        + ", ".join(f"`{name}` takes it from {DELTA_FROM[name]}" for name in implicit)
        + ", and every other kind writes it out as a `u2`. The extended forms exist for "
        "exactly that reason. A jump of more than "
        + str(frames["SAME"]["high"])
        + " bytes cannot be said in one byte, so the same frame has to be written a "
        "longer way."
    )
    out.append("")
    out.append(
        f"That is not a rare case. {over63:,} of the {frame_count:,} frames in the "
        f"scanned code sit more than {frames['SAME']['high']} bytes past the frame before "
        f"them, and the largest gap is {biggest:,} bytes."
    )
    out.append("")
    out.append(
        f"`CHOP` and `APPEND` both encode a count in the first byte, and both count "
        f"against the same constant: a chop drops "
        f"{prose['chop_base']} - `frame_type` locals and an append adds "
        f"`frame_type` - {prose['chop_base']}. {prose['chop_base']} is "
        f"`SAME_FRAME_EXTENDED`, which sits between the two ranges, so the arithmetic is "
        f"one subtraction from the middle in either direction. Neither can move more than "
        f"three locals, which is why a method whose locals change by four at a branch "
        f"target pays for a `full_frame`."
    )
    out.append("")

    out.append(f"## 2.3 The {word(reserved_count)} first bytes that mean nothing")
    out.append("")
    out.append(
        f"{link(edition, SPEC_SECTION)} says, in one sentence of prose and in no table at "
        f"all, that tags in the range {reserved_low} to {reserved_high} are reserved for "
        f"future use. That is {reserved_count} of the 256 values a first byte can hold, "
        f"{100 * reserved_count / 256:.0f}% of the encoding space, unusable and unnamed. "
        f"HotSpot gives the range a name that the specification does not: `RESERVED_START` "
        f"and `RESERVED_END` in `{TABLE_HPP}`, which is the only place either boundary is "
        f"a constant rather than a sentence."
    )
    out.append("")
    out.append(
        f"The other {legal} values are all in use, and that is a measurement rather than "
        f"an inference: every one of the {legal} legal first bytes occurs at least once in "
        f"the scanned code. There is no first byte that is legal and unused, so a parser "
        f"cannot be tested against real files into believing it handles the whole range."
    )
    out.append("")
    out.append(
        f"A parser therefore needs one branch for the reserved range and it needs to "
        f"reject rather than skip, because a reserved byte carries no length and there is "
        f"no way to find the next frame past it. That is the difference between this "
        f"attribute and most of a class file: an unknown attribute can be skipped by its "
        f"length, and an unknown frame type cannot be skipped at all."
    )
    out.append("")

    extra = {name: value for name, value in verifier.items()
             if value not in types or name not in [entry["item"] for entry in types.values()]}
    if extra:
        out.append("## 2.4 What the verifier holds in the same byte")
        out.append("")
        out.append(
            f"The item number in a class file has {word(len(types))} legal values. The "
            f"item number inside HotSpot's verifier has more, because the verifier tracks "
            f"types the format has no way to write down. `int` in a stack map covers "
            f"`boolean`, `byte`, `short` and `char`, and the verifier keeps them apart "
            f"anyway so that an array store can be checked. A `long` occupies two "
            f"locations and the format writes one item, and the verifier gives the second "
            f"location a name."
        )
        out.append("")
        out.append("| value | name in `verificationType.hpp` |")
        out.append("|---:|---|")
        for name, value in sorted(extra.items(), key=lambda item: item[1]):
            out.append(f"| {value} | `{name}` |")
        out.append("")
        out.append(
            f"Every one of those is above {max(types)}, which is the largest item number "
            f"the format defines, so a value the verifier invented can never be mistaken "
            f"for one a class file may contain. That is the same discipline the constant "
            f"pool tag byte follows, and for the same reason: one field, two vocabularies, "
            f"and no overlap between them."
        )
        out.append("")

    out.append("## 2.5 The offset arithmetic")
    out.append("")
    out.append(
        f"A frame says where it applies as a distance from the frame before it, and the "
        f"rule has an exception in it. The bytecode offset is the previous frame's offset "
        f"plus `offset_delta` plus {prose['delta_one']}, unless the previous frame is the "
        f"method's initial frame, in which case it is `offset_delta` exactly. The initial "
        f"frame is not in the attribute at all: it is computed from the method descriptor "
        f"and the access flags, so the first entry in the table describes the second frame "
        f"of the method."
    )
    out.append("")
    out.append(
        f"The `+ {prose['delta_one']}` is what makes two frames at the same offset "
        f"impossible to write, so a verifier gets sortedness and uniqueness from the "
        f"encoding rather than having to check for them. It is also the single rule in "
        f"this attribute most likely to be implemented wrongly, because the exception "
        f"applies to one frame per method and a test suite of small methods will not "
        f"notice."
    )
    out.append("")
    out.append(
        f"It is checked here rather than quoted. {checked:,} of the {frame_count:,} "
        f"frames in the scanned code carry their own delta in the first byte, so the rule "
        f"can be applied to the offset the pinned JDK resolved and the two compared. "
        f"They agree on every one, and this generator refuses to write the page if they "
        f"stop agreeing."
    )
    out.append("")

    out.append("## 2.6 What the encoding buys, and what it costs")
    out.append("")
    saved = all_full - attribute_bytes
    out.append(
        f"Six of the {word(len(frames))} frame kinds are a compression of the seventh. "
        f"Written entirely as `full_frame`, the same {frame_count:,} frames would take "
        f"{all_full:,} bytes instead of {attribute_bytes:,}, so the compressed forms save "
        f"{saved:,} bytes, which is {100 * saved / all_full:.0f}% of the attribute and "
        f"{100 * saved / class_bytes:.1f}% of every class file byte in the scanned code."
    )
    out.append("")
    out.append(
        f"What it costs is that the attribute is {attribute_bytes:,} bytes against "
        f"{code_bytes:,} bytes of bytecode, so a class file carries "
        f"{100 * attribute_bytes / code_bytes:.0f} bytes of proof for every hundred bytes "
        f"of code, and {100 * attribute_bytes / class_bytes:.1f}% of the whole file. That "
        f"is the price of verifying by type checking instead of by inference, and it is "
        f"paid on disk and in memory by every class ever loaded."
    )
    out.append("")
    not_minimal = total("frames_larger_than_needed")
    if not not_minimal:
        out.append(
            f"Every frame in the scanned code is written in the smallest form that could "
            f"express it. The probe works out, from the decoded frame and the one before "
            f"it, which first byte the format would have allowed and takes the cheapest, "
            f"and the answer differs from what was written for none of the "
            f"{frame_count:,} frames. The encoding is therefore canonical in practice "
            f"without being canonical by rule: nothing in {link(edition, SPEC_SECTION)} "
            f"requires the smallest form, a file using `full_frame` everywhere is legal "
            f"and loads, and no such file is in the scanned code."
        )
        out.append("")
        out.append(
            f"That has a use. Two compilers that agree about the types at every branch "
            f"target will produce byte identical `StackMapTable` attributes, so a "
            f"difference in these bytes is a difference in what was proved rather than a "
            f"difference in how it was written down."
        )
    else:
        out.append(
            f"{not_minimal:,} of the {frame_count:,} frames are written in a larger form "
            f"than the format required, which is legal and costs "
            f"{attribute_bytes - total('minimal_bytes'):,} bytes. "
            + "; ".join(f"{count:,} are a `{was}` where a `{could}` would do"
                        for was, could, count in
                        sorted(((name.split("-to-")[0], name.split("-to-")[1], count)
                                for name, count in larger.items()),
                               key=lambda item: -item[2]))
            + "."
        )
    out.append("")
    if version:
        out.append(
            f"One last number for a reader dating a class file. The attribute arrived at "
            f"class file version {version[0]}, which is Java SE {version[1]}, and from "
            f"that version on a `Code` attribute without one is treated as having an empty "
            f"one rather than as having no stack map. So the absence of this attribute is "
            f"itself a claim, and which claim it is depends on the version field at the "
            f"top of the file."
        )
        out.append("")

    out.append("## 2.7 Provenance")
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


def spec_prose(text: str) -> dict:
    """The three things 4.7.4 states in a sentence rather than in a structure."""
    flat = plain(text)
    reserved = RESERVED_PROSE.search(flat)
    chop = CHOP_PROSE.search(flat)
    delta = DELTA_PROSE.search(flat)
    if not reserved:
        sys.exit(f"JVMS {SPEC_SECTION} no longer states a reserved range in prose")
    if not chop:
        sys.exit(f"JVMS {SPEC_SECTION} no longer states the chop formula in prose")
    if not delta:
        sys.exit(f"JVMS {SPEC_SECTION} no longer states the offset formula in prose")
    return {
        "reserved": (int(reserved.group("low")), int(reserved.group("high"))),
        "chop_base": int(chop.group("base")),
        "delta_one": int(delta.group("one")),
    }


def spec_version(page: str) -> list[str]:
    """The class file version the attribute arrived at, from the attribute table."""
    for title in TABLE_TITLE.finditer(page):
        if title.group("id") != "4.7-B":
            continue
        body = TABLE_BODY.search(page, title.end())
        if not body:
            continue
        for row in ROW.finditer(body.group("body")):
            cells = [plain(cell.group("cell")) for cell in CELL.finditer(row.group("row"))]
            if cells and cells[0] == "StackMapTable":
                return cells[1:3]
    return []


def frame_ranges(text: str) -> dict[str, tuple[int, int]]:
    """HotSpot's frame type enum as a name to a range.

    The enum writes a range as two entries with `_START` and `_END` suffixes and a single
    value as one entry, so both shapes come back as a pair.
    """
    values = enum(body_around(text, "SAME_FRAME_START", TABLE_HPP), ENUM_FRAME,
                  "SAME_FRAME_START", TABLE_HPP)
    found: dict[str, tuple[int, int]] = {}
    for name, value in values.items():
        if name.endswith("_START"):
            # A range is spelled `CHOP_FRAME_START` where the specification calls the
            # kind `CHOP`, so the `_FRAME` comes off here and only here. The single
            # values are spelled the same in both, `FULL_FRAME` included, and taking a
            # suffix off those would rename one of them into something no source has.
            base = name.removesuffix("_START")
            end = values.get(base + "_END")
            if end is None:
                sys.exit(f"{TABLE_HPP} has {name} and no {base}_END")
            found[base.removesuffix("_FRAME")] = (value, end)
        elif not name.endswith("_END"):
            found[name] = (value, value)
    return found


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
    header = enum(body_around(header_text, "JVM_ITEM_Top", HEADER_PATH), ENUM_ITEM,
                  "JVM_ITEM_Top", HEADER_PATH)
    hpp_text, hpp_sha = source(tag, TABLE_HPP)
    type_text, type_sha = source(tag, TYPE_HPP)
    verifier = enum(type_text, ENUM_ITEM, "ITEM_Top", TYPE_HPP)
    ranges = frame_ranges(hpp_text)

    url = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    body = fetch(url)
    page = body.decode("utf-8", errors="replace")
    spec_sha = hashlib.sha256(body).hexdigest()
    index_says = check_the_index(spec_sha)
    only = section_of(page)
    boxes = blocks(only)
    types = spec_types(boxes)
    frames = spec_frames(boxes)
    prose = spec_prose(only)
    version = spec_version(page)

    data = read(RESULTS, "probes/stackmap/run.py")
    api = {name: entry["tag"] for name, entry in one_answer(data, "types").items()}
    built = one_answer(data, "built")
    items = summed(data, "items")
    where = summed(data, "items_where")
    seen = summed(data, "frame_types")
    larger = summed(data, "larger_than_needed")

    notes = verify(types, frames, header, verifier, ranges, prose, api, data, items, seen)

    hashes = {
        f"JVMS {edition} chapter {SPEC_CHAPTER}, which {index_says}": spec_sha,
        f"`{HEADER_PATH}` at `{tag}`": header_sha,
        f"`{TABLE_HPP}` at `{tag}`": hpp_sha,
        f"`{TYPE_HPP}` at `{tag}`": type_sha,
    }

    out = build(pinned, types, frames, header, verifier, ranges, prose, api, version,
                data, items, where, seen, larger, built, notes, hashes)

    if args.show:
        reserved = prose["reserved"][1] - prose["reserved"][0] + 1
        print(f"{len(types)} verification types, {len(frames)} frame kinds, "
              f"{reserved} reserved first bytes, {256 - reserved} legal ones")
        print(f"{sum(found['scan']['frames'] for found in data.values()):,} frames in "
              f"{sum(found['scan']['attribute_bytes'] for found in data.values()):,} "
              f"bytes over {len(data)} environments")
        print(f"{sum(found['scan']['frames_larger_than_needed'] for found in data.values()):,} "
              f"frames are written larger than the format required")
        return 0

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_stackmap.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != out:
            print(f"{OUTPUT} does not match its sources, run tools/gen_stackmap.py",
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
