#!/usr/bin/env python3
"""Join the fuzzer's rejections to the sentences of the specification they break.

M2's last gate asks for a case where HotSpot's rejection message differs from what the
specification names. That is a comparison between two texts. `probes/classfile-fuzz`
produces one of them by breaking a valid class file 27 named ways and 256 random ways and
recording what the JVM said. The other one is `SPEC` below: for each named case, the JVMS
section that states the rule, the words that section uses, and the error the specification
names for breaking it.

  python tools/gen_fuzz.py            write docs/generated/classfile-fuzz.md
  python tools/gen_fuzz.py --check    fail if the committed page is stale
  python tools/gen_fuzz.py --print    the summary, without writing anything

Every claim in `SPEC` is checked against the pinned specification before anything is
written. The section has to exist, every fragment quoted from it has to appear in it
verbatim, the error named for it has to appear in the section that names errors, and the
words the comparison looks for in a JVM message have to be words the quoted sentence
actually uses. A claim that fails any of those stops the generator, because a table
comparing HotSpot against a rule nobody can find is worse than no table.

The chapters come from docs.oracle.com at the pinned edition, and their hashes are checked
against the ones `tools/gen_jvms_index.py` recorded, so a chapter rewritten underneath us
is an event rather than a table that quietly changed meaning.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = ROOT / "docs" / "pin.json"
RESULTS = pathlib.Path("probes/classfile-fuzz/results")
JVMS_INDEX = pathlib.Path("docs/generated/jvms-index.json")
OPCODES = pathlib.Path("docs/generated/opcodes.md")
OUTPUT = pathlib.Path("docs/generated/classfile-fuzz.md")

PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"

# Chapter 4 states the rules and chapter 5 names the errors. Nothing else is fetched.
CHAPTERS = ["4", "5"]

# `<a name="jvms-4.9.1"></a>` opens a section and the next one closes it. Splitting on the
# anchor rather than on the heading markup is what keeps this working when the template
# around the heading changes, which it has between editions.
ANCHOR = re.compile(r'<a name="(jvms-[\d.]+)"></a>')
TAG = re.compile(r"<[^>]+>")

# `Location: Target.go()I @0: fast_iaccess_0` in a verifier message.
AT_OPCODE = re.compile(r"@\d+: ([A-Za-z0-9_]+)")

# `| 0 `0x00` | `nop` | 1 | none |` in the generated opcode table.
OPCODE_ROW = re.compile(r"^\| \d+ `0x[0-9a-f]{2}` \| `([a-z0-9_]+)` \|")


@dataclasses.dataclass(frozen=True)
class Claim:
    """What the specification says about one of the 27 things the fuzzer breaks.

    `rule` is the section that states the constraint and `states` is quoted from it, in
    fragments when the sentence between them is a list of twenty mnemonics. `error` is the
    throwable the specification names for breaking a constraint of this kind and
    `named_at` is where it names it, which is 5.3.5 for a class file that is not a
    ClassFile structure and 5.4.1 for a class file that breaks a constraint in 4.9.

    `names` is the interesting one. These are the identifiers the quoted sentence itself
    uses for the thing that is wrong, and the comparison asks whether the JVM's message
    uses any of them. They are checked against the quotation rather than chosen freely,
    so the column measures whether HotSpot and the specification are talking about the
    same item in the same words rather than whether a message matched a word I liked.
    """

    rule: str
    states: tuple[str, ...]
    error: str
    named_at: str
    names: tuple[str, ...]


def claim(rule: str, states, error: str, named_at: str, names) -> Claim:
    if isinstance(states, str):
        states = (states,)
    return Claim(rule, tuple(states), error, named_at, tuple(names))


FORMAT = "5.3.5"      # names ClassFormatError, UnsupportedClassVersionError, NoClassDefFoundError
VERIFY = "5.4.1"      # names VerifyError, for the constraints in 4.9

SPEC: dict[str, Claim] = {
    "magic_is_not_cafebabe": claim(
        "4.1",
        "The magic item supplies the magic number identifying the class file format; "
        "it has the value 0xCAFEBABE",
        "ClassFormatError", FORMAT, ["magic"]),
    "major_version_before_there_were_any": claim(
        "4.1",
        "The major_version item must be a value in the range 45 through 69, inclusive",
        "UnsupportedClassVersionError", FORMAT, ["major_version"]),
    "major_version_from_the_future": claim(
        "4.1",
        "The major_version item must be a value in the range 45 through 69, inclusive",
        "UnsupportedClassVersionError", FORMAT, ["major_version"]),
    "constant_pool_count_is_zero": claim(
        "4.1",
        "The value of the constant_pool_count item is equal to the number of entries in "
        "the constant_pool table plus one",
        "ClassFormatError", FORMAT, ["constant_pool_count", "constant_pool"]),
    "constant_pool_count_past_the_end": claim(
        "4.1",
        "The value of the constant_pool_count item is equal to the number of entries in "
        "the constant_pool table plus one",
        "ClassFormatError", FORMAT, ["constant_pool_count", "constant_pool"]),
    "constant_pool_tag_is_not_a_tag": claim(
        "4.4",
        "Each entry in the constant_pool table must begin with a 1-byte tag indicating "
        "the kind of constant denoted by the entry",
        "ClassFormatError", FORMAT, ["tag"]),
    "class_entry_names_an_index_past_the_end": claim(
        "4.4.1",
        "The value of the name_index item must be a valid index into the constant_pool "
        "table",
        "ClassFormatError", FORMAT, ["index"]),
    "class_entry_names_the_wrong_kind_of_entry": claim(
        "4.4.1",
        "The constant_pool entry at that index must be a CONSTANT_Utf8_info structure",
        "ClassFormatError", FORMAT, ["Utf8"]),
    "utf8_holds_a_zero_byte": claim(
        "4.4.7",
        "The bytes array contains the bytes of the string. No byte may have the value "
        "(byte)0",
        "ClassFormatError", FORMAT, ["byte"]),
    "this_class_is_not_a_class_entry": claim(
        "4.1",
        "The value of the this_class item must be a valid index into the constant_pool "
        "table. The constant_pool entry at that index must be a CONSTANT_Class_info "
        "structure",
        "ClassFormatError", FORMAT, ["this_class"]),
    "this_class_names_a_different_class": claim(
        "5.3.5",
        "the purported representation does not actually represent a class or interface "
        "named",
        "NoClassDefFoundError", FORMAT, ["name"]),
    "super_class_is_zero": claim(
        "4.1",
        "If the value of the super_class item is zero, then this class file must "
        "represent the class Object",
        "ClassFormatError", FORMAT, ["super_class"]),
    "the_class_is_final_and_abstract": claim(
        "4.1",
        "such a class file must not have both its ACC_FINAL and ACC_ABSTRACT flags set",
        "ClassFormatError", FORMAT, ["ACC_FINAL", "ACC_ABSTRACT"]),
    "the_class_is_an_interface_that_is_not_abstract": claim(
        "4.1",
        "If the ACC_INTERFACE flag is set, the ACC_ABSTRACT flag must also be set",
        "ClassFormatError", FORMAT, ["ACC_INTERFACE", "ACC_ABSTRACT"]),
    "the_field_is_final_and_volatile": claim(
        "4.5",
        "must not have both its ACC_FINAL and ACC_VOLATILE flags set",
        "ClassFormatError", FORMAT, ["ACC_FINAL", "ACC_VOLATILE"]),
    "the_method_is_abstract_and_final": claim(
        "4.6",
        "If a method of a class or interface has its ACC_ABSTRACT flag set, it must not "
        "have any of its ACC_PRIVATE",
        "ClassFormatError", FORMAT, ["ACC_ABSTRACT"]),
    "code_length_is_zero": claim(
        "4.7.3",
        "The value of code_length must be greater than zero (as the code array must not "
        "be empty) and less than 65536",
        "ClassFormatError", FORMAT, ["code_length"]),
    "code_length_is_past_the_attribute": claim(
        "4.7",
        "The value of the attribute_length item indicates the length of the subsequent "
        "information in bytes",
        "ClassFormatError", FORMAT, ["attribute_length"]),
    "the_attribute_is_longer_than_what_follows_it": claim(
        "4.7",
        "The value of the attribute_length item indicates the length of the subsequent "
        "information in bytes",
        "ClassFormatError", FORMAT, ["attribute_length"]),
    "the_file_is_truncated": claim(
        "4.8",
        "The class file must not be truncated or have extra bytes at the end",
        "ClassFormatError", FORMAT, ["truncated"]),
    "the_file_has_four_bytes_too_many": claim(
        "4.8",
        "The class file must not be truncated or have extra bytes at the end",
        "ClassFormatError", FORMAT, ["extra bytes"]),
    "the_opcode_is_not_an_opcode": claim(
        "4.9.1",
        ("Only instances of the instructions documented in",
         "any opcodes not documented in this specification must not appear in the code "
         "array"),
        "VerifyError", VERIFY, ["instruction"]),
    "the_jump_lands_outside_the_method": claim(
        "4.9.1",
        ("The target of each jump and branch instruction",
         "must be the opcode of an instruction within this method"),
        "VerifyError", VERIFY, ["branch"]),
    "max_stack_is_too_small": claim(
        "4.9.2",
        "At no point during execution can the operand stack grow to a depth greater "
        "than that implied by the max_stack item",
        "VerifyError", VERIFY, ["max_stack"]),
    "max_locals_is_too_small": claim(
        "4.9.1",
        ("The index operand of each iload",
         "instruction must be a non-negative integer no greater than max_locals - 1"),
        "VerifyError", VERIFY, ["max_locals"]),
    "the_stack_map_frame_type_is_reserved": claim(
        "4.7.4",
        "Tags in the range [128-246] are reserved for future use",
        "ClassFormatError", FORMAT, ["reserved"]),
    # The rule broken here is not the one about unrecognised attributes. JVMS 4.7.1 says
    # an implementation must silently ignore an attribute it does not recognise, and
    # HotSpot does. What is left after it does is a method with no stack maps, which is
    # the type checker's rule rather than the parser's, so this cites the type checker.
    "the_stack_map_table_is_renamed_and_so_ignored": claim(
        "4.10.1",
        ("The type checker requires a list of stack map frames for each method with a "
         "Code attribute",
         "A list of stack map frames is given by the StackMapTable attribute"),
        "VerifyError", VERIFY, ["stack map"]),
}

# The order the page lists them in: down the file, header first and method bodies last,
# then the two that are about the file as a whole.
ORDER = [
    "magic_is_not_cafebabe",
    "major_version_before_there_were_any",
    "major_version_from_the_future",
    "constant_pool_count_is_zero",
    "constant_pool_count_past_the_end",
    "constant_pool_tag_is_not_a_tag",
    "class_entry_names_an_index_past_the_end",
    "class_entry_names_the_wrong_kind_of_entry",
    "utf8_holds_a_zero_byte",
    "this_class_is_not_a_class_entry",
    "this_class_names_a_different_class",
    "super_class_is_zero",
    "the_class_is_final_and_abstract",
    "the_class_is_an_interface_that_is_not_abstract",
    "the_field_is_final_and_volatile",
    "the_method_is_abstract_and_final",
    "code_length_is_zero",
    "code_length_is_past_the_attribute",
    "the_attribute_is_longer_than_what_follows_it",
    "max_stack_is_too_small",
    "max_locals_is_too_small",
    "the_opcode_is_not_an_opcode",
    "the_jump_lands_outside_the_method",
    "the_stack_map_frame_type_is_reserved",
    "the_stack_map_table_is_renamed_and_so_ignored",
    "the_file_is_truncated",
    "the_file_has_four_bytes_too_many",
]

# The regions of the seed class file, in the order the file lays them out, so the random
# table reads like a walk through the file rather than like an alphabetical list.
REGIONS = [
    "header",
    "constant pool",
    "class, super and interfaces",
    "fields",
    "methods",
    "class attributes",
]

STAGE_TITLE = {
    "parse": "defineClass",
    "link": "linking",
    "run": "running",
    "accepted": "nothing objected",
    "killed the jvm": "killed the JVM",
}


def pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def sections(body: bytes) -> dict[str, str]:
    """Every anchored section of one chapter, as text with its markup removed.

    The specification is HTML and the claims below quote sentences out of it, so the
    quotation has to be compared against something. Stripping tags to spaces and then
    collapsing whitespace is what makes `<code class="literal">max_stack</code> item` and
    `max_stack item` the same string, which is the whole reason a quotation can be
    checked at all.
    """
    text = body.decode("utf-8", errors="replace")
    parts = ANCHOR.split(text)
    found: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        name = parts[index].removeprefix("jvms-")
        prose = html.unescape(TAG.sub(" ", parts[index + 1]))
        found[name] = " ".join(prose.split())
    return found


def chapters(edition: str) -> tuple[dict[str, str], dict[str, str]]:
    """The sections of chapters 4 and 5, and the hash of each page they came from."""
    prose: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for which in CHAPTERS:
        url = PAGE.format(edition=edition.lower(), chapter=which)
        body = fetch(url)
        prose.update(sections(body))
        hashes[which] = hashlib.sha256(body).hexdigest()
    return prose, hashes


def against_the_index(hashes: dict[str, str]) -> dict[str, str]:
    """Compare the chapters just read against the ones the index recorded.

    `gen_jvms_index.py` hashes every chapter for exactly this. A chapter that was
    republished has to stop the build, because the sentences quoted below are the
    reason this page can say what the specification names, and a rewritten chapter is
    the one way that stops being true without anything else changing.
    """
    if not JVMS_INDEX.is_file():
        sys.exit(f"{JVMS_INDEX} is not committed, run tools/gen_jvms_index.py")
    index = json.loads(JVMS_INDEX.read_text(encoding="utf-8"))
    known = {one["chapter"]: one for one in index.get("chapters", [])}
    said: dict[str, str] = {}
    for which, sha in hashes.items():
        one = known.get(which)
        if one is None:
            sys.exit(f"docs/generated/jvms-index.json does not carry chapter {which}")
        if one["sha256"] != sha:
            sys.exit(
                f"JVMS chapter {which} hashes to {sha[:12]} and the index says "
                f"{one['sha256'][:12]}. The chapter changed. Run "
                f"tools/gen_jvms_index.py, read the diff, then run this again."
            )
        said[which] = one["title"]
    return said


def verify(prose: dict[str, str]) -> None:
    """Stop unless every claim in `SPEC` is a claim the pinned specification supports."""
    for case, one in sorted(SPEC.items()):
        rule = prose.get(one.rule)
        if rule is None:
            sys.exit(f"{case}: JVMS {one.rule} is not a section at this edition")
        for fragment in one.states:
            if fragment not in rule:
                sys.exit(
                    f"{case}: JVMS {one.rule} does not contain '{fragment}'. Either the "
                    f"section was rewritten or the claim was wrong when it was written. "
                    f"Read the section before changing the quotation."
                )
        naming = prose.get(one.named_at)
        if naming is None:
            sys.exit(f"{case}: JVMS {one.named_at} is not a section at this edition")
        if one.error not in naming:
            sys.exit(
                f"{case}: JVMS {one.named_at} does not name {one.error}, so this page "
                f"cannot say the specification names it"
            )
        quoted = " ".join(one.states).lower()
        for name in one.names:
            if name.lower() not in quoted:
                sys.exit(
                    f"{case}: '{name}' is not a word the quoted sentence uses, so "
                    f"looking for it in a JVM message measures nothing"
                )
        # 4.9 states the constraints, 4.10 states the verification that enforces them,
        # and 5.4.1 names one error for both. Keeping that link mechanical means a case
        # cannot be quietly given the error HotSpot happens to throw.
        if one.rule.startswith(("4.9", "4.10")) != (one.error == "VerifyError"):
            sys.exit(
                f"{case}: JVMS {one.rule} with {one.error}. A constraint in 4.9 or 4.10 "
                f"is a VerifyError by 5.4.1 and anything else is not."
            )


def results() -> dict[str, dict]:
    found = {}
    for path in sorted((ROOT / RESULTS).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        # The file is named for the platform the house calls it and the probe records
        # what the JVM calls itself, `osx-arm64` against `darwin-arm64`. Both are worth
        # printing, so the reading of the file carries the name of the file.
        data["file"] = f"{RESULTS.as_posix()}/{path.name}"
        found[data["platform"]] = data
    if not found:
        sys.exit(f"no results in {RESULTS}, run probes/classfile-fuzz/run.py")
    return found


def agree(data: dict[str, dict], reach) -> object:
    """One value, or a stop if the platforms disagree about it.

    Everything this page reports is a property of the class file format and of one JDK
    build, so two environments running that build have to say the same thing. A
    disagreement is a finding rather than a cell, and it belongs in prose that somebody
    wrote, not in a table that averaged it away.
    """
    seen = {name: reach(one) for name, one in data.items()}
    values = set(seen.values())
    if len(values) != 1:
        detail = ", ".join(f"{name}: {value!r}" for name, value in sorted(seen.items()))
        sys.exit(f"the platforms disagree, which is a finding rather than a row: {detail}")
    return values.pop()


def mentions(message: str, name: str) -> bool:
    """Does this message use this identifier, allowing for how each side writes it.

    The specification writes `max_stack` and a VM message might write `max stack` or
    `maxstack`, and all three are the same word to a reader. Whole words only: the
    message for a max_stack that is too small contains `StackMapTable`, and counting
    that as naming the stack would turn the most interesting row on the page into a
    row where everything matched.
    """
    lowered = message.lower()
    spaced = name.lower().replace("_", " ")
    forms = {name.lower(), spaced, spaced.replace(" ", "")}
    return any(re.search(rf"\b{re.escape(form)}\b", lowered) for form in forms)


def thrown(error: str) -> str:
    """`java.lang.VerifyError` as `VerifyError`, and an empty error as nothing."""
    return error.rsplit(".", 1)[-1] if error else ""


def short(message: str) -> str:
    """A message without the disassembly some of them carry."""
    cut = message.split(" Exception Details:", 1)[0]
    return " ".join(cut.split())


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def opcodes() -> set[str]:
    """Every mnemonic JVMS chapter 7 lists, read off the table that generator wrote.

    This is here for one row. When the verifier refuses a byte that is not an opcode it
    names the instruction it found, and the name it prints is not always a name the
    specification has. Checking that against the committed opcode table rather than
    against a list typed here means the claim moves when the instruction set does.
    """
    if not OPCODES.is_file():
        sys.exit(f"{OPCODES} is missing, run tools/gen_opcodes.py")
    found = {row.group(1) for row in
             (OPCODE_ROW.match(line) for line in
              OPCODES.read_text(encoding="utf-8").splitlines()) if row}
    if len(found) < 200:
        sys.exit(f"{OPCODES} yielded {len(found)} mnemonics, so its table changed shape")
    return found


def invented(data: dict[str, dict], known: set[str]) -> list[tuple[str, str]]:
    """The instruction names a message prints that the specification does not have."""
    out: list[tuple[str, str]] = []
    for case in ORDER:
        message = str(agree(data, lambda one: one["cases"][case]["message"]))
        for name in AT_OPCODE.findall(message):
            if name not in known and (case, name) not in out:
                out.append((case, name))
    return out


def rows(data: dict[str, dict]) -> list[dict]:
    """One row per case, with both sides of the comparison already made."""
    out = []
    for case in ORDER:
        one = SPEC[case]
        what = str(agree(data, lambda d: d["cases"][case]["what"]))
        stage = str(agree(data, lambda d: d["cases"][case]["stage"]))
        error = thrown(str(agree(data, lambda d: d["cases"][case]["error"])))
        message = short(str(agree(data, lambda d: d["cases"][case]["message"])))
        out.append({
            "case": case,
            "what": what,
            "stage": stage,
            "error": error,
            "message": message,
            "expected": one.error,
            "same_error": error == one.error,
            "names_it": any(mentions(message, name) for name in one.names),
        })
    return out


def by_region(data: dict[str, dict]) -> list[dict]:
    flips = next(iter(data.values()))["random"]
    out = []
    for region in REGIONS:
        mine = [flip for flip in flips if flip["region"] == region]
        if not mine:
            continue
        out.append({
            "region": region,
            "flips": len(mine),
            "bytes": next(iter(data.values()))["regions"][region]["bytes"],
            "accepted": sum(1 for flip in mine if flip["stage"] == "accepted"),
            "errors": sorted({thrown(flip["error"]) for flip in mine if flip["error"]}),
        })
    return out


def counted(flips: list[dict], field: str) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for flip in flips:
        key = thrown(flip[field]) if field == "error" else flip[field]
        if key:
            tally[key] = tally.get(key, 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))


def build(data: dict[str, dict], prose: dict[str, str], hashes: dict[str, str],
          titles: dict[str, str], pinned: dict) -> str:
    edition = pinned["jvms_edition"]
    one = next(iter(data.values()))
    table = rows(data)
    flips = one["random"]
    differs = [row for row in table if not row["same_error"]]
    silent = [row for row in table if row["names_it"] is False]

    # The gate this page exists for. If a JDK ever agrees with the specification on all
    # 27, the page's own headline stops being true, and it should stop rather than
    # print a heading contradicted by the table under it.
    if not differs:
        sys.exit(
            "every case now throws the error the specification names. That is worth "
            "knowing and it is not what this page says, so rewrite the page."
        )

    out: list[str] = []
    out.append("# Where HotSpot and the specification disagree about a broken class file")
    out.append("")
    out.append(
        "Generated by `tools/gen_fuzz.py` from `probes/classfile-fuzz/results` and "
        f"JVMS {edition}. Do not edit it, edit a source."
    )
    out.append("")
    names = ", ".join(f"`{name}`" for name in sorted(data))
    build_id = str(agree(data, lambda d: d["java_build"]))
    measured = sorted({d["measured"] for d in data.values()})
    out.append(
        f"One valid class file of {one['seed_bytes']} bytes, broken {len(ORDER)} named "
        f"ways and {len(flips)} random ways, on {names}, java {build_id}, on "
        f"{', '.join(measured)}. The two environments agree on every row below, so each "
        f"cell is one answer rather than an average of two."
    )
    out.append("")

    out.append("## The error the specification names, and the error that arrives")
    out.append("")
    out.append(
        f"{len(differs)} of {len(ORDER)} cases end in an error the specification does "
        f"not name for them. JVMS {VERIFY} says a class file that breaks a constraint "
        f"in 4.9 throws a VerifyError, and JVMS {FORMAT} says a purported representation "
        f"that is not a ClassFile structure throws a ClassFormatError, so which of the "
        f"two arrives says which kind of rule the JVM thinks was broken."
    )
    out.append("")
    out.append("| what was broken | JVMS | names | thrown | stops at | same |")
    out.append("|---|---|---|---|---|---|")
    for row in table:
        rule = SPEC[row["case"]].rule
        link = f"[{rule}]({PAGE.format(edition=edition.lower(), chapter=rule.split('.')[0])}#jvms-{rule})"
        out.append(
            f"| {cell(row['what'])} | {link} | `{row['expected']}` | `{row['error']}` "
            f"| {STAGE_TITLE.get(row['stage'], row['stage'])} "
            f"| {'yes' if row['same_error'] else '**no**'} |"
        )
    out.append("")

    out.append("## What the JVM said, and whether it names the item the rule is about")
    out.append("")
    out.append(
        f"Each rule below is quoted from the pinned specification and the words looked "
        f"for in a message are words that quotation uses. {len(silent)} of "
        f"{len(ORDER)} messages use none of them. Messages that carry a disassembly are "
        f"cut at `Exception Details`, which is where the disassembly starts."
    )
    out.append("")
    out.append("| what was broken | the rule names | the message | says it |")
    out.append("|---|---|---|---|")
    for row in table:
        wanted = ", ".join(f"`{name}`" for name in SPEC[row["case"]].names)
        out.append(
            f"| {cell(row['what'])} | {wanted} | {cell(row['message'])} "
            f"| {'yes' if row['names_it'] else '**no**'} |"
        )
    out.append("")

    out.append("## The sentences this page is comparing against")
    out.append("")
    out.append(
        f"Quoted from JVMS {edition} and checked against the chapter on every run. A "
        f"fragment that stops appearing in the section it is attributed to stops the "
        f"generator rather than being reworded."
    )
    out.append("")
    out.append("| JVMS | what it states | broken by |")
    out.append("|---|---|---|")
    seen: list[tuple[str, str]] = []
    for case in ORDER:
        key = (SPEC[case].rule, " ... ".join(SPEC[case].states))
        if key not in seen:
            seen.append(key)
    for rule, quoted in seen:
        cases = [row["what"] for row in table
                 if SPEC[row["case"]].rule == rule
                 and " ... ".join(SPEC[row["case"]].states) == quoted]
        out.append(f"| {rule} | {cell(quoted)} | {cell('; '.join(cases))} |")
    out.append("")

    strange = invented(data, opcodes())
    if strange:
        out.append("## Instructions the message names that the class file cannot contain")
        out.append("")
        out.append(
            "The verifier prints the instruction it was looking at. These names are not "
            "in the opcode table at `docs/generated/opcodes.md`, which is JVMS "
            f"{edition} chapter 7. They are the codes HotSpot rewrites into a method "
            "after parsing, so a reader who takes the message to the specification is "
            "looking for something that was never allowed in a file."
        )
        out.append("")
        out.append("| what was broken | the message names |")
        out.append("|---|---|")
        for case, name in strange:
            what = next(row["what"] for row in table if row["case"] == case)
            out.append(f"| {cell(what)} | `{name}` |")
        out.append("")

    out.append("## Flipping one bit, 256 times")
    out.append("")
    accepted = [flip for flip in flips if flip["stage"] == "accepted"]
    out.append(
        f"One bit of one byte, chosen from a fixed seed so the same 256 mutants come "
        f"back on any machine. {len(accepted)} of them load and link with nothing said, "
        f"which is the number worth carrying: a class file can be wrong in a way no "
        f"reader of it will ever mention."
    )
    out.append("")
    out.append("| region | bytes | flips | loaded anyway | what the rest threw |")
    out.append("|---|---|---|---|---|")
    for region in by_region(data):
        errors = ", ".join(f"`{name}`" for name in region["errors"]) or "nothing"
        out.append(
            f"| {region['region']} | {region['bytes']} | {region['flips']} "
            f"| {region['accepted']} | {errors} |"
        )
    out.append("")
    out.append("| where it stopped | flips |")
    out.append("|---|---|")
    for stage, count in counted(flips, "stage"):
        out.append(f"| {STAGE_TITLE.get(stage, stage)} | {count} |")
    out.append("")
    out.append("| error | flips |")
    out.append("|---|---|")
    for error, count in counted(flips, "error"):
        out.append(f"| `{error}` | {count} |")
    out.append("")

    killed = sorted({index for d in data.values() for index in d["killed_the_jvm"]})
    out.append(
        f"{len(killed)} of the {len(flips)} killed the JVM rather than being refused by "
        f"it." if killed else
        f"None of the {len(flips)} killed the JVM. Every one of them was either accepted "
        f"or refused with a message."
    )
    out.append("")

    out.append("## Where this came from")
    out.append("")
    out.append("| source | what it gave | sha256 |")
    out.append("|---|---|---|")
    for which in CHAPTERS:
        out.append(
            f"| JVMS {edition} chapter {which}, {titles[which]} "
            f"| {sum(1 for name in prose if name.split('.')[0] == which)} sections "
            f"| `{hashes[which][:16]}` |"
        )
    for name in sorted(data):
        path = data[name]["file"]
        raw = (ROOT / path).read_bytes()
        out.append(
            f"| `{path}`, measured on {name} | {len(ORDER)} cases and {len(flips)} flips "
            f"| `{hashlib.sha256(raw).hexdigest()[:16]}` |"
        )
    out.append("")
    out.append(
        f"The seed class file is major version {one['class_file_major']}, which is what "
        f"the pinned JDK writes. The pinned specification edition describes class files "
        f"up to major version {pinned['jvms_class_file_major']}, so the file being "
        f"broken here is two versions newer than the document its rules are quoted from."
    )
    out.append("")
    return "\n".join(out)


def render(data: dict[str, dict]) -> str:
    table = rows(data)
    differs = [row for row in table if not row["same_error"]]
    silent = [row for row in table if not row["names_it"]]
    lines = [f"{len(ORDER)} cases on {', '.join(sorted(data))}"]
    for row in differs:
        lines.append(f"  {row['case']}: {row['expected']} named, {row['error']} thrown")
    lines.append(f"  {len(silent)} messages name none of the words their rule uses")
    flips = next(iter(data.values()))["random"]
    accepted = sum(1 for flip in flips if flip["stage"] == "accepted")
    lines.append(f"  {accepted} of {len(flips)} random flips loaded anyway")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed page is stale")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the summary without writing anything")
    args = ap.parse_args(argv)

    pinned = pin()
    data = results()

    missing = set(SPEC) ^ set(next(iter(data.values()))["cases"])
    if missing:
        sys.exit(
            f"the probe and this generator do not agree on the cases: "
            f"{', '.join(sorted(missing))}"
        )
    if set(ORDER) != set(SPEC):
        sys.exit("ORDER and SPEC list different cases")
    for name, one in data.items():
        if one["java_build"] != pinned["jdk_build"]:
            sys.exit(f"{name} measured java {one['java_build']} and the pin says "
                     f"{pinned['jdk_build']}")
        if one["class_file_major"] != pinned["jdk_class_file_major"]:
            sys.exit(f"{name} wrote major version {one['class_file_major']} and the pin "
                     f"says {pinned['jdk_class_file_major']}")
        if one["seed_run"]["stage"] != "accepted":
            sys.exit(f"{name} could not run its unbroken seed class")

    if args.show:
        print(render(data))
        return 0

    prose, hashes = chapters(pinned["jvms_edition"])
    titles = against_the_index(hashes)
    verify(prose)
    text = build(data, prose, hashes, titles, pinned)

    if args.check:
        if not OUTPUT.is_file():
            print(f"{OUTPUT} is missing, run tools/gen_fuzz.py", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT} does not match {RESULTS} and JVMS "
                  f"{pinned['jvms_edition']}, run tools/gen_fuzz.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is current at JVMS {pinned['jvms_edition']}")
        print(render(data))
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
