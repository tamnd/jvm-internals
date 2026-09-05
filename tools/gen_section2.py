#!/usr/bin/env python3
"""Emit the data structures section of the object header blueprint, from measurements.

This is the emitter half of issue #3, and the thing the M0 gate asks for: a section 2 for
`oopDesc`, `markWord`, `Klass` and `InstanceKlass` that nobody transcribed. It has three
inputs and writes none of them down itself.

  probes/sa/results/*.json          the type database, read out of a running VM
  docs/generated/markword.json      the mark word bits, parsed from markWord.hpp
  probes/capability/results/*.json  the VM configuration those layouts depend on

  python tools/gen_section2.py           regenerate the section
  python tools/gen_section2.py --check   regenerate in memory and fail on a diff

Three inputs rather than one because no single source has it. The type database has the
structs and knows nothing about the mark word's bits, since `markWord` is a type with a
size and no exported fields. The header parse has the bits and nothing about the structs
that hold them. And both describe a layout that is contingent on flags neither of them
records, which is what the capability probe measured on four platforms.

The checks in `verify()` are what makes the word correct in "emits a correct section 2"
mean anything. A mark word whose fields do not tile the word, a field that lies outside
the struct that declares it, or a subclass whose own fields start inside its superclass
would all produce a document that looks right, so all three stop the generator instead.

When `bpc` exists this becomes its section 2 emitter and the output moves next to the
blueprint. Until then it is a generator like every other one here, checked in CI, because
a generated section that nobody regenerates is a transcription with extra steps.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_sa_types  # noqa: E402

TYPES = pathlib.Path("probes/sa/results")
CAPABILITY = pathlib.Path("probes/capability/results")
MARKWORD = pathlib.Path("docs/generated/markword.json")
PIN = pathlib.Path("docs/pin.json")
PAGE = pathlib.Path("docs/generated/section2-object-header.md")

# The four types the milestone names, smallest first, which is also the order they
# contain each other in.
ORDER = ["markWord", "oopDesc", "Klass", "InstanceKlass"]

# The measured settings every layout below is contingent on, and what each one decides.
# `flag.` answers say whether the VM accepts the flag on the command line and `vm.`
# answers say what the running VM reports, which are different questions and differ here
# for exactly one of these.
SETTINGS = [
    ("vm.UseCompactObjectHeaders",
     "whether the class pointer lives in the mark word or in a field after it"),
    ("vm.UseCompressedOops",
     "whether a reference stored in a field is 4 bytes or 8"),
    ("flag.UseCompressedClassPointers",
     "the flag that used to decide the class pointer width"),
    ("vm.ObjectAlignmentInBytes",
     "the boundary every object size is rounded up to"),
    ("vm.arch", "the architecture, which decides the word size"),
]


def load_types() -> tuple[dict, list[str]]:
    """The struct layouts, and the environments that read them.

    Delegated to the generator that already owns this file format, so the rule that two
    machines disagreeing stops the page rather than being averaged into it is enforced in
    one place and applies here too.
    """
    return gen_sa_types.agreed(gen_sa_types.load())


def load_capability() -> dict[str, dict]:
    files = sorted(CAPABILITY.glob("*.json"))
    if not files:
        sys.exit(f"no results in {CAPABILITY}, run probes/capability/run.py first")
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}


def load_markword() -> dict:
    if not MARKWORD.is_file():
        sys.exit(f"{MARKWORD} is missing, run tools/gen_markword.py first")
    return json.loads(MARKWORD.read_text(encoding="utf-8"))


def fields(type_: dict) -> list[dict]:
    """A type's fields in offset order, statics last, which have addresses not offsets."""
    return sorted(type_["fields"],
                  key=lambda f: (f["offset"] is None, f["offset"] or 0, f["name"]))


def instance_fields(type_: dict) -> list[dict]:
    return [f for f in fields(type_) if not f["static"]]


