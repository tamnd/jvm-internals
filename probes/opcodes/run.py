#!/usr/bin/env python3
"""Ask the pinned JDK what an opcode is, and count how often each one occurs.

Issue #15. M2's exit criterion says BP-BYTECODE section 3 is generated rather than
written, and a generated opcode table needs sources. This is one of the three:
`java.lang.classfile.Opcode` as the JDK on this machine defines it, plus a count of
every instruction in every method body of one module of that JDK's own runtime image.

The other two are the specification's opcode table in JVMS chapter 7 and HotSpot's
`bytecodes.cpp`, both of which are files on the internet and are read by
`tools/gen_opcodes.py`. This one has to run, because an enum is not a file.

The count is the part a reader cannot get anywhere else. A table of 205 opcodes read
top to bottom suggests they matter about equally, and they do not: the frequency of
the most common one and of the median one differ by four orders of magnitude, and
some are emitted by nothing at all. A lesson that spends equal time on each is a
lesson that spends most of its time on instructions the reader will never see.

  JAVA_HOME=... python probes/opcodes/run.py --out probes/opcodes/results/osx-arm64.json

No network, no root, and it takes about ten seconds. The module scanned is `java.base`
unless `--module` says otherwise, because java.base is in every image ever built and a
result measured against a module somebody has to install is not comparable with one
measured against a module everybody has.
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
OPCODES = HERE / "Opcodes.java"


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


def ask(home: pathlib.Path, module: str) -> dict:
    """Run the Java half and fold its key/value lines into the shape of the results."""
    done = subprocess.run(
        [tool(home, "java"), str(OPCODES), module],
        capture_output=True, text=True, timeout=900,
    )
    if done.returncode != 0:
        sys.exit(f"Opcodes.java did not run:\n{done.stdout}\n{done.stderr}")

    opcodes: dict[str, dict[str, object]] = {}
    counts: dict[str, int] = {}
    slots: dict[str, int] = {}
    scan: dict[str, object] = {}
    unreadable: list[str] = []

    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key.startswith("opcode."):
            _, name, field = key.split(".", 2)
            entry = opcodes.setdefault(name, {})
            if field in ("bytecode", "size"):
                entry[field] = int(value)
            elif field == "wide":
                entry[field] = value == "true"
            else:
                entry[field] = value
        elif key.startswith("count."):
            counts[key.split(".", 1)[1]] = int(value)
        elif key.startswith("slot."):
            slots[key.split(".", 1)[1]] = int(value)
        elif key == "unreadable":
            unreadable.append(value)
        elif key.startswith("scan."):
            field = key.split(".", 1)[1]
            scan[field] = int(value) if lump(value) else value

    if not opcodes:
        sys.exit("Opcodes.java printed no opcodes, so its output shape changed")
    missing = sorted(set(opcodes) - set(counts))
    if missing:
        sys.exit(f"no count for {', '.join(missing)}, so the scan did not finish")

    scan["unreadable_files"] = unreadable
    return {"opcodes": opcodes, "counts": counts, "widest_slot": slots, "scan": scan}


def lump(value: str) -> bool:
    """Whether a scan value is a number, including a negative one.

    `str.isdigit` says no to `-1`, and `-1` is the honest answer for the highest local
    variable slot in a scan that found no instruction naming one.
    """
    return bool(re.fullmatch(r"-?\d+", value))


def describe(home: pathlib.Path) -> dict:
    done = subprocess.run([tool(home, "java"), "-version"],
                          capture_output=True, text=True, timeout=120)
    text = done.stdout + done.stderr
    build = ""
    for line in text.splitlines():
        if "build" in line:
            build = line.split("build", 1)[1].strip(" )")
            break
    # No hostname and no home directory, same as every other probe here. Which machine
    # this ran on is not one of the things it measures.
    return {
        "probe": "opcodes",
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
