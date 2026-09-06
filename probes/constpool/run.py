#!/usr/bin/env python3
"""Ask the pinned JDK what a constant pool entry is, and count what a pool is made of.

Issue #15. BP-CONSTPOOL is one of M2's six blueprints, and its section 2 is the table of
entry kinds: the tag, how many slots the entry takes, whether `ldc` may load it, and what
it points at. Every one of those is a fact this JDK knows, so this asks rather than
retypes, by building one entry of every kind through `java.lang.classfile` and reading
back what the platform says about it. Two of the seventeen kinds do not occur anywhere in
`java.base`, which is why the table is built rather than found.

The counting half is the part no document has. The pool is more than half of a class file
by entry and nearly three fifths of it is `CONSTANT_Utf8`, and nothing anywhere says what
all those strings are. So this walks a module of the runtime image and works out what
every Utf8 entry is used as, which member kind each method handle points at, how many
slots are the unusable second half of a long or a double, and how often one pool holds the
same constant twice.

  JAVA_HOME=... python probes/constpool/run.py --out probes/constpool/results/osx-arm64.json

No network, no root, and it takes about a second. The module is `java.base` unless
`--module` says otherwise, for the same reason as the census next door: java.base is in
every image ever built, so two people on two machines are counting the same thing.
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
POOL = HERE / "Pool.java"


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


def held(value: str) -> list[str]:
    """`name->Utf8Entry,tag:int` back into a list, with the empty string meaning empty."""
    return [part for part in value.split(",") if part]


def ask(home: pathlib.Path, module: str) -> dict:
    """Run the Java half and fold its key/value lines into the shape of the results."""
    done = subprocess.run(
        [tool(home, "java"), str(POOL), module],
        capture_output=True, text=True, timeout=1800,
    )
    if done.returncode != 0:
        sys.exit(f"Pool.java did not run:\n{done.stdout}\n{done.stderr}")

    kinds: dict[str, dict] = {}
    scan: dict[str, object] = {}
    tags: dict[str, int] = {}
    roles: dict[str, int] = {}
    handles: dict[str, dict[str, int]] = {}
    dynamic: dict[str, int] = {}
    duplicate: dict[str, object] = {}
    duplicate_tags: dict[str, int] = {}
    repeated: list[dict[str, object]] = []
    unseen: list[dict[str, str]] = []

    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        # Longest prefix first. `duplicate.` is a prefix of `duplicate.tag.`, and reading
        # them the other way round files a per tag count under a field called `tag`.
        if key.startswith("kind."):
            name, field = key.split(".")[1:3]
            entry = kinds.setdefault(name, {})
            if field in ("tag", "slots"):
                entry[field] = int(value)
            elif field == "holds":
                entry[field] = held(value)
            else:
                entry[field] = value == "true"
        elif key.startswith("duplicate.tag."):
            duplicate_tags[key.split(".", 2)[2]] = int(value)
        elif key.startswith("duplicate."):
            duplicate[key.split(".", 1)[1]] = int(value)
        elif key.startswith("repeated."):
            where, entry = value.split("|", 1)
            repeated.append({"class": where, "entry": entry})
        elif key.startswith("unseen."):
            where, text = value.split("|", 1)
            unseen.append({"class": where, "text": text})
        elif key.startswith("handle."):
            reference_kind, points_at = key.split(".")[1:3]
            handles.setdefault(reference_kind, {})[points_at] = int(value)
        elif key.startswith("dynamic."):
            dynamic[key.split(".", 1)[1]] = int(value)
        elif key.startswith("utf8role."):
            roles[key.split(".", 1)[1]] = int(value)
        elif key.startswith("utf8."):
            scan[key.replace(".", "_", 1)] = int(value)
        elif key.startswith("tag."):
            tags[key.split(".", 1)[1]] = int(value)
        elif key.startswith("scan."):
            field = key.split(".", 1)[1]
            scan[field] = int(value) if number(value) else value

    if not kinds:
        sys.exit("Pool.java described no entry kinds, so its shape changed")
    if not tags or not roles:
        sys.exit("Pool.java counted nothing, so the scan did not finish")

    duplicate["by_tag"] = duplicate_tags
    duplicate["entries_seen"] = repeated
    scan["utf8_unaccounted"] = unseen
    return {
        "kinds": kinds,
        "scan": scan,
        "tags": tags,
        "utf8_roles": roles,
        "method_handles": handles,
        "dynamic": dynamic,
        "duplicates": duplicate,
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
        "probe": "constpool",
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
