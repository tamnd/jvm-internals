#!/usr/bin/env python3
"""Break one class file every way the specification has a rule about, and at random.

Issue #15. M2's last gate asks the fuzzer to find at least one case where HotSpot's
rejection differs from what the specification names, so this is the half that produces
the rejections. What the specification names is in `tools/gen_fuzz.py`, cited section by
section, and the comparison happens there rather than here.

`Fuzz.java` builds one valid class file, patches a named byte of it and records where the
JVM stopped and in what words. It is run twice: once for the 27 targeted patches, and then
in chunks for the 256 seeded random bit flips.

  JAVA_HOME=... python probes/classfile-fuzz/run.py --out probes/classfile-fuzz/results/osx-arm64.json

No network, no root, and it takes about ten seconds.

The random half runs in chunks and not in one JVM, because a fuzzer that finds a way to
kill the VM has found the most interesting thing on the page and must not lose the rest of
the run doing it. A chunk that does not come back is rerun one mutant at a time, and the
mutant that kills the JVM is recorded as a result rather than as a missing row.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import platform
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
FUZZ = HERE / "Fuzz.java"
CHUNK = 64

# A mutant that is accepted runs nothing, and a mutant that is refused is refused in
# under a second, so a chunk that takes this long has found a loop rather than a bug.
TIMEOUT = 300


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


def facts(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1)
            found[key] = value
    return found


def java(home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [tool(home, "java"), "-Xmx256m", str(FUZZ), *args],
        capture_output=True, text=True, timeout=TIMEOUT,
    )


def targeted(home: pathlib.Path) -> dict:
    done = java(home, "--targeted")
    if done.returncode != 0:
        sys.exit(f"Fuzz.java did not run:\n{done.stdout}\n{done.stderr}")
    return facts(done.stdout)


def randomly(home: pathlib.Path, total: int) -> tuple[dict[str, str], list[int]]:
    """Every seeded flip, in chunks, with the ones that killed the JVM named."""
    found: dict[str, str] = {}
    killers: list[int] = []
    for start in range(0, total, CHUNK):
        count = min(CHUNK, total - start)
        done = java(home, "--random", str(start), str(count))
        if done.returncode == 0:
            found.update(facts(done.stdout))
            continue
        print(f"chunk from {start} exited {done.returncode}, rerunning one at a time",
              file=sys.stderr)
        for index in range(start, start + count):
            alone = java(home, "--random", str(index), "1")
            if alone.returncode == 0:
                found.update(facts(alone.stdout))
            else:
                killers.append(index)
                found[f"random.{index:03d}.stage"] = "killed the jvm"
                found[f"random.{index:03d}.error"] = f"exit {alone.returncode}"
                found[f"random.{index:03d}.message"] = first_line(
                    alone.stdout + alone.stderr)
    return found, killers


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def shape(found: dict[str, str]) -> dict:
    """The key/value lines folded into the shape the generator reads."""
    cases: dict[str, dict] = {}
    regions: dict[str, dict] = {}
    flips: dict[str, dict] = {}
    probe: dict[str, object] = {}
    seed_run: dict[str, str] = {}

    for key, value in found.items():
        head, rest = key.split(".", 1)
        if head == "case":
            name, field = rest.rsplit(".", 1)
            cases.setdefault(name, {})[field] = value
        elif head == "region":
            name, field = rest.rsplit(".", 1)
            regions.setdefault(name, {})[field] = int(value)
        elif head == "random":
            name, field = rest.rsplit(".", 1)
            flips.setdefault(name, {})[field] = value
        elif head == "probe":
            probe[rest] = int(value) if value.isdigit() else value
        elif head == "seed":
            seed_run[rest] = value

    for entry in cases.values():
        entry["bytes"] = int(entry["bytes"])
    ordered = []
    for name in sorted(flips):
        entry = flips[name]
        ordered.append({
            "mutant": int(name),
            "at": int(entry["at"]),
            "bit": int(entry.get("bit", -1)),
            "region": entry.get("region", ""),
            "stage": entry["stage"],
            "error": entry["error"],
            "message": entry["message"],
        })

    return {
        "seed": probe.get("seed"),
        "class_file_major": probe.get("class_file_major"),
        "seed_class": probe.get("seed_class"),
        "seed_bytes": probe.get("seed_bytes"),
        "seed_run": seed_run,
        "regions": regions,
        "cases": cases,
        "random": ordered,
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
        "probe": "classfile-fuzz",
        "issue": 15,
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build,
        "measured": datetime.date.today().isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--mutants", type=int, default=256,
                    help="how many seeded random flips to try (default 256)")
    args = ap.parse_args(argv)

    home = java_home()
    print(f"asking {home}", file=sys.stderr)

    found = describe(home)
    lines = targeted(home)
    flips, killers = randomly(home, args.mutants)
    lines.update(flips)
    found.update(shape(lines))
    found["killed_the_jvm"] = killers

    if found["seed_run"].get("stage") != "accepted":
        sys.exit(
            f"the unpatched seed class did not run: {found['seed_run']}. Every result "
            f"below it would be a measurement of a broken starting point."
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