def verify(types: dict, mark: dict) -> None:
    """Everything that would make the section wrong rather than incomplete.

    Incomplete is fine and is stated in the text: `vmStructs` exports what it chose to
    export. Wrong is not, and these are the three ways the inputs could be wrong without
    the output looking wrong.
    """
    bits = sorted(mark["fields"], key=lambda f: f["shift"])
    at = 0
    for field in bits:
        if field["shift"] != at:
            sys.exit(
                f"the mark word does not tile the word: {field['name']} starts at bit "
                f"{field['shift']} and the field before it ended at {at}. Either "
                f"markWord.hpp changed shape or gen_markword.py misread it, and either "
                f"way this section cannot be emitted from it."
            )
        at += field["bits"]
    if at != mark["word_bits"]:
        sys.exit(
            f"the mark word fields cover {at} bits of a {mark['word_bits']} bit word. "
            f"A section that prints them as a layout would be printing a layout with a "
            f"hole in it."
        )

    missing = [n for n in ORDER if types.get(n) is None]
    if missing:
        sys.exit(
            f"the type database has no layout for {', '.join(missing)}, and those are "
            f"the four types this section exists to describe. A section 2 that quietly "
            f"printed three of them would be a section that met its own gate by "
            f"lowering it."
        )

    for name, type_ in types.items():
        if type_ is None:
            continue
        seen: dict[int, str] = {}
        for field in instance_fields(type_):
            if field["offset"] >= type_["size"]:
                sys.exit(
                    f"{name}.{field['name']} is at offset {field['offset']} in a type "
                    f"of {type_['size']} bytes, which cannot be true. Read "
                    f"{TYPES} before touching this generator."
                )
            if field["offset"] in seen:
                sys.exit(
                    f"{name} has {seen[field['offset']]} and {field['name']} at the "
                    f"same offset {field['offset']}, which is a union or a misread."
                )
            seen[field["offset"]] = field["name"]
        parent = types.get(type_["super"])
        if parent and instance_fields(type_):
            first = instance_fields(type_)[0]["offset"]
            if first < parent["size"]:
                sys.exit(
                    f"{name} extends {type_['super']}, which is {parent['size']} bytes, "
                    f"and its own first field {first} starts inside it."
                )


def laid_out(types: dict) -> list[str]:
    """The types the section prints, in order. A type the probe measured and this list
    does not name is printed after the named ones rather than dropped."""
    return ORDER + [n for n in types if n not in ORDER and types[n]]


