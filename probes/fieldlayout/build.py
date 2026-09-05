#!/usr/bin/env python3
"""Build a fastdebug JDK from the pinned source, and write down what that took.

Issue #5 asks whether JOL is right about object layout under compact headers, and the
only thing that can answer it is `-XX:+PrintFieldLayout`, which is a develop flag. #33
measured that no product build on any of four platforms has it. So the measurement needs
a build nobody ships, and this is the script that makes one.

It is separate from `run.py` because it takes hours and the measurement takes seconds.
Building once and measuring many times is the shape of the work, and a probe that
rebuilt the JDK every time somebody wanted to add a class to the list would not get run.

  docker build -t jvx-fieldlayout probes/fieldlayout
  docker run --rm -v "$PWD:/repo:ro" -v /tmp/fieldlayout:/work jvx-fieldlayout \\
      python3 /repo/probes/fieldlayout/build.py

It writes `/work/build.json`, which `run.py` reads for the path to the java it built and
for the provenance it copies into its own results. Give it ten gigabytes of scratch and,
on a machine that is busy with other work, several hours.

`make jdk` rather than `make images`, because the exploded image is a runnable JDK and
skips the jmod and jlink steps, which are a large part of a build whose output is going
to be thrown away. `--with-jobs` defaults low here on purpose: this is meant to run on a
shared machine without making it unusable for everybody else, and the wall clock cost of
being polite is the cheapest thing in this file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import multiprocessing
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(os.environ.get("JVX_REPO", "/repo"))
WORK = pathlib.Path(os.environ.get("JVX_WORK", "/work"))

CONF = "fastdebug"

# What configure is told. fastdebug rather than slowdebug because the develop flags are
# in both and slowdebug is unoptimised, which would make every timing in every later
# probe a measurement of the wrong thing. Warnings as errors is disabled because a build
# environment newer than the one the tag was cut against fails on a warning that has
# nothing to do with what is being measured, and a probe that cannot build is worse than
# one that built with a warning.
CONFIGURE = [
    f"--with-conf-name={CONF}",
    "--with-debug-level=fastdebug",
    "--with-jvm-variants=server",
    "--disable-warnings-as-errors",
]


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def timed(command: list[str], **kwargs) -> dict:
    start = time.monotonic()
    done = run(command, **kwargs)
    return {
        "ok": done.returncode == 0,
        "seconds": round(time.monotonic() - start, 1),
        "tail": tail(done.stdout + done.stderr),
    }


def tail(text: str, lines: int = 20) -> str:
    kept = [line for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def pin() -> dict:
    return json.loads((REPO / "docs" / "pin.json").read_text(encoding="utf-8"))


def boot_jdk() -> pathlib.Path:
    """The pinned GA build, which is what this build is compiled by as well as against."""
    home = WORK / "boot-jdk"
    done = run([sys.executable, str(REPO / "tools" / "fetch_jdk.py"), "--dir", str(home)])
    if done.returncode != 0:
        sys.exit(f"could not fetch the pinned JDK: {tail(done.stderr)}")
    return pathlib.Path(done.stdout.strip())


def java_build(home: pathlib.Path) -> str:
    done = run([str(home / "bin" / "java"), "-version"])
    return (done.stderr or done.stdout).strip().splitlines()[-1].strip()


def source(tag: str, expected: str) -> dict:
    """The pinned source, cloned shallow, with the commit checked rather than assumed."""
    into = WORK / "jdk-src"
    found: dict = {"tag": tag, "path": str(into)}
    if not (into / "configure").is_file():
        found["cloned"] = timed([
            "git", "clone", "--depth", "1", "--branch", tag,
            "https://github.com/openjdk/jdk.git", str(into),
        ])
        if not found["cloned"]["ok"]:
            return found
    else:
        found["cloned"] = {"ok": True, "seconds": 0, "tail": "already there"}
    head = run(["git", "-C", str(into), "rev-parse", "HEAD"])
    found["commit"] = head.stdout.strip()
    found["commit_matches_pin"] = found["commit"] == expected
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", type=int, default=max(1, multiprocessing.cpu_count() // 2),
                    help="parallel make jobs, half the cores by default")
    ap.add_argument("--out", type=pathlib.Path, default=WORK / "build.json")
    args = ap.parse_args(argv)

    settings = pin()
    WORK.mkdir(parents=True, exist_ok=True)
    found: dict = {
        "probe": "fieldlayout-build",
        "built": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "jobs": args.jobs,
        "configure_flags": CONFIGURE,
    }

    print("fetching the boot JDK", file=sys.stderr, flush=True)
    boot = boot_jdk()
    found["boot_jdk"] = {"path": str(boot), "build": java_build(boot)}

    print(f"cloning openjdk/jdk at {settings['jdk_tag']}", file=sys.stderr, flush=True)
    found["source"] = source(settings["jdk_tag"], settings["jdk_tag_commit"])
    if not found["source"]["cloned"]["ok"]:
        return write(args.out, found, "the clone failed")
    if not found["source"]["commit_matches_pin"]:
        return write(args.out, found, "the clone is not the pinned commit")

    src = pathlib.Path(found["source"]["path"])
    print("configure", file=sys.stderr, flush=True)
    found["configure"] = timed(
        ["bash", "configure", f"--with-boot-jdk={boot}"] + CONFIGURE, cwd=src)
    if not found["configure"]["ok"]:
        return write(args.out, found, "configure failed")

    print(f"make jdk with {args.jobs} jobs, this is the long part",
          file=sys.stderr, flush=True)
    found["make"] = timed(
        ["make", f"JOBS={args.jobs}", "CONF=" + CONF, "jdk"], cwd=src)
    if not found["make"]["ok"]:
        return write(args.out, found, "the build failed")

    home = src / "build" / CONF / "jdk"
    if not (home / "bin" / "java").is_file():
        return write(args.out, found, f"the build produced no java at {home}")
    found["jdk"] = {"home": str(home), "build": java_build(home)}
    # The one thing that makes this build worth its hours. A product build accepts the
    # flag and warns, so the check is that the VM says nothing about it rather than that
    # it started.
    check = run([str(home / "bin" / "java"), "-XX:+PrintFieldLayout", "-version"])
    found["jdk"]["print_field_layout_accepted"] = (
        check.returncode == 0 and "PrintFieldLayout" not in check.stderr)
    return write(args.out, found, None)


def write(out: pathlib.Path, found: dict, error: str | None) -> int:
    found["error"] = error
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"fastdebug JDK at {found['jdk']['home']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
