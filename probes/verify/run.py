#!/usr/bin/env python3
"""Ask the pinned JVM which verification types it will accept in place of which others.

Issue #15. BP-VERIFY is the last of M2's six blueprints and its subject is the assignability
relation of JVMS 4.10.1.2, which the specification gives as nine Prolog rules over fourteen
type terms. Nine rules is small enough to paraphrase and paraphrasing them is what every
account of the verifier does, so this does not. Ten of the fourteen terms can be written
into a class file, this builds one class per ordered pair of those ten plus the six shapes
of reference type worth telling apart, and the answer for each pair is whether the JVM
loaded the class or threw VerifyError. The relation is the table, not a reading of it.

Three more measurements go with the grid. A class that puts a String in a local the frame
declares as an interface and then calls a method on it, which shows what the accepted half
of the table costs at run time. The same wrong frame at five class file versions, which is
where the two verifiers and the failover between them become visible. And a count of the
instructions in a module of the runtime image that carry a run time type check, which is
the size of the gap the grid opens.

  JAVA_HOME=... python probes/verify/run.py --out probes/verify/results/osx-arm64.json

No network, no root, about ten seconds. The module is `java.base` unless `--module` says
otherwise, for the same reason as the two probes next door: it is in every image ever
built, so two people on two machines are counting the same thing.
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
TYPES = HERE / "Types.java"


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
        [tool(home, "java"), str(TYPES), module],
        capture_output=True, text=True, timeout=1800,
    )
    if done.returncode != 0:
        sys.exit(f"Types.java did not run:\n{done.stdout}\n{done.stderr}")

    cells: dict[str, int] = {}
    kinds: dict[str, str] = {}
    grid: dict[str, int] = {}
    shapes: dict[str, int] = {}
    check: dict[str, int] = {}
    prop: dict[str, int] = {}
    triples: list[str] = []
    pairs: list[str] = []
    unmeasurable: dict[str, str] = {}
    hole: dict[str, object] = {}
    failover: dict[str, dict] = {}
    census: dict[str, object] = {}
    ops: dict[str, int] = {}

    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        # Longest prefix first, the same discipline as the two probes next door.
        # `failover.` holds a version and then a field, and a type name can contain a dot,
        # so neither can be split on the first dot and read as a leaf.
        if key.startswith("nontransitive."):
            triples.append(key.split(".", 1)[1])
        elif key.startswith("unmeasurable."):
            unmeasurable[key.split(".", 1)[1]] = value
        elif key.startswith("mutual."):
            pairs.append(key.split(".", 1)[1])
        elif key.startswith("property."):
            prop[key.split(".", 1)[1]] = int(value)
        elif key.startswith("failover."):
            major, field = key.split(".")[1:3]
            entry = failover.setdefault(major, {})
            entry[field] = int(value) if number(value) else value
        elif key.startswith("census."):
            field = key.split(".", 1)[1]
            census[field] = int(value) if number(value) else value
        elif key.startswith("shape."):
            shapes[key.split(".", 1)[1]] = int(value)
        elif key.startswith("check."):
            check[key.split(".", 1)[1]] = int(value)
        elif key.startswith("cell."):
            cells[key.split(".", 1)[1]] = int(value)
        elif key.startswith("type."):
            kinds[key.split(".")[1]] = value
        elif key.startswith("grid."):
            grid[key.split(".", 1)[1]] = int(value)
        elif key.startswith("hole."):
            field = key.split(".", 1)[1]
            hole[field] = int(value) if number(value) else value
        elif key.startswith("op."):
            ops[key.split(".", 1)[1]] = int(value)

    if len(kinds) != 16 or grid.get("cells") != 256:
        sys.exit(f"Types.java built a {len(kinds)} type grid of "
                 f"{grid.get('cells')} cells, not sixteen of 256")
    if len(cells) != grid.get("cells"):
        sys.exit(f"the grid says {grid.get('cells')} cells and {len(cells)} came back")
    # A cell is a class the JVM either loaded or refused with VerifyError. Anything else is
    # a class this harness built wrong, and one of those makes the whole table a guess, so
    # a run with any writes no file rather than a file with a hole in it.
    if unmeasurable:
        sys.exit(f"these cells did not answer: {unmeasurable}")

    # 256 answers from a machine, and nothing else here would notice if the harness quietly
    # built the same class 256 times. These twelve can be worked out from JVMS 4.10.1.2 on
    # paper, so a run in which the machine disagrees with the paper writes nothing.
    wrong = sorted(name for name, ok in check.items() if name != "wrong" and not ok)
    if wrong:
        sys.exit(f"the measured grid disagrees with pairs worked out by hand: {wrong}")

    # The three structural claims are counted twice, once in Java over the map it built as
    # it went and once here over the cells as they were parsed back out of the text. The
    # second reader shares no code with the first, and a claim about the shape of the
    # relation is worth nothing if the two readers of the same 256 cells disagree.
    names = sorted(kinds)
    yes = {name for name, got in cells.items() if got == 1}
    again = {
        "reflexive": sum(1 for n in names if f"{n}->{n}" in yes),
        "nontransitive": sum(
            1
            for a in names for b in names for c in names
            if f"{a}->{b}" in yes and f"{b}->{c}" in yes and f"{a}->{c}" not in yes
        ),
        "mutual": sum(
            1
            for i, a in enumerate(names) for b in names[i + 1:]
            if f"{a}->{b}" in yes and f"{b}->{a}" in yes
        ),
    }
    if again != prop:
        sys.exit(f"the two readers of the same grid disagree: Java said {prop} and "
                 f"a recount of the cells says {again}")
    if len(yes) != grid.get("assignable"):
        sys.exit(f"the grid says {grid.get('assignable')} assignable and the cells "
                 f"come to {len(yes)}")

    # The hole is the whole point of the accepted half of the table and it only shows
    # anything if all three of its parts behave: the class verifies, the call verifies, and
    # the wrongness surfaces at run time instead of never.
    if hole.get("verifies") != 1 or hole.get("call_verifies") != 1:
        sys.exit(f"the interface hole no longer verifies: {hole}")
    if "IncompatibleClassChangeError" not in str(hole.get("throws", "")):
        sys.exit(f"the interface hole did not fail at run time: {hole.get('throws')}")
    # The control is the same class with a class where the interface was. If that verifies
    # too then the hole is not about interfaces and the measurement means nothing.
    if hole.get("control_verifies") != 0:
        sys.exit("the control class verified, so the hole is not about interfaces")

    return {
        "types": kinds,
        # The order the grid was built in, kept because a sorted results file loses it and
        # a table whose rows and columns are alphabetical puts `top` between `String` and
        # `uninitializedThis`, which is a table nobody can read.
        "order": list(kinds),
        "grid": grid,
        "cells": cells,
        "refusal_shapes": shapes,
        "properties": prop,
        "nontransitive_triples": sorted(triples),
        "mutually_assignable": sorted(pairs),
        "worked_by_hand": check,
        "hole": hole,
        "failover": failover,
        "census": census,
        "ops": ops,
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
        "probe": "verify",
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
