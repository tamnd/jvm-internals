#!/usr/bin/env python3
"""Ask the pinned JDK what a class file may contain, and count what one does contain.

Issue #15. M2's exit criterion says BP-CLASSFILE section 2 is generated rather than
written, and section 2 is the structure: the header, the constant pool, the attributes
and the access flags. This is the source for it that has to run.

Two things come out of a JVM and nowhere else. `java.lang.reflect.AccessFlag` knows
every access flag, its mask, and which parts of a class file it may appear in, and it
knows that per class file version rather than once, which is the only machine readable
form of a fact the specification states in prose. `java.lang.classfile.Attributes` knows
which attributes the platform's own library can model and how each behaves under a
transform.

The rest is a count. The specification lists 17 constant pool tags and thirty attributes
and says nothing about which are in every class file and which are in four, so this walks
a module of the runtime image and counts constant pool entries by tag, attributes by name
and location, access flag bits by location, and the class file version of every class.

  JAVA_HOME=... python probes/classfile-census/run.py --out probes/classfile-census/results/osx-arm64.json

No network, no root, and it takes about fifteen seconds. The module is `java.base` unless
`--module` says otherwise, because java.base is in every image ever built and a result
measured against a module somebody has to install is not comparable with one measured
against a module everybody has.
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
CENSUS = HERE / "Census.java"


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


def names(value: str) -> list[str]:
    """A comma joined list back into a list, with the empty string meaning empty.

    `"".split(",")` is `[""]`, which would turn "this flag belongs nowhere" into "this
    flag belongs somewhere called nothing", and that is the one answer on the page that
    a reader would stop and reread.
    """
    return [part for part in value.split(",") if part]


def ask(home: pathlib.Path, module: str) -> dict:
    """Run the Java half and fold its key/value lines into the shape of the results."""
    done = subprocess.run(
        [tool(home, "java"), str(CENSUS), module],
        capture_output=True, text=True, timeout=1800,
    )
    if done.returncode != 0:
        sys.exit(f"Census.java did not run:\n{done.stdout}\n{done.stderr}")

    api: dict[str, object] = {}
    releases: dict[str, int] = {}
    flags: dict[str, dict] = {}
    flag_locations: dict[str, dict[str, list[str]]] = {}
    attributes: dict[str, dict] = {}
    scan: dict[str, object] = {}
    versions: dict[str, int] = {}
    version_examples: dict[str, list[str]] = {}
    tags: dict[str, int] = {}
    attribute_counts: dict[str, dict[str, int]] = {}
    unknown_attributes: dict[str, int] = {}
    flag_counts: dict[str, dict[str, int]] = {}
    stray: dict[str, dict] = {}
    unreadable: list[str] = []

    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        # Longest prefix first, because `flag.` is a prefix of nothing but `version.` is
        # a prefix of `versionexample.` if the tests are run in the wrong order.
        if key.startswith("versionexample."):
            version_examples[key.split(".", 1)[1]] = names(value)
        elif key.startswith("version."):
            versions[key.split(".", 1)[1]] = int(value)
        elif key.startswith("strayexample."):
            stray.setdefault(key.split(".", 1)[1], {})["examples"] = names(value)
        elif key.startswith("stray."):
            stray.setdefault(key.split(".", 1)[1], {})["count"] = int(value)
        elif key.startswith("flagseen."):
            location, mask = key.split(".")[1:3]
            flag_counts.setdefault(location, {})[mask] = int(value)
        elif key.startswith("flagat."):
            name, release = key.split(".")[1:3]
            flag_locations.setdefault(name, {})[release] = names(value)
        elif key.startswith("flag."):
            name, field = key.split(".")[1:3]
            entry = flags.setdefault(name, {})
            if field == "mask":
                entry[field] = int(value)
            elif field == "source_modifier":
                entry[field] = value == "true"
            else:
                entry[field] = names(value)
        elif key.startswith("attribute."):
            name, field = key.split(".")[1:3]
            entry = attributes.setdefault(name, {})
            entry[field] = value == "true" if field == "allow_multiple" else value
        elif key.startswith("attr."):
            location, name = key.split(".", 2)[1:3]
            attribute_counts.setdefault(location, {})[name] = int(value)
        elif key.startswith("unknown."):
            unknown_attributes[key.split(".", 1)[1]] = int(value)
        elif key.startswith("release."):
            releases[key.split(".", 1)[1]] = int(value)
        elif key.startswith("tag."):
            tags[key.split(".", 1)[1]] = int(value)
        elif key.startswith("api."):
            api[key.split(".", 1)[1]] = int(value) if number(value) else value
        elif key == "unreadable":
            unreadable.append(value)
        elif key.startswith("scan."):
            field = key.split(".", 1)[1]
            scan[field] = int(value) if number(value) else value

    if not flags or not attributes:
        sys.exit("Census.java printed no flags or no attributes, so its shape changed")
    if not tags or not attribute_counts:
        sys.exit("Census.java counted nothing, so the scan did not finish")

    scan["unreadable_files"] = unreadable
    return {
        "api": api,
        "releases": releases,
        "flags": flags,
        "flag_locations": flag_locations,
        "attributes": attributes,
        "scan": scan,
        "versions": versions,
        "version_examples": version_examples,
        "tags": tags,
        "attribute_counts": attribute_counts,
        "unknown_attributes": unknown_attributes,
        "flag_counts": flag_counts,
        "stray_flags": stray,
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
        "probe": "classfile-census",
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
