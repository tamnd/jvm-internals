#!/usr/bin/env python3
"""Generate the bytecode table from three sources that disagree with each other.

M2's exit criterion says BP-BYTECODE section 3 is generated rather than written. An
opcode table is the single most copied artifact in this subject and almost every copy
of it is a transcription of a transcription, which is why so many of them still list
`jsr` without saying that no compiler has emitted one since 1.4.

There are three places an opcode is defined and they are not the same place:

  the specification    JVMS chapter 7 is the normative list. 205 opcodes, by number
                       and mnemonic, and nothing else. It says what a class file is
                       allowed to contain.

  the implementation   `src/hotspot/share/interpreter/bytecodes.cpp` is what HotSpot
                       has. It defines more codes than the specification does, because
                       a running interpreter rewrites bytecode into forms no class file
                       may contain, and it carries the four facts the specification
                       leaves to prose: the operand format, the wide form, the stack
                       depth change and whether the instruction can trap.

  the platform library `java.lang.classfile.Opcode` is what the JDK's own class file
                       API believes. It has neither the number of constants the
                       specification has nor the number HotSpot has, and the way it
                       differs from both is a fact about how `wide` really works.

This joins all three, refuses to write anything if they disagree in a way that is not
already understood and named below, and prints the disagreements it does understand as
part of the output. A generated table whose sources silently drifted would be worse
than a hand written one, because nobody would think to check it.

  python tools/gen_opcodes.py            write docs/generated/opcodes.md
  python tools/gen_opcodes.py --check    fail if the committed table is stale
  python tools/gen_opcodes.py --print    the summary, without writing anything

The HotSpot sources come from raw.githubusercontent.com at the tag in `docs/pin.json`,
or from a local checkout when `JVX_JDK_SRC` points at one. The specification chapter
comes from docs.oracle.com at the pinned edition, and its hash is checked against the
one `tools/gen_jvms_index.py` recorded, so a chapter that was rewritten underneath us
stops this rather than quietly changing the table.
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
RESULTS = pathlib.Path("probes/opcodes/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OUTPUT = pathlib.Path("docs/generated/opcodes.md")

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
HEADER_PATH = "src/hotspot/share/interpreter/bytecodes.hpp"
TABLE_PATH = "src/hotspot/share/interpreter/bytecodes.cpp"
SPEC_PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"
SPEC_CHAPTER = "7"
INSTRUCTIONS_CHAPTER = "6"

# `_nop                  =   0, // 0x00` and `_fast_bgetfield       ,`. Both forms
# appear and the second one means "one more than the last". Matched after the comment
# and the trailing comma have been taken off, so the pattern has only the two shapes
# in it rather than the punctuation around them.
ENUM_ENTRY = re.compile(r"^(?P<name>\w+)(?:\s*=\s*(?P<value>.+))?$")

# One row of BYTECODES_DO, which is the same shape in both the standard block and the
# HotSpot-only block above it.
DEF = re.compile(
    r'^\s*def\(\s*(?P<code>\w+)\s*,\s*"(?P<name>[^"]*)"\s*,\s*'
    r'(?P<format>"[^"]*"|nullptr)\s*,\s*(?P<wide>"[^"]*"|nullptr)\s*,\s*'
    r'(?P<result>\w+)\s*,\s*(?P<depth>-?\d+)\s*,\s*(?P<trap>true|false)\s*,\s*'
    r'(?P<java>\w+)\s*\)'
)

# `<code class="literal">&nbsp;96&nbsp;(0x60)</code>&nbsp;&nbsp;&nbsp;&nbsp;iadd`. The
# leading padding is there so the numbers line up in a fixed width column, and a regex
# that does not allow for it silently loses the four two-digit opcodes that need it.
SPEC_ROW = re.compile(
    r'<code class="literal">(?:&nbsp;)*(?P<number>\d+)&nbsp;\(0x(?P<hex>[0-9a-f]{2})\)'
    r'</code>(?:&nbsp;)+(?P<name>[a-z0-9_]+)'
)

# The anchors in chapter 6, one per instruction description. The specification folds
# families, so `aload_0` through `aload_3` are all described under `aload_n`, and a
# table that links every opcode to its section has to know the folding rule.
SPEC_ANCHOR = re.compile(r'jvms-6\.5\.(?P<name>[a-zA-Z0-9_]+)"')

# The folding rule, as the specification applies it. Every entry here is checked
# against the anchors actually present on the page, so a renamed section is a hard stop
# rather than a dead link in a generated table.
FAMILIES = {
    "iconst_i": ["iconst_m1", "iconst_0", "iconst_1", "iconst_2", "iconst_3",
                 "iconst_4", "iconst_5"],
    "lconst_l": ["lconst_0", "lconst_1"],
    "fconst_f": ["fconst_0", "fconst_1", "fconst_2"],
    "dconst_d": ["dconst_0", "dconst_1"],
    "iload_n": ["iload_0", "iload_1", "iload_2", "iload_3"],
    "lload_n": ["lload_0", "lload_1", "lload_2", "lload_3"],
    "fload_n": ["fload_0", "fload_1", "fload_2", "fload_3"],
    "dload_n": ["dload_0", "dload_1", "dload_2", "dload_3"],
    "aload_n": ["aload_0", "aload_1", "aload_2", "aload_3"],
    "istore_n": ["istore_0", "istore_1", "istore_2", "istore_3"],
    "lstore_n": ["lstore_0", "lstore_1", "lstore_2", "lstore_3"],
    "fstore_n": ["fstore_0", "fstore_1", "fstore_2", "fstore_3"],
    "dstore_n": ["dstore_0", "dstore_1", "dstore_2", "dstore_3"],
    "astore_n": ["astore_0", "astore_1", "astore_2", "astore_3"],
    "lcmp": ["lcmp"],
    "fcmp_op": ["fcmpl", "fcmpg"],
    "dcmp_op": ["dcmpl", "dcmpg"],
    "if_cond": ["ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle"],
    "if_icmp_cond": ["if_icmpeq", "if_icmpne", "if_icmplt", "if_icmpge",
                     "if_icmpgt", "if_icmple"],
    "if_acmp_cond": ["if_acmpeq", "if_acmpne"],
}

# The three the specification lists in chapter 7 and forbids in a class file. They are
# in the table for the same reason a map shows the edge of the world: a reader who
# meets one wants to know it is not theirs.
RESERVED = {
    202: "reserved for a debugger's breakpoint, and HotSpot really does write it",
    254: "reserved for an implementation to use, and HotSpot does not",
    255: "reserved for an implementation to use, and HotSpot does not",
}

# The prefix byte the class file API folds into the opcode value of a wide form.
WIDE_PREFIX = 196

# Small counts read better as words in a sentence, and every count in this file that
# lands in a sentence is small. Anything outside the range falls back to the numeral.
WORDS = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}


def word(count: int) -> str:
    return WORDS.get(count, str(count))

# A HotSpot type name to something a reader recognises. T_ILLEGAL is not a mistake in
# the table: it is what the file says for every instruction whose result type depends
# on the descriptor it resolves, which is most of the interesting ones.
RESULT_TYPE = {
    "T_VOID": "void", "T_INT": "int", "T_LONG": "long", "T_FLOAT": "float",
    "T_DOUBLE": "double", "T_OBJECT": "reference", "T_ARRAY": "array",
    "T_BOOLEAN": "boolean", "T_BYTE": "byte", "T_CHAR": "char", "T_SHORT": "short",
    "T_ILLEGAL": "from the descriptor",
}

# What each character of a format string is. Uppercase means the operand is in native
# byte order, which is only true after the Rewriter has been over the method, so it is
# the single most useful thing in this file for anybody comparing javap output with
# what an interpreter is really executing.
FORMAT_CHAR = {
    "b": "opcode byte",
    "w": "wide prefix",
    "i": "local variable index",
    "c": "constant",
    "k": "constant pool index",
    "j": "constant pool cache index",
    "o": "branch offset",
    "_": "nothing reads",
}


def pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def source(tag: str, path: str) -> tuple[str, str]:
    """One HotSpot file, from a local checkout when there is one and the network when
    there is not, with the hash of what was read either way."""
    local = os.environ.get("JVX_JDK_SRC")
    if local:
        found = pathlib.Path(local) / path
        if not found.is_file():
            sys.exit(f"JVX_JDK_SRC is set but {found} is not there")
        body = found.read_bytes()
    else:
        body = fetch(RAW.format(tag=tag, path=path))
    return body.decode("utf-8", errors="replace"), hashlib.sha256(body).hexdigest()


def enum(text: str) -> dict[str, int]:
    """The `Bytecodes::Code` enum, resolved to numbers.

    The file writes most entries without a value, meaning one more than the last, and
    writes two of them as a name that was defined earlier. Anything outside those two
    forms is a reason to stop, because a guess here would put every row of the table
    against the wrong number and nothing downstream would notice.
    """
    values: dict[str, int] = {}
    inside = False
    following = 0
    for number, line in enumerate(text.splitlines(), 1):
        if not inside:
            if line.strip().startswith("enum Code"):
                inside = True
            continue
        if line.strip().startswith("};"):
            break
        entry = line.split("//")[0].strip().rstrip(",").strip()
        if not entry:
            continue
        found = ENUM_ENTRY.match(entry)
        if found is None:
            continue
        name, raw = found.group("name"), found.group("value")
        if raw is None:
            value = following
        elif re.fullmatch(r"-?\d+", raw.strip()):
            value = int(raw.strip())
        elif raw.strip() in values:
            value = values[raw.strip()]
        else:
            sys.exit(f"{HEADER_PATH}:{number}: cannot resolve '{raw.strip()}'")
        values[name] = value
        following = value + 1
        if name == "number_of_codes":
            break
    if "_nop" not in values or "number_of_java_codes" not in values:
        sys.exit(f"{HEADER_PATH}: the enum did not parse, so its shape changed")
    return values


def defs(text: str) -> dict[str, dict]:
    """Every `def(...)` row of BYTECODES_DO, keyed by the enum name it defines."""
    rows: dict[str, dict] = {}
    for number, line in enumerate(text.splitlines(), 1):
        found = DEF.match(line)
        if found is None:
            continue
        rows[found.group("code")] = {
            "mnemonic": found.group("name"),
            "format": string_or_none(found.group("format")),
            "wide_format": string_or_none(found.group("wide")),
            "result": found.group("result"),
            "depth": int(found.group("depth")),
            "can_trap": found.group("trap") == "true",
            "java_code": found.group("java"),
            "line": number,
        }
    if len(rows) < 200:
        sys.exit(f"{TABLE_PATH}: only {len(rows)} definitions, so the table shape changed")
    return rows


def string_or_none(raw: str) -> str | None:
    return None if raw == "nullptr" else raw[1:-1]


def length(fmt: str | None) -> int | None:
    """How many bytes an instruction takes, or nothing when it is variable.

    An empty format string means variable length, which is `tableswitch`,
    `lookupswitch`, `wide`, `breakpoint` and two of HotSpot's rewritten switches. A
    missing format means the code has no short form at all.
    """
    if fmt is None or fmt == "":
        return None
    return len(fmt)


def spec_opcodes(edition: str) -> tuple[dict[int, str], str]:
    """JVMS chapter 7: the normative opcode list, by number."""
    url = SPEC_PAGE.format(edition=edition.lower(), chapter=SPEC_CHAPTER)
    body = fetch(url)
    text = body.decode("utf-8", errors="replace")
    found: dict[int, str] = {}
    for row in SPEC_ROW.finditer(text):
        number = int(row.group("number"))
        if number != int(row.group("hex"), 16):
            sys.exit(f"{url}: {number} is printed as 0x{row.group('hex')}")
        if number in found:
            sys.exit(f"{url}: opcode {number} is listed twice")
        found[number] = row.group("name")
    if len(found) < 200:
        sys.exit(f"{url}: only {len(found)} opcodes, so the page shape changed")
    return found, hashlib.sha256(body).hexdigest()


def spec_anchors(edition: str) -> set[str]:
    """The instruction description names in JVMS chapter 6."""
    url = SPEC_PAGE.format(edition=edition.lower(), chapter=INSTRUCTIONS_CHAPTER)
    text = fetch(url).decode("utf-8", errors="replace")
    found = {match.group("name") for match in SPEC_ANCHOR.finditer(text)}
    # The page also anchors the parts of a description, `aaload.desc` and the rest.
    # Those are dropped by taking only the names with no dot, which the regex already
    # does, so an empty set here means the page stopped using these anchors at all.
    if len(found) < 100:
        sys.exit(f"{url}: only {len(found)} instruction anchors, so the page changed")
    return found


def check_the_index(sha: str) -> str:
    """Compare the chapter we just read against the one the index recorded.

    `gen_jvms_index.py` hashes every chapter it indexes for exactly this: a chapter
    that was rewritten is an event somebody has to look at, rather than a table that
    quietly says something different next Tuesday.
    """
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
        sys.exit(f"no results in {RESULTS}, run probes/opcodes/run.py")
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def api_table(data: dict[str, dict]) -> dict[int, dict]:
    """`java.lang.classfile.Opcode` keyed by its byte, with the platforms made to agree.

    An enum is a property of the JDK rather than of the machine, so every environment
    must report the same one. A difference here means two different JDKs were measured
    and called the same pin, which is worth stopping for.
    """
    merged: dict[int, dict] = {}
    for platform, found in sorted(data.items()):
        for name, entry in found["opcodes"].items():
            byte = entry["bytecode"]
            row = {"name": name, "size": entry["size"], "kind": entry["kind"],
                   "wide": entry["wide"]}
            if byte in merged and merged[byte] != row:
                sys.exit(
                    f"{platform} reports opcode {byte} as {row} and another environment "
                    f"reports {merged[byte]}. Two JDKs, one pin."
                )
            merged[byte] = row
    return merged


def census(data: dict[str, dict]) -> dict[str, int]:
    """Instruction counts, summed over every environment measured.

    Summed rather than averaged. Each environment scanned its own copy of java.base and
    the copies are not identical, so a sum is a real count of instructions actually
    seen and a mean would be a count of nothing.
    """
    total: dict[str, int] = {}
    for found in data.values():
        for name, count in found["counts"].items():
            total[name] = total.get(name, 0) + count
    return total


def verify(spec: dict[int, str], codes: dict[str, int], rows: dict[str, dict],
           api: dict[int, dict], anchors: set[str]) -> list[str]:
    """Every check that has to pass before a table is worth writing.

    Each failure here is a source having moved. The right response to that is a person
    reading the diff, not a generator picking a winner, so these exit rather than warn.
    """
    notes: list[str] = []

    by_number = {codes[name]: (name, row) for name, row in rows.items()
                 if name in codes}

    for number, mnemonic in sorted(spec.items()):
        if number in (254, 255):
            if number in by_number:
                sys.exit(f"HotSpot now defines {number}, which the JVMS reserves")
            continue
        if number not in by_number:
            sys.exit(f"the JVMS has {number} {mnemonic} and HotSpot does not")
        name, row = by_number[number]
        if row["mnemonic"] != mnemonic:
            sys.exit(f"opcode {number}: the JVMS calls it {mnemonic}, HotSpot {row['mnemonic']}")

    for number, (name, row) in sorted(by_number.items()):
        if number < codes["number_of_java_codes"] and number not in spec:
            sys.exit(f"HotSpot has {number} {row['mnemonic']} below the Java codes "
                     f"boundary and the JVMS does not list it")

    # The class file API against HotSpot, on the two facts they both carry.
    for number, entry in sorted(api.items()):
        if entry["wide"]:
            base = number - (WIDE_PREFIX << 8)
            if base not in by_number:
                sys.exit(f"{entry['name']} is {number}, which is not the wide form of "
                         f"anything HotSpot defines")
            _, row = by_number[base]
            if row["wide_format"] is None:
                sys.exit(f"{entry['name']} is a wide form and HotSpot gives "
                         f"{row['mnemonic']} no wide format")
            if entry["size"] != len(row["wide_format"]):
                sys.exit(f"{entry['name']} is {entry['size']} bytes and HotSpot's "
                         f"{row['wide_format']} is {len(row['wide_format'])}")
            continue
        if number not in by_number:
            sys.exit(f"the class file API has {entry['name']} at {number} and HotSpot "
                     f"defines nothing there")
        _, row = by_number[number]
        fixed = length(row["format"])
        if fixed is not None and entry["size"] != fixed:
            sys.exit(f"{row['mnemonic']} is {fixed} bytes in HotSpot and "
                     f"{entry['size']} in the class file API")

    wide_forms = {name for name, row in rows.items() if row["wide_format"] is not None}
    api_wide = {entry["name"] for entry in api.values() if entry["wide"]}
    if len(wide_forms) != len(api_wide):
        sys.exit(f"HotSpot has {len(wide_forms)} wide forms and the class file API has "
                 f"{len(api_wide)}")

    if WIDE_PREFIX in api:
        sys.exit("the class file API now has a constant for `wide` itself, so the way "
                 "it models the prefix changed and the table below is describing "
                 "something that is no longer true")
    notes.append("`wide` has no constant in the class file API, which folds the prefix "
                 "into the opcode value of the twelve instructions that can take it.")

    for name, row in sorted(rows.items()):
        if row["java_code"] not in rows:
            sys.exit(f"{row['mnemonic']} says it rewrites {row['java_code']}, which is "
                     f"not a defined code")
        if row["format"] and not row["format"].startswith(("b", "w")):
            sys.exit(f"{row['mnemonic']} has format '{row['format']}', which starts "
                     f"with neither an opcode byte nor a wide prefix")
        if row["wide_format"] and not row["wide_format"].startswith("wb"):
            sys.exit(f"{row['mnemonic']} has wide format '{row['wide_format']}'")
        for char in (row["format"] or "") + (row["wide_format"] or ""):
            if char.lower() not in FORMAT_CHAR:
                sys.exit(f"{row['mnemonic']}: '{char}' is not a format character "
                         f"this knows about")

    for family, members in sorted(FAMILIES.items()):
        if family not in anchors:
            sys.exit(f"the JVMS no longer has a section called {family}")
    # The reserved opcodes are listed in chapter 7 and described nowhere in chapter 6,
    # which is correct rather than a gap: chapter 6.2 covers all three in prose.
    folded = {member for members in FAMILIES.values() for member in members}
    for number, (name, row) in sorted(by_number.items()):
        if number not in spec or number in RESERVED:
            continue
        mnemonic = row["mnemonic"]
        if mnemonic in folded:
            continue
        if mnemonic not in anchors:
            sys.exit(f"the JVMS has no section for {mnemonic}")

    return notes


def anchor_for(mnemonic: str) -> str | None:
    for family, members in FAMILIES.items():
        if mnemonic in members:
            return family
    return mnemonic


def spec_link(edition: str, mnemonic: str) -> str:
    found = anchor_for(mnemonic)
    if found is None:
        return ""
    page = SPEC_PAGE.format(edition=edition.lower(), chapter=INSTRUCTIONS_CHAPTER)
    return f"[{found}]({page}#jvms-6.5.{found})"


def operands(fmt: str | None) -> str:
    """A format string as words, for the reader who has never seen one.

    One character is one byte, and a run of the same character is one operand spread
    over that many bytes. Reading `boooo` out as four branch offsets rather than as one
    four byte offset would be a table that teaches the wrong thing about `goto_w`.
    """
    if fmt is None:
        return "no short form"
    if fmt == "":
        return "variable"
    parts = []
    for char, run in runs(fmt[1:]):
        word = FORMAT_CHAR[char.lower()]
        if char == "_":
            parts.append(f"{run} byte{'s' if run > 1 else ''} {word}")
            continue
        if char.isupper():
            word += " in native order"
        parts.append(f"a {run} byte {word}")
    return ", ".join(parts) if parts else "none"


def runs(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for char in text:
        if out and out[-1][0] == char:
            out[-1] = (char, out[-1][1] + 1)
        else:
            out.append((char, 1))
    return out


def build(pinned: dict, spec: dict[int, str], codes: dict[str, int],
          rows: dict[str, dict], api: dict[int, dict], counts: dict[str, int],
          data: dict[str, dict], notes: list[str], hashes: dict[str, str]) -> str:
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]
    by_number = {codes[name]: (name, row) for name, row in rows.items() if name in codes}
    api_by_name = {entry["name"]: entry for entry in api.values()}

    scanned = sorted({found["scan"]["module"] for found in data.values()})
    platforms = ", ".join(f"`{name}`" for name in sorted(data))
    builds = sorted({found["java_build"] for found in data.values()})
    measured = sorted({found["measured"] for found in data.values()})
    total = sum(counts.values())
    classes = sum(found["scan"]["classes"] for found in data.values())
    bodies = sum(found["scan"]["method_bodies"] for found in data.values())

    out: list[str] = []
    out.append("# BP-BYTECODE section 3. The instruction set")
    out.append("")
    out.append(
        f"Generated by `tools/gen_opcodes.py`. Do not edit it, edit a source. There are "
        f"three: JVMS {edition} chapter 7 for what a class file may contain, "
        f"`{TABLE_PATH}` at `{tag}` for what HotSpot has and what each instruction "
        f"costs, and `java.lang.classfile.Opcode` read off the pinned JDK for what the "
        f"platform's own class file library believes. The counts come from "
        f"`probes/opcodes/results`."
    )
    out.append("")
    out.append(
        f"Counted over {', '.join(f'`{m}`' for m in scanned)} on {platforms}, java "
        f"{', '.join(builds)}, on {', '.join(measured)}: {classes:,} class files, "
        f"{bodies:,} method bodies, {total:,} instructions."
    )
    out.append("")

    out.append("## 3.1 How many opcodes there are, which depends on who is asked")
    out.append("")
    out.append("| source | how many | what it is counting |")
    out.append("|---|---|---|")
    out.append(f"| JVMS {edition} chapter 7 | {len(spec)} | every opcode a class file "
               f"may contain, including the three it reserves |")
    hot_java = sum(1 for number in by_number if number < codes["number_of_java_codes"])
    hot_all = len(rows)
    out.append(f"| `bytecodes.cpp` at `{tag}` | {hot_all} | every code the interpreter "
               f"understands, of which {hot_java} are numbered at or below the last "
               f"opcode the specification names |")
    out.append(f"| `java.lang.classfile.Opcode` | {len(api)} | every instruction the "
               f"class file API can read or write, counting each wide form separately |")
    out.append("")
    for note in notes:
        out.append(note)
        out.append("")
    out.append(
        f"The three numbers differ for two reasons and neither is a mistake. HotSpot "
        f"defines {hot_all - hot_java} codes above the last one the specification "
        f"names, which it writes into a method's bytecode after the class file has been "
        f"parsed, so they exist in memory and can never exist in a file. The class file "
        f"API drops `wide` and `breakpoint` and adds twelve wide forms, which is a "
        f"different way of modelling the same bytes."
    )
    out.append("")

    out.append("## 3.2 Every opcode a class file may contain")
    out.append("")
    out.append(
        "Length is the whole instruction in bytes, opcode included. Stack is what the "
        "instruction does to the depth of the operand stack, as HotSpot's own table "
        "records it. Seen is how many times it occurs in the scanned code above, which "
        "is the column that says which of these a reader will actually meet."
    )
    out.append("")
    native = sorted(row["mnemonic"] for number, (name, row) in by_number.items()
                    if number in spec and row["format"]
                    and any(char.isupper() for char in row["format"]))
    out.append(
        f"The operands column is HotSpot's format string put into words, and it "
        f"describes a method already loaded into a VM rather than the bytes on disk. "
        f"That is why {word(len(native))} of these say native order: HotSpot rewrites those "
        f"operands when it links the method, replacing the constant pool index the file "
        f"carries with an index into its own resolved cache, in the byte order of the "
        f"machine. In the class file itself every one of them is a big endian constant "
        f"pool index, which is what to write when building one by hand and what `javap` "
        f"shows. The ones that differ are "
        + ", ".join(f"`{name}`" for name in native) + "."
    )
    out.append("")
    out.append("| opcode | mnemonic | length | operands | stack | traps | seen | JVMS |")
    out.append("|---:|---|---:|---|---:|---|---:|---|")
    reserved_page = SPEC_PAGE.format(edition=edition.lower(),
                                     chapter=INSTRUCTIONS_CHAPTER)
    for number in sorted(spec):
        mnemonic = spec[number]
        where = (f"[reserved]({reserved_page}#jvms-6.2)" if number in RESERVED
                 else spec_link(edition, mnemonic))
        if number not in by_number:
            out.append(f"| {number} `0x{number:02x}` | `{mnemonic}` | | | | | | "
                       f"{where} |")
            continue
        _, row = by_number[number]
        fixed = length(row["format"])
        size = str(fixed) if fixed is not None else "variable"
        api_entry = api_by_name.get(mnemonic.upper())
        seen = counts.get(mnemonic.upper(), 0) if api_entry else 0
        seen_text = f"{seen:,}" if api_entry else "n/a"
        out.append(
            f"| {number} `0x{number:02x}` | `{mnemonic}` | {size} | "
            f"{operands(row['format'])} | {row['depth']:+d} | "
            f"{'yes' if row['can_trap'] else 'no'} | {seen_text} | {where} |"
        )
    out.append("")
    blank = sum(1 for number in RESERVED if number not in by_number)
    out.append(
        f"Three of those opcodes are reserved by the specification and may not appear in "
        f"a class file, and {word(blank)} of the three have an empty row above because "
        f"HotSpot defines nothing for them. "
        + " ".join(f"`0x{number:02x}` is {why}."
                   for number, why in sorted(RESERVED.items()))
    )
    out.append("")

    out.append("## 3.3 The twelve instructions `wide` can be applied to")
    out.append("")
    out.append(
        "`wide` is a prefix rather than an instruction. It takes the one byte operand "
        "of the instruction after it and makes it two, and for `iinc` it makes both "
        "operands two. The class file API models this by giving each combination its "
        "own constant, whose value is the prefix byte shifted left eight bits plus the "
        "opcode, which is why those constants have values above 50,000."
    )
    out.append("")
    out.append("| instruction | narrow | wide | class file API | seen wide |")
    out.append("|---|---:|---:|---|---:|")
    for number, entry in sorted(api.items()):
        if not entry["wide"]:
            continue
        base = number - (WIDE_PREFIX << 8)
        _, row = by_number[base]
        out.append(
            f"| `{row['mnemonic']}` | {length(row['format'])} | "
            f"{len(row['wide_format'])} | `{entry['name']}` = {number} | "
            f"{counts.get(entry['name'], 0):,} |"
        )
    out.append("")

    out.append("## 3.4 The codes HotSpot has and no class file may contain")
    out.append("")
    out.append(
        "The interpreter rewrites a method's bytecode the first time the method is "
        "linked, replacing some instructions with faster forms that resolve a constant "
        "pool entry once instead of every time. Those forms have opcode numbers above "
        "everything the specification names. A reader who dumps a method's bytecode out "
        "of a running VM sees them and a reader who runs `javap` on the same class never "
        "does, and that difference is not a bug in either tool."
    )
    out.append("")
    out.append("| code | number | rewritten from | length | stack | traps |")
    out.append("|---|---:|---|---:|---:|---|")
    for number, (name, row) in sorted(by_number.items()):
        if number < codes["number_of_java_codes"]:
            continue
        origin = rows[row["java_code"]]["mnemonic"]
        # A code whose java_code is itself is not a rewrite of anything. There is one,
        # and printing it as rewritten from itself would read like a parse failure.
        came_from = "nothing" if row["java_code"] == name else f"`{origin}`"
        fixed = length(row["format"])
        out.append(
            f"| `{row['mnemonic']}` | {number} | {came_from} | "
            f"{fixed if fixed is not None else 'variable'} | {row['depth']:+d} | "
            f"{'yes' if row['can_trap'] else 'no'} |"
        )
    out.append("")

    out.append("## 3.5 Which opcodes a reader will actually meet")
    out.append("")
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    used = [(name, count) for name, count in ranked if count]
    running = 0
    marks: dict[int, int] = {}
    for index, (_, count) in enumerate(used, 1):
        running += count
        for share in (50, 90, 99):
            if share not in marks and running * 100 >= total * share:
                marks[share] = index
    out.append(
        f"Of the {len(api)} instructions the class file API can read, {len(used)} occur "
        f"at least once in the code scanned above and {len(api) - len(used)} never "
        f"occur at all. The {marks.get(50, 0)} most common are half of every "
        f"instruction executed, the {marks.get(90, 0)} most common are ninety per cent "
        f"and the {marks.get(99, 0)} most common are ninety nine."
    )
    out.append("")
    out.append("| rank | instruction | seen | share | running |")
    out.append("|---:|---|---:|---:|---:|")
    running = 0
    for index, (name, count) in enumerate(used[:20], 1):
        running += count
        out.append(f"| {index} | `{name.lower()}` | {count:,} | "
                   f"{100 * count / total:.2f}% | {100 * running / total:.2f}% |")
    out.append("")
    never = [name for name, count in sorted(counts.items()) if not count]
    out.append(f"Never once, in {total:,} instructions:")
    out.append("")
    out.append(", ".join(f"`{name.lower()}`" for name in never))
    out.append("")

    highest_slot = max(found["scan"]["highest_local_slot"] for found in data.values())
    increments = [found["scan"] for found in data.values()]
    low = min(found["lowest_iinc_constant"] for found in increments)
    high = max(found["highest_iinc_constant"] for found in increments)
    wide_used = sorted(
        (name, max(found["widest_slot"].get(name, -1) for found in data.values()))
        for name, count in counts.items()
        if count and api_by_name.get(name, {}).get("wide")
    )
    out.append(
        f"That list is the useful half of this section. `jsr`, `jsr_w`, `ret` and "
        f"`ret_w` are the four no compiler has emitted since 1.4 and the four the "
        f"verifier's type checking rules were rewritten so as not to need. `goto_w` "
        f"never occurs, which means no method "
        f"body in the scanned code branches further than a two byte offset reaches. The "
        f"highest local variable slot any instruction anywhere in it names is "
        f"{highest_slot}, which is why eleven of the twelve wide forms never occur."
    )
    out.append("")
    if wide_used:
        named = ", ".join(f"`{name.lower()}`, whose highest slot is {slot}"
                          for name, slot in wide_used)
        out.append(
            f"The exception is worth its own sentence, because it is the thing a table "
            f"of opcode lengths will not tell you. The wide form that does occur is "
            f"{named}. `wide` widens both operands of `iinc`, and the increments in the "
            f"scanned code run from {low:,} to {high:,}, so what needed the extra bytes "
            f"was the constant rather than the slot."
        )
        out.append("")

    # Everything already accounted for by the paragraphs above, so that a reader is not
    # told about the same opcode twice with two different reasons.
    explained = {"jsr", "jsr_w", "ret", "ret_w", "goto_w"}
    others = [name.lower() for name in never
              if not api_by_name.get(name, {}).get("wide")
              and name.lower() not in explained]
    if others:
        out.append(
            f"The remaining {word(len(others))}, "
            + ", ".join(f"`{name}`" for name in others)
            + ", are ordinary instructions that are legal, that the class file API will "
            "write, and that nothing in the scanned code happens to contain. That is a "
            "fact about one compiler's output for one module rather than a fact about "
            "the instruction set, and it is the reason this section counts rather than "
            "asserts."
        )
        out.append("")

    out.append("## 3.6 Provenance")
    out.append("")
    out.append(
        "A hash of each source as it was read. It is here for the same reason "
        "`gen_markword.py` hashes the header it parses: a source that was rewritten "
        "underneath a generated table should be an event somebody looks at, rather than "
        "a table that quietly says something else next Tuesday."
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
                    help="fail if the committed table is stale")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the summary without writing")
    args = ap.parse_args(argv)

    pinned = pin()
    tag = pinned["jdk_tag"]
    edition = pinned["jvms_edition"]

    header, header_sha = source(tag, HEADER_PATH)
    table, table_sha = source(tag, TABLE_PATH)
    codes = enum(header)
    rows = defs(table)

    spec, spec_sha = spec_opcodes(edition)
    index_says = check_the_index(spec_sha)
    anchors = spec_anchors(edition)

    data = results()
    api = api_table(data)
    counts = census(data)

    notes = verify(spec, codes, rows, api, anchors)
    hashes = {
        f"`{HEADER_PATH}` at `{tag}`": header_sha,
        f"`{TABLE_PATH}` at `{tag}`": table_sha,
        f"JVMS {edition} chapter {SPEC_CHAPTER}, which {index_says}": spec_sha,
    }

    text = build(pinned, spec, codes, rows, api, counts, data, notes, hashes)

    if args.show:
        print(f"{len(spec)} opcodes in the specification, {len(rows)} codes in HotSpot, "
              f"{len(api)} constants in the class file API")
        print(f"{sum(counts.values()):,} instructions counted over "
              f"{len(data)} environments")
        print(f"{sum(1 for c in counts.values() if not c)} never occur")
        return 0

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_opcodes.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT} does not match its sources, run tools/gen_opcodes.py",
                  file=sys.stderr)
            return 1
        print(f"{OUTPUT} matches all three sources")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
