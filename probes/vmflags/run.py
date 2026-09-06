#!/usr/bin/env python3
"""Ask a product JVM which flags it actually has, what they are set to, and who set it.

Issue #15. M2 wants BP-FLAGS generated from `globals.hpp` with types, defaults and
origins. The header is half of that. It declares about twelve hundred flags for the
platform this runs on, and a product build of the same source reports eight hundred,
because a `develop` flag is compiled out, a garbage collector that was not built takes
its flags with it, and three flags are declared inside a macro that only expands when
JFR is present. No amount of reading the header tells you which eight hundred.

The other half is only in a running VM, and there are four things in it:

  the origin      whether a value is the header's default, something ergonomics chose
                  from the machine, or something the command line said
  the value       which for an ergonomic flag is a function of processors and memory,
                  so it is machine data and not JDK data
  the range       the bounds the flag was declared with, which the header states and
                  the VM enforces, and only the VM will tell you the enforcement text
  the gate        what happens when you set a flag you are not allowed to set, which
                  is a different message for each of six reasons

So this runs the pinned `java` a dozen times and records all four. The baseline is run
three times rather than once, because at least one flag reports a different value on
every run of the same command, and a probe that samples that once is a probe that says
a random number is a fact.

  JAVA_HOME=... python probes/vmflags/run.py --out probes/vmflags/results/osx-arm64.json

No network, no root, about ten seconds. Every run is `-version`, so nothing is compiled
and nothing is written anywhere.
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

# `     bool UseTLAB   = true    {product} {default}` and the `:=` spelling that marks a
# value the VM changed. The type is one token, the value is whatever is left before the
# first brace, and a `ccstr` flag with no value leaves that empty rather than absent.
FLAG = re.compile(
    r"^\s*(?P<type>\S+)\s+(?P<name>\w+)\s+(?P<assign>:?=)\s*(?P<value>.*?)\s*"
    r"\{(?P<kind>[^}]*)\}\s*\{(?P<origin>[^}]*)\}\s*$"
)

# The same line from `-XX:+PrintFlagsRanges`, where a flag with no declared range still
# gets a pair of brackets with nothing between them.
RANGE = re.compile(
    r"^\s*(?P<type>\S+)\s+(?P<name>\w+)\s+\[\s*(?P<min>.*?)\s*\.\.\.\s*(?P<max>.*?)\s*\]"
)

# One option each, chosen because each moves a different part of the VM: the first turns
# the compilers off, the second and third change the two inputs ergonomics reads, and the
# last two swap the collector. What is being measured is not the option, it is how many
# other flags the option moves without saying so.
SCENARIOS = [
    ("interpreter only", ["-Xint"]),
    ("a small heap", ["-Xmx256m"]),
    ("one processor", ["-XX:ActiveProcessorCount=1"]),
    ("the serial collector", ["-XX:+UseSerialGC"]),
    ("the Z collector", ["-XX:+UseZGC"]),
]

# Six ways to be told no. The flag names are checked against the reported table further
# down, so a release that renames one of them fails here rather than quietly measuring
# the wrong gate.
GATES = [
    ("no such flag", ["-XX:+NoSuchFlagHere"], None),
    ("a develop flag", ["-XX:+TraceBytecodes"], None),
    ("a diagnostic flag", ["-XX:+PrintInlining"], "PrintInlining"),
    ("an experimental flag", ["-XX:+AlwaysSafeConstructors"], "AlwaysSafeConstructors"),
    ("outside the range", ["-XX:ObjectAlignmentInBytes=7"], "ObjectAlignmentInBytes"),
    ("inside the range and refused", ["-XX:ObjectAlignmentInBytes=9"],
     "ObjectAlignmentInBytes"),
    ("a collector that is not in this build", ["-XX:+UseShenandoahGC"],
     "UseShenandoahGC"),
]


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


def run(home: pathlib.Path, options: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [tool(home, "java"), *options, "-version"],
        capture_output=True, text=True, timeout=300,
    )


def table(home: pathlib.Path, options: list[str]) -> dict[str, dict]:
    """Every flag a run reports, by name."""
    done = run(home, [*options, "-XX:+PrintFlagsFinal"])
    if done.returncode != 0:
        return {}
    found = {}
    for line in done.stdout.splitlines():
        match = FLAG.match(line)
        if match:
            found[match.group("name")] = {
                "type": match.group("type"),
                "value": match.group("value"),
                # `{C2 product}` and `{product lp64_product}` are two words in one pair
                # of braces. Splitting here means the generator never has to.
                "kind": match.group("kind").split(),
                "origin": match.group("origin"),
                "assigned": match.group("assign") == ":=",
            }
    return found


def steady(home: pathlib.Path, runs: int = 3) -> tuple[dict[str, dict], list[str]]:
    """The baseline table, with the flags that will not sit still marked as such.

    Running the same command twice and getting a different number back is the sort of
    thing a table quietly hides. Here it is measured: a flag whose value differs between
    identical runs keeps its type, kind and origin and loses its value, because there is
    no single value to report and printing one of them would be the wrong answer twice
    out of three times.
    """
    samples = [table(home, []) for _ in range(runs)]
    first = samples[0]
    moving = sorted(
        name for name in first
        if any(other.get(name, {}).get("value") != first[name]["value"]
               for other in samples[1:])
    )
    for name in moving:
        first[name]["value"] = None
        first[name]["unstable"] = True
    return first, moving


def ranges(home: pathlib.Path) -> dict[str, dict[str, str]]:
    done = run(home, ["-XX:+PrintFlagsRanges"])
    if done.returncode != 0:
        sys.exit("-XX:+PrintFlagsRanges did not run")
    found = {}
    for line in done.stdout.splitlines():
        match = RANGE.match(line)
        # A flag with no declared range prints empty brackets. Recording it as a range
        # with no bounds would make "has a range" unanswerable, so it is left out and
        # the count of what is left out is the interesting number.
        if match and (match.group("min") or match.group("max")):
            found[match.group("name")] = {"min": match.group("min"),
                                          "max": match.group("max")}
    return found


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def about(text: str, flag: str) -> str:
    """The line of a refusal that is about the flag rather than about the consequences.

    Most of these gates say what is wrong on the first line. One of them prints `Error
    occurred during initialization of VM` first and the reason second, so taking the
    first line would record the same uninformative sentence for a case that is different
    from all the others. The line that names the flag is the one that says something.
    """
    for line in text.splitlines():
        if flag and flag in line:
            return line.strip()
    return first_line(text)


def gates(home: pathlib.Path, reported: dict[str, dict]) -> list[dict]:
    """What the VM says when a flag may not be set, and the one case where it may."""
    found = []
    for name, options, needs in GATES:
        if needs and needs not in reported:
            sys.exit(
                f"the gate named {name!r} sets {needs}, which this build does not "
                f"report. The flag was renamed or removed upstream, which is a real "
                f"finding: pick another flag of the same kind and say so."
            )
        done = run(home, options)
        # `-XX:+NoSuchFlagHere` and `-XX:ObjectAlignmentInBytes=7` both name one flag.
        named = re.sub(r"^-XX:[+-]?", "", options[-1]).split("=")[0]
        found.append({
            "name": name,
            "options": options,
            "exit": done.returncode,
            "message": about(done.stdout + done.stderr, named),
            "lines": [line.strip() for line in (done.stdout + done.stderr).splitlines()
                      if line.strip()][:6],
        })
    unlocked = run(home, ["-XX:+UnlockDiagnosticVMOptions", "-XX:-PrintInlining"])
    found.append({
        "name": "the same diagnostic flag, unlocked",
        "options": ["-XX:+UnlockDiagnosticVMOptions", "-XX:-PrintInlining"],
        "exit": unlocked.returncode,
        # The VM prints its version banner on success and there is nothing to quote, so
        # the exit status is the whole result.
        "message": "" if unlocked.returncode == 0 else first_line(unlocked.stderr),
        "lines": [],
    })
    return found


def scenarios(home: pathlib.Path, baseline: dict[str, dict]) -> list[dict]:
    """For each option, which other flags it moved."""
    found = []
    for name, options in SCENARIOS:
        other = table(home, options)
        if not other:
            # A collector that is not in this build is a fact about the build, not a
            # failed measurement, so it is recorded rather than skipped silently.
            found.append({"name": name, "options": options, "supported": False})
            continue
        changed = {
            flag: {"from": baseline[flag]["value"], "to": other[flag]["value"]}
            for flag in sorted(baseline)
            # A flag that moves on its own moves here too, and counting it as something
            # the option did would inflate every scenario by the same wrong amount.
            if flag in other and not baseline[flag].get("unstable")
            and other[flag]["value"] != baseline[flag]["value"]
        }
        origins: dict[str, int] = {}
        for entry in other.values():
            origins[entry["origin"]] = origins.get(entry["origin"], 0) + 1
        found.append({
            "name": name,
            "options": options,
            "supported": True,
            "reported": len(other),
            "changed": changed,
            "origins": origins,
            "only_here": sorted(set(other) - set(baseline)),
            "only_in_baseline": sorted(set(baseline) - set(other)),
        })
    return found


def tally(reported: dict[str, dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in reported.values():
        value = entry[field]
        for word in (value if isinstance(value, list) else [value]):
            counts[word] = counts.get(word, 0) + 1
    return dict(sorted(counts.items()))


def describe(home: pathlib.Path) -> dict:
    done = subprocess.run([tool(home, "java"), "-version"],
                          capture_output=True, text=True, timeout=120)
    text = done.stdout + done.stderr
    build = ""
    for line in text.splitlines():
        if "build" in line:
            build = line.split("build", 1)[1].strip(" )")
            break
    return {
        "probe": "vmflags",
        "issue": 15,
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build,
        "measured": datetime.date.today().isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    args = ap.parse_args(argv)

    home = java_home()
    print(f"asking {home}", file=sys.stderr)

    reported, moving = steady(home)
    if len(reported) < 400:
        sys.exit(
            f"-XX:+PrintFlagsFinal reported {len(reported)} flags, which is too few for "
            f"any build of this JDK. The line format changed and the parser is reading "
            f"the wrong thing."
        )
    # Whether the unlock options change what is *listed* rather than what is settable is
    # the question every article about diagnostic flags gets wrong, so it is measured.
    unlocked = table(home, ["-XX:+UnlockDiagnosticVMOptions",
                            "-XX:+UnlockExperimentalVMOptions"])
    bounded = ranges(home)

    found = describe(home)
    found.update({
        "processors": os.cpu_count(),
        "flags": reported,
        "ranges": bounded,
        "unstable_between_runs": moving,
        "counts": {
            "reported": len(reported),
            "reported_when_unlocked": len(unlocked),
            "with_a_range": len(bounded),
            "unstable_between_runs": len(moving),
            "by_kind": tally(reported, "kind"),
            "by_origin": tally(reported, "origin"),
            "by_type": tally(reported, "type"),
        },
        "scenarios": scenarios(home, reported),
        "gates": gates(home, reported),
    })

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