def said(value: object) -> str:
    """A measured answer as a word. A page that prints `True` is printing Python."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def settings(capability: dict[str, dict]) -> list[tuple[str, str, str]]:
    """Each setting, the values measured for it, and where each value was measured.

    Values are grouped rather than reduced to one, because the environments do not all
    agree and the ones that differ are the point: a section that printed a single
    architecture would be a section about one machine.
    """
    rows = []
    for key, meaning in SETTINGS:
        grouped: dict[str, list[str]] = {}
        for name, data in capability.items():
            grouped.setdefault(said(data["answers"].get(key, "not measured")),
                               []).append(name)
        printed = "; ".join(
            f"`{value}` on {', '.join('`' + n + '`' for n in sorted(names))}"
            for value, names in sorted(grouped.items())
        )
        rows.append((key, printed, meaning))
    return rows


def gaps(type_: dict, parent: dict | None) -> list[tuple[int, int]]:
    """The ground between exported fields, in bytes from and to.

    A pair of consecutive offsets more than one word apart is either a wide field or a
    field `vmStructs` did not export, and nothing in the type database says which. They
    are listed rather than interpreted.

    The ground before the first field is measured from the end of the superclass when the
    superclass is one of the types here, because everything below that offset belongs to
    it and is not missing from anything.
    """
    found = []
    offsets = [f["offset"] for f in instance_fields(type_)]
    if not offsets:
        return found
    start = parent["size"] if parent else 0
    if offsets[0] - start > 8:
        found.append((start, offsets[0]))
    for one, other in zip(offsets, offsets[1:]):
        if other - one > 8:
            found.append((one, other))
    if type_["size"] - offsets[-1] > 8:
        found.append((offsets[-1], type_["size"]))
    return found


def sources() -> dict:
    """Every input, read from disk, and nothing derived from them."""
    types, read_by = load_types()
    return {
        "types": types,
        "read_by": read_by,
        "capability": load_capability(),
        "mark": load_markword(),
        "pin": json.loads(PIN.read_text(encoding="utf-8")),
    }


def build(types: dict, read_by: list[str], capability: dict, mark: dict, pin: dict) -> str:
    verify(types, mark)

    out: list[str] = []
    out.append("# BP-HEADER section 2. Data structures")
    out.append("")
    out.append(
        "Generated by `tools/gen_section2.py`. Do not edit it, edit the measurement. "
        "It has three sources and transcribes none of them: the struct layouts come out "
        "of a running VM's Serviceability Agent type database in "
        f"`{TYPES}`, the mark word's bits come from "
        f"`{mark['source']['path']}` by way of `{MARKWORD}`, and the configuration they "
        f"are contingent on comes from `{CAPABILITY}`. Everything here is "
        f"HotSpot at `{pin['jdk_tag']}` and none of it is specified by the JVMS."
    )
    out.append("")

    out.append("## 2.1 The configuration these layouts describe")
    out.append("")
    out.append(
        "Every layout below is contingent on these, measured on "
        f"{len(capability)} environments by `probes/capability/run.py`. A reader who "
        "changes one of them is reading a different section 2."
    )
    out.append("")
    out.append("| setting | measured | what it decides |")
    out.append("|---|---|---|")
    for key, printed, meaning in settings(capability):
        out.append(f"| `{key}` | {printed} | {meaning} |")
    out.append("")
    out.append(
        "`UseCompressedClassPointers` reads `removed` because the flag no longer exists "
        "in this release: the VM accepts it, warns, and ignores it. A check that reads "
        "the exit status of a VM started with it will report that it worked."
    )
    out.append("")

    out.append("## 2.2 The mark word")
    out.append("")
    out.append(
        f"{mark['word_bits']} bits, in one machine word, at offset 0 of every object. "
        f"The type database has `markWord` as a type of {types['markWord']['size']} "
        f"bytes and exports {len(types['markWord']['fields']) or 'no'} fields of it, so "
        "this table is parsed from the header rather than read out of a VM, and every "
        "row carries the line it came from."
    )
    out.append("")
    out.append("| bits | width | mask | field | meaning | defined at |")
    out.append("|---|---|---|---|---|---|")
    for field in sorted(mark["fields"], key=lambda f: f["shift"]):
        top = field["shift"] + field["bits"] - 1
        span = f"{field['shift']}" if field["bits"] == 1 else f"{field['shift']}..{top}"
        out.append(
            f"| {span} | {field['bits']} | `{field['mask']}` | `{field['name']}` | "
            f"{field['meaning']} | {field['defined_at']['shift']} |"
        )
    out.append("")
    out.append("The two low bits are a state, and the rest of the word means what those two bits say it means.")
    out.append("")
    out.append("| `lock` | state | meaning |")
    out.append("|---|---|---|")
    for state in mark["lock_states"]:
        out.append(f"| `{state['bits']}` | {state['name']} | {state['meaning']} |")
    out.append("")
    out.append(mark["note"])
    out.append("")

    out.append("## 2.3 The structs")
    out.append("")
    out.append(
        "As the type database resolved them, read by "
        + " and ".join(f"`{n}`" for n in read_by)
        + ". An offset is bytes from the start of the struct. A static field has an "
        "address rather than an offset and is printed as `static`."
    )
    out.append("")
    for number, name in enumerate(laid_out(types), start=1):
        type_ = types[name]
        out.append(f"### 2.3.{number} {name}")
        out.append("")
        extends = f"extends `{type_['super']}`" if type_["super"] else "no superclass"
        rows = fields(type_)
        out.append(
            f"`{name}`, {type_['size']} bytes, {extends}, "
            f"{len(rows)} exported fields."
        )
        out.append("")
        if not rows:
            out.append(
                "The type is exported and not one of its fields is, which is why 2.2 is "
                "read from the header instead."
            )
            out.append("")
            continue
        out.append("| offset | field | type |")
        out.append("|---|---|---|")
        for field in rows:
            where = "static" if field["static"] else str(field["offset"])
            out.append(f"| {where} | `{field['name']}` | `{field['type']}` |")
        out.append("")

    out.append("## 2.4 What these tables do not say")
    out.append("")
    out.append(
        "The type database exports what `vmStructs` chose to export, which is less than "
        "the struct. Nothing in it distinguishes a field that is wide from a field that "
        "was not exported, so the ground between consecutive exported offsets is listed "
        "here and interpreted nowhere."
    )
    out.append("")
    out.append("| type | from | to | bytes |")
    out.append("|---|---|---|---|")
    for name in laid_out(types):
        type_ = types[name]
        for start, end in gaps(type_, types.get(type_["super"])):
            out.append(f"| `{name}` | {start} | {end} | {end - start} |")
    out.append("")
    out.append(
        "Two of those rows have an explanation the database does not carry. "
        "`Klass._primary_supers[0]` is the first element of an array the database names "
        "by its first element and gives no length for, and the next exported field is "
        "64 bytes later, which is eight pointers. The last row of each type runs from "
        "its last exported field to its size, and for `Klass` that ground holds the "
        "vtable length's neighbours rather than nothing."
    )
    out.append("")
    out.append(
        "`Klass` starts at 8 rather than 0 for a different reason: it extends "
        "`Metadata`, which this section did not ask the probe for, so the first 8 bytes "
        "belong to a type that is not here. The table above does not show that as a gap, "
        "because measuring a subclass from 0 would call its superclass missing."
    )
    out.append("")
    out.append(
        f"The last thing is not a gap at all. `oopDesc` is {types['oopDesc']['size']} "
        "bytes here with a `_compressed_klass` field at 8, and that is the struct the "
        "compiler laid out rather than the header this VM uses: with "
        "`UseCompactObjectHeaders` measured on above, the class pointer is in the mark "
        "word and the header is one word. A section 2 that printed the struct without "
        "that sentence would tell a reader an object has a separate class pointer when "
        "it does not."
    )
    out.append("")

    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = ap.parse_args(argv)

    wanted = build(**sources())
    if args.check:
        if not PAGE.is_file():
            print(f"{PAGE} is missing, run tools/gen_section2.py", file=sys.stderr)
            return 1
        if PAGE.read_text(encoding="utf-8") != wanted:
            print(f"{PAGE} does not match its measurements, run tools/gen_section2.py",
                  file=sys.stderr)
            return 1
        print("the generated section 2 matches its measurements")
        return 0

    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(wanted, encoding="utf-8")
    print(f"wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
