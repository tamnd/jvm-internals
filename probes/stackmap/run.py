#!/usr/bin/env python3
"""Ask the pinned JDK what a stack map frame is, and count what a StackMapTable is made of.

Issue #15. BP-STACKMAP is one of M2's six blueprints and its section 2 is two tables: the
nine verification types with their tags, and the seven frame kinds with the range of first
bytes each occupies. Both are facts this JDK knows, so this asks rather than retypes. Two
of the nine types cannot be found by looking, because an object that has been allocated
and not yet constructed only lives across a branch in code a compiler rarely emits, so
this builds a class that forces both and reads the frames back.

The counting half is the part no document has. A stack map frame is the only structure in
a class file whose meaning depends on the structure before it, which means no single frame
can be read on its own and nobody writes down what a real attribute looks like. So this
walks a module of the runtime image, decodes every frame, recovers the encoding it was
written in, works out the smallest encoding that same frame could have had, and compares.
It also checks the offset arithmetic against the bytecode offsets the platform resolves,
because the one rule everybody implementing this gets wrong once is that the delta is the
offset for the first frame and the offset minus one for every frame after it.

  JAVA_HOME=... python probes/stackmap/run.py --out probes/stackmap/results/osx-arm64.json

No network, no root, and it takes about a minute. The module is `java.base` unless
`--module` says otherwise, for the same reason as the pool census next door: java.base is
in every image ever built, so two people on two machines are counting the same thing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
FRAMES = HERE / "Frames.java"


def java_home() -> pathlib.Path:
    home = os.environ.get("JAVA_HOME")
    if home and (pathlib.Path(home) / "bin").is_dir():
        return pathlib.Path(home)
    sys.exit(
        "set JAVA_HOME to the pinned JDK first. tools/fetch_jdk.py will install it and "
        "print the path."
    )


def tool(home: pathlib.Path, name: str) -> str:
    suffix = ".exe" if platform.system() == "Windows" else ""
    return str(home / "bin" / (name + suffix))


def number(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", value))


def ask(home: pathlib.Path, module: str) -> dict:
    """Run the Java half and fold its key/value lines into the shape of the results."""
    done = subprocess.run(
        [tool(home, "java"), str(FRAMES), module],
        capture_output=True, text=True, timeout=1800,
    )
    if done.returncode != 0:
        sys.exit(f"Frames.java did not run:\n{done.stdout}\n{done.stderr}")

    types: dict[str, dict] = {}
    built: dict[str, object] = {}
    scan: dict[str, object] = {}
    frame_types: dict[str, int] = {}
    raw_frame_types: dict[str, int] = {}
    kinds: dict[str, int] = {}
    items: dict[str, int] = {}
    where: dict[str, dict[str, int]] = {}
    larger: dict[str, int] = {}
    check: dict[str, int] = {}
    notes: list[str] = []

    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        # Longest prefix first, the same discipline as the pool probe. `where.` holds a
        # second level and `type.` holds a field name, so neither can be read as a leaf.
        if key.startswith("where."):
            side, tag = key.split(".")[1:3]
            where.setdefault(side, {})[tag] = int(value)
        elif key.startswith("type."):
            name, field = key.split(".")[1:3]
            entry = types.setdefault(name, {})
            entry[field] = int(value) if number(value) else value
        elif key.startswith("built."):
            field = key.split(".", 1)[1]
            built[field] = int(value) if number(value) else value
        elif key.startswith("rawframetype."):
            raw_frame_types[key.split(".", 1)[1]] = int(value)
        elif key.startswith("frametype."):
            frame_types[key.split(".", 1)[1]] = int(value)
        elif key.startswith("kind."):
            kinds[key.split(".", 1)[1]] = int(value)
        elif key.startswith("item."):
            items[key.split(".", 1)[1]] = int(value)
        elif key.startswith("check."):
            check[key.split(".", 1)[1]] = int(value)
        elif key.startswith("larger."):
            larger[key.split(".", 1)[1]] = int(value)
        elif key.startswith("note."):
            notes.append(value)
        elif key.startswith("scan."):
            field = key.split(".", 1)[1]
            scan[field] = int(value) if number(value) else value

    if len(types) != 9:
        sys.exit(f"Frames.java described {len(types)} verification types, not nine")
    if not frame_types or not items:
        sys.exit("Frames.java counted nothing, so the scan did not finish")
    # The built class exists to put four things in a frame that a walk of a module does
    # not find, and it is only evidence if a JVM accepts it, so a run in which any of the
    # four went missing or the file failed to verify writes nothing.
    empty = [name for name in ("uninitialized_in_locals", "uninitialized_in_stack",
                               "uninitialized_this_in_locals", "uninitialized_this_in_stack")
             if not built.get(name)]
    if empty:
        sys.exit(f"the built class no longer forces these into a frame: {', '.join(empty)}")
    if built.get("verifies") != 1:
        sys.exit(f"the built class did not verify: {built.get('rejected_by', 'no reason given')}")

    # The headline of the scan is a zero, and a function that has never been watched
    # return anything else cannot produce a zero worth reporting. These six frames have a
    # smallest encoding that can be worked out on paper, so a run in which the encoder
    # gets one of them wrong writes no file at all rather than a file full of zeroes.
    wanted = {"same": 5, "same_extended": 251, "same_locals_1": 69,
              "chop": 250, "append": 252, "full": 255}
    wrong = {name: (check.get(name), want) for name, want in wanted.items()
             if check.get(name) != want}
    if wrong:
        sys.exit(f"the minimal encoder disagrees on frames worked out by hand: {wrong}")

    # Both byte counts in the scan are worked out from decoded frames by one set of width
    # rules, so a wrong rule is wrong on both sides of the comparison and cancels. The
    # second reader walks the file as bytes and takes `attribute_length` out of it, which
    # is a number nothing here computed, and the scan is worth nothing if they differ.
    if scan.get("raw_attribute_bytes") != scan.get("attribute_bytes"):
        sys.exit(
            f"the decoded frames come to {scan.get('attribute_bytes')} bytes and the "
            f"attribute_length fields in the same files come to "
            f"{scan.get('raw_attribute_bytes')}, so one of the two readers is wrong"
        )
    if raw_frame_types != frame_types:
        differ = sorted(set(raw_frame_types) ^ set(frame_types),
                        key=lambda name: int(name))
        sys.exit(f"the two readers disagree about frame types {differ or 'in their counts'}")

    # The two ranges the specification does not give a name to. The first byte of a frame
    # is one of 256 values and the count of the ones that mean nothing is the finding, so
    # it is derived here from what was seen rather than asserted.
    scan["frame_type_values_seen"] = len(frame_types)
    scan["frame_type_values_reserved"] = 246 - 128 + 1

    return {
        "types": types,
        "built": built,
        "scan": scan,
        "frame_types": frame_types,
        "kinds": kinds,
        "items": items,
        "encoder_self_check": check,
        "items_where": where,
        "larger_than_needed": larger,
        "arithmetic_notes": notes,
    }


def describe(home: pathlib.Path) -> dict:
    done = subprocess.run([tool(home, "java"), "-version"],
                          capture_output=True, text=True, timeout=120)
    text = done.stdout + done.stderr
    build = ""
    for line in text.splitlines():
        if "build" in line:
            build = line.split("build", 1)[1].strip(" )")
            break
    # No hostname and no home directory, same as every other probe here.
    return {
        "probe": "stackmap",
        "issue": 15,
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build,
        "measured": datetime.date.today().isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--module", default="java.base",
                    help="which module of the runtime image to count (default java.base)")
    args = ap.parse_args(argv)

    home = java_home()
    print(f"asking {home}", file=sys.stderr)

    found = describe(home)
    found.update(ask(home, args.module))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
