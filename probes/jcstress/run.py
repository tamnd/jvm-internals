#!/usr/bin/env python3
"""Ask whether the interesting interleavings actually appear on a given machine.

Issue #9. The whole concurrency part is graded by jcstress and the Race Playground
publishes its outcome tables. On a shared two core runner the interesting outcomes do
not appear, and they do not fail to appear at random: they systematically never happen,
because there is no second core free to race on. Publishing "we never observed this
reordering" when the reason is the runner rather than the memory model would be worse
than publishing nothing at all.

So this is not a pass or fail check. It runs five tests whose interesting outcome is
written into the test itself, and records how often each one appeared, on a named
machine, with the core count and the load average next to it. Two runs on two machines
are the measurement. The question the report answers is whether a reader on ordinary
hardware would ever see the thing a lesson is about.

  python probes/jcstress/run.py --label quiet-x64
  python probes/jcstress/run.py --label quiet-aarch64 --mode default
  python probes/jcstress/run.py --label shared-runner --mode quick

Run it on a machine that is doing nothing else. That is the entire point.

The tests are in `tests/`, in this repository, rather than taken from jcstress. The
uber jar of jcstress's own test corpus is not published to Maven Central, so a probe
that depended on it would depend on somebody building jcstress from source first. The
five here are the ones a lesson would show anyway, they declare their own interesting
outcome, and two of them are controls that fail the run if the memory model is violated.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
TESTS = HERE / "tests"
CACHE = pathlib.Path.home() / ".cache" / "jvx" / "jcstress"

# jcstress and the two things it needs to run, pinned by version and by hash for the
# same reason the JDK is: a table of outcomes measured against whatever was newest that
# week is not something a second machine can be compared against. The versions are the
# ones jcstress-parent 0.16 declares, which matters more than it looks: with a newer
# jopt-simple the runner starts and `-h` throws NoSuchMethodError, and with no JNA at
# all it silently loses the ability to pin threads to cores.
VERSION = "0.16"
CENTRAL = "https://repo1.maven.org/maven2"
DEPS = {
    "jcstress-core-0.16.jar": (
        f"{CENTRAL}/org/openjdk/jcstress/jcstress-core/{VERSION}/"
        f"jcstress-core-{VERSION}.jar",
        "58ee119c227bd02a86ad47f32b6165537e42502ef6cc8140d6454b0cac29f6ac"),
    "jopt-simple-4.6.jar": (
        f"{CENTRAL}/net/sf/jopt-simple/jopt-simple/4.6/jopt-simple-4.6.jar",
        "3fcfbe3203c2ea521bf7640484fd35d6303186ea2e08e72f032d640ca067ffda"),
    "jna-5.8.0.jar": (
        f"{CENTRAL}/net/java/dev/jna/jna/5.8.0/jna-5.8.0.jar",
        "930273cc1c492f25661ea62413a6da3fd7f6e01bf1c4dcc0817fc8696a7b07ac"),
    "jna-platform-5.8.0.jar": (
        f"{CENTRAL}/net/java/dev/jna/jna-platform/5.8.0/jna-platform-5.8.0.jar",
        "ffd93fe1bc07de6f33eabf3d051c3636e01a01c17cb0da8448c53a2ac5e3bf7a"),
}

SELECTOR = "jvx.jcstress.*"

# The shape of jcstress 0.16's report, which is the only machine readable thing it gives
# away short of its own binary blob. The blob is kept too, so a parse that comes back
# thin can be redone without occupying the machine for another hour.
HEADER = re.compile(r"^\.*\s*\[(?P<status>OK|FAILED|ERROR)\]\s+(?P<name>\S+)\s*$")
ROW = re.compile(
    r"^\s+(?P<outcome>\S.*?)\s{2,}(?P<samples>[\d,]+)\s+(?P<freq>[\d.]+)%\s+"
    r"(?P<expect>Acceptable|Interesting|Forbidden|Unknown)\s+(?P<desc>.*)$")
BLOB = re.compile(r'Test result blob:\s*"([^"]+)"')
ACROSS = "Results across all configurations:"


def digest(path: pathlib.Path) -> str:
    found = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            found.update(block)
    return found.hexdigest()


def classpath() -> list[pathlib.Path]:
    """The pinned jars, downloaded once and checked against their hashes every time."""
    CACHE.mkdir(parents=True, exist_ok=True)
    found = []
    for name, (url, want) in DEPS.items():
        path = CACHE / name
        if not path.is_file():
            print(f"fetching {name}", file=sys.stderr)
            tmp = path.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=300) as source:
                tmp.write_bytes(source.read())
            tmp.rename(path)
        got = digest(path)
        if got != want:
            sys.exit(f"{path} hashes to {got}, the pin says {want}. Delete it and rerun "
                     f"if you believe the download was interrupted.")
        found.append(path)
    return found


def java_home(given: pathlib.Path | None) -> pathlib.Path:
    if given is not None:
        return given
    home = os.environ.get("JAVA_HOME")
    if home:
        return pathlib.Path(home)
    binary = shutil.which("java")
    if binary is None:
        sys.exit("no --jdk, no JAVA_HOME and no java on PATH")
    return pathlib.Path(binary).resolve().parent.parent


def compile_tests(home: pathlib.Path, jars: list[pathlib.Path]) -> pathlib.Path:
    """Build the five tests, and the harness jcstress generates from their annotations.

    The generated classes are most of what runs. Each `@JCStressTest` becomes a class
    with the actor loops, the result collection and a resource estimator in it, written
    by jcstress's annotation processor, which is why the processor path matters and why
    a compile that quietly skips it produces a run with no tests in it.
    """
    out = CACHE / "classes"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    sources = sorted(str(path) for path in TESTS.rglob("*.java"))
    if not sources:
        sys.exit(f"no tests in {TESTS}")
    joined = os.pathsep.join(str(jar) for jar in jars)
    done = subprocess.run(
        [str(home / "bin" / "javac"), "-cp", joined, "-processorpath", joined,
         "-d", str(out), *sources],
        capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        sys.exit(f"javac failed:\n{done.stdout}\n{done.stderr}")
    listed = out / "META-INF" / "TestList"
    if not listed.is_file():
        sys.exit("the annotation processor did not run, so there are no tests to run")
    print(f"compiled {len(sources)} tests", file=sys.stderr)
    return out


def parse(text: str) -> dict[str, dict]:
    """Per test outcomes, out of the summary jcstress prints at the end of a run.

    Only the section after RUN RESULTS is read. Everything before it is one table per
    configuration, printed while the run is going, and adding those up would count the
    same sample several times.
    """
    if "RUN RESULTS:" not in text:
        return {}
    tests: dict[str, dict] = {}
    current: str | None = None
    capturing = False
    for line in text.split("RUN RESULTS:", 1)[1].splitlines():
        found = HEADER.match(line)
        if found:
            current = found.group("name")
            tests[current] = {"status": found.group("status"), "outcomes": {}}
            capturing = False
            continue
        if line.strip() == ACROSS:
            capturing = True
            continue
        if not capturing or current is None:
            continue
        row = ROW.match(line)
        if row:
            tests[current]["outcomes"][row.group("outcome")] = {
                "samples": int(row.group("samples").replace(",", "")),
                "percent": float(row.group("freq")),
                "expect": row.group("expect"),
                "description": row.group("desc").strip(),
            }
    return tests


def interesting(tests: dict[str, dict]) -> dict:
    """The number the issue is about: did the interesting outcome ever happen.

    A test with an interesting outcome that never fired is kept by name rather than
    dropped, because that is the finding on a machine too small to race on and it is
    exactly the thing this probe exists to stop somebody publishing as a fact about
    the memory model.
    """
    observed, silent = {}, []
    for name, one in tests.items():
        rare = {outcome: found for outcome, found in one["outcomes"].items()
                if found["expect"] == "Interesting"}
        if not rare:
            continue
        hits = sum(found["samples"] for found in rare.values())
        total = sum(found["samples"] for found in one["outcomes"].values())
        if hits:
            observed[name] = {
                "samples": hits,
                "of": total,
                "percent": round(100 * hits / total, 6) if total else None,
                "outcomes": {outcome: found["samples"] for outcome, found in rare.items()},
            }
        else:
            silent.append(name)
    return {"observed": dict(sorted(observed.items())),
            "never_observed": sorted(silent)}


def forbidden(tests: dict[str, dict]) -> dict:
    """The controls. A forbidden outcome that fired would mean the run is not sound."""
    return {name: {outcome: found["samples"]
                   for outcome, found in one["outcomes"].items()
                   if found["expect"] == "Forbidden" and found["samples"]}
            for name, one in tests.items()
            if any(found["expect"] == "Forbidden" and found["samples"]
                   for found in one["outcomes"].values())}


def describe(home: pathlib.Path) -> dict:
    done = subprocess.run([str(home / "bin" / "java"), "-version"],
                          capture_output=True, text=True)
    text = done.stdout + done.stderr
    build = re.search(r"\(build ([^)]+)\)", text)
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        # No hostname and no paths, the same rule the other probes follow. This file goes
        # into a public repository and the platform and the build are what reproducing it
        # needs.
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build.group(1) if build else "unknown",
        "cpus": os.cpu_count(),
        # The load average before anything starts. A machine that was already busy is not
        # the quiet hardware this probe asks for, and this is how the report says so
        # rather than the person running it having to remember.
        "load_before": [round(one, 2) for one in load] if load else None,
        "measured": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0).isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True,
                    help="what to call this machine, for example quiet-x64")
    ap.add_argument("--jdk", type=pathlib.Path, help="a JDK home, defaults to JAVA_HOME")
    ap.add_argument("--mode", default="quick",
                    choices=["sanity", "quick", "default", "tough", "stress"],
                    help="jcstress preset, quick is a few minutes and sanity proves "
                         "the harness runs without measuring anything")
    ap.add_argument("--cpus", type=int, help="pass -c to jcstress, for the small runner")
    ap.add_argument("--out", type=pathlib.Path,
                    help="where to write, defaults to results/<label>.json")
    args = ap.parse_args(argv)

    home = java_home(args.jdk)
    jars = classpath()
    classes = compile_tests(home, jars)

    out = args.out or HERE / "results" / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    described = describe(home)
    print(f"{described['platform']}, {described['cpus']} cpus, "
          f"load {described['load_before']}, mode {args.mode}", file=sys.stderr)

    command = [
        str(home / "bin" / "java"),
        "-cp", os.pathsep.join([*(str(jar) for jar in jars), str(classes)]),
        "org.openjdk.jcstress.Main",
        # Verbose, because without it jcstress prints the count of tests that passed and
        # says "Use -v to print them", and the outcome counts are the whole result.
        "-v", "-m", args.mode, "-t", SELECTOR,
    ]
    if args.cpus:
        command += ["-c", str(args.cpus)]

    # A scratch directory, because jcstress writes its result blob and an HTML report
    # into the working directory whatever else you asked it for, and the place this gets
    # run most is somebody's checkout.
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as raw:
        scratch = pathlib.Path(raw)
        done = subprocess.run(command, capture_output=True, text=True,
                              cwd=scratch, timeout=12 * 3600)
        text = done.stdout + done.stderr
        (out.with_suffix(".log")).write_text(text, encoding="utf-8")
        blob = BLOB.search(text)
        kept = None
        if blob and (scratch / blob.group(1)).is_file():
            kept = out.parent / f"{args.label}.bin.gz"
            shutil.copy2(scratch / blob.group(1), kept)
    seconds = round(time.monotonic() - started, 1)

    tests = parse(text)
    rare = interesting(tests)
    broken = forbidden(tests)

    result = {
        "probe": "jcstress",
        "issue": 9,
        "label": args.label,
        "jcstress_version": VERSION,
        "mode": args.mode,
        "seconds": seconds,
        "exit": done.returncode,
        **described,
        "load_after": [round(one, 2) for one in os.getloadavg()]
        if hasattr(os, "getloadavg") else None,
        # jcstress cannot pin threads to cores everywhere, and where it cannot the run is
        # measuring the scheduler as much as the hardware. It says so on the way past and
        # this is that sentence, kept.
        "affinity": next((line.strip() for line in text.splitlines()
                          if "affinity mode" in line), None),
        "tests": tests,
        "interesting": rare,
        "forbidden_outcomes_that_fired": broken,
        "sound": not broken and done.returncode == 0,
    }
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"{len(tests)} tests, {len(rare['observed'])} showed the interesting outcome, "
          f"{len(rare['never_observed'])} did not", file=sys.stderr)
    if broken:
        print(f"a forbidden outcome fired: {broken}", file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)
    print(f"the console log is {out.with_suffix('.log')}"
          + (f" and the blob is {kept}" if kept else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
