#!/usr/bin/env python3
"""Measure how much JShell distorts what a lesson can observe, against issue #2.

The same workload is run four ways and the same six observations are taken each time.
The four ways matter as much as the six observations, because "JShell" is not one thing.

  compiled      javac, then java. The floor. Nothing in the process that the workload
                did not put there, and the number every other arm is measured against.
  launcher      java Workload.java, the JEP 330 single file source launcher. This is
                what `jvx.run` does, so it is the cost of the escape hatch itself.
  kernel        jshell with its default execution provider, which starts a second JVM
                and runs the reader's code over there. The command line default.
  kernel-local  jshell --execution local, which runs the reader's code in the same JVM
                as the JShell compiler. This is what a notebook kernel does, so it is
                the arm the E0 tier of this curriculum actually stands on.

Observations, the six the research document asked for:

  1 class histogram      GC.class_histogram, in process through the MBean
  2 class loading        -Xlog:class+load=info to a file, counted
  3 compilation          -XX:+PrintCompilation on stdout, counted
  4 JFR events           a profile recording, summarised by event type with `jfr`
  5 heap dump            counted by tools/hprof_count.py
  6 class stats          GC.class_stats, which no longer exists, see the report

Each arm runs three times because three of the observations need a VM flag that cannot
be turned on later, and leaving all of them on at once would mean every number was
measured under the weight of the others.

  python probes/jshell-noise/run.py --out probes/jshell-noise/results/thishost.json
"""

from __future__ import annotations

import argparse
import datetime
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

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import hprof_count  # noqa: E402

ARMS = ["compiled", "launcher", "kernel", "kernel-local"]

# Anything whose name says JShell put it there. The REPL prefix is the wrapper class
# JShell generates for a snippet, so a reader's own class shows up under it too.
SUBSTRATE = re.compile(r"REPL[./]|\$JShell\$|jdk[./]jshell|jdk[./]internal[./]jshell")

# The single file source launcher compiles in memory, so javac is in the process. This
# is the launcher arm's own contribution and is worth separating from JShell's.
JAVAC = re.compile(r"com[./]sun[./]tools[./]javac|jdk[./]internal[./]shellsupport")

# A PrintCompilation line starts with a timestamp in milliseconds and a compile id.
# Anything else on stdout is the workload talking, or JShell's own banner.
COMPILE_LINE = re.compile(r"^\s*\d+\s+\d+\s")

# `jfr summary` prints a table of event type, count and size once past its header.
JFR_ROW = re.compile(r"^\s*(jdk\.\S+)\s+(\d+)\s")


def jdk() -> pathlib.Path:
    home = os.environ.get("JAVA_HOME")
    if home and (pathlib.Path(home) / "bin" / "java").is_file():
        return pathlib.Path(home) / "bin"
    found = shutil.which("java")
    if found is None:
        raise SystemExit("no java on PATH and no JAVA_HOME")
    return pathlib.Path(found).parent


def kernel_source(workload: str, out_dir: pathlib.Path) -> str:
    """Turn the workload file into something JShell will accept.

    Only two changes, both of them mechanical. The leading comment block goes because
    JShell would take each line of it as a snippet, and `public` goes off the class
    because JShell has no package for it to be public in. The bytecode of the workload
    itself is untouched, which is the whole point: both sides have to be running the
    same program or the comparison says nothing.
    """
    body = workload[workload.index("import com.sun"):]
    body = body.replace("public class Workload {", "class Workload {", 1)
    return body + '\nWorkload.main(new String[]{"%s"});\n/exit\n' % out_dir


class Arm:
    def __init__(self, name: str, bin_dir: pathlib.Path, work: pathlib.Path, source: str):
        self.name = name
        self.bin = bin_dir
        self.work = work
        self.source = source
        self.java_file = work / "Workload.java"
        self.java_file.write_text(source, encoding="utf-8")
        self.classes = work / "classes"
        if name == "compiled":
            self.classes.mkdir(exist_ok=True)
            done = subprocess.run(
                [str(bin_dir / "javac"), "-d", str(self.classes), str(self.java_file)],
                capture_output=True,
                text=True,
            )
            if done.returncode != 0:
                raise SystemExit("javac failed:\n" + done.stderr)

    def command(self, out_dir: pathlib.Path, vm: list[str]) -> tuple[list[str], str | None]:
        """The command line for this arm, and stdin for it if it needs any."""
        if self.name == "compiled":
            return [
                str(self.bin / "java"), *vm, "-cp", str(self.classes), "Workload",
                str(out_dir),
            ], None
        if self.name == "launcher":
            return [str(self.bin / "java"), *vm, str(self.java_file), str(out_dir)], None

        # Which prefix carries a VM option depends on which JVM ends up running the
        # code. On the default provider that is the remote agent and the prefix is -R.
        # Under --execution local there is no remote agent, and -R is accepted and then
        # silently ignored, so the option has to go to JShell's own JVM with -J. Getting
        # this wrong does not fail, it just produces a run with no log file in it, which
        # is how the first version of this harness reported None for half its numbers.
        script = self.work / f"{self.name}.jsh"
        script.write_text(kernel_source(self.source, out_dir), encoding="utf-8")
        if self.name == "kernel-local":
            extra, prefix = ["--execution", "local"], "-J"
        else:
            # The default JDI handshake gives the agent JVM a few seconds to call back,
            # and on a slow Linux box starting a flight recording takes eight seconds all
            # by itself, so the agent misses the deadline and JShell reports that every
            # provider failed. The deadline is not part of what this probe measures, so
            # it is raised here rather than worked around, and raised on every machine
            # so that the arms stay comparable.
            extra, prefix = ["--execution", "jdi:timeout(60000)"], "-R"
        return [
            str(self.bin / "jshell"), "-q", *extra,
            *[f"{prefix}{option}" for option in vm], str(script),
        ], None


def run(command: list[str], timeout: int = 900) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    elapsed = time.monotonic() - started
    output = done.stdout + done.stderr
    if "WORKLOAD DONE" not in output and "PrintCompilation" not in " ".join(command):
        raise SystemExit(
            "the workload did not finish for:\n  %s\n%s" % (" ".join(command), output[-4000:])
        )
    return output, elapsed


def count_histogram(text: str) -> dict:
    """Pull the totals line out of GC.class_histogram, and the substrate's share."""
    total = {"classes": 0, "instances": 0, "bytes": 0}
    substrate = {"classes": 0, "instances": 0, "bytes": 0}
    for line in text.splitlines():
        row = re.match(r"\s*\d+:\s+(\d+)\s+(\d+)\s+(\S+)", line)
        if not row:
            continue
        instances, byte_count, name = int(row.group(1)), int(row.group(2)), row.group(3)
        total["classes"] += 1
        total["instances"] += instances
        total["bytes"] += byte_count
        if SUBSTRATE.search(name):
            substrate["classes"] += 1
            substrate["instances"] += instances
            substrate["bytes"] += byte_count
    return {"total": total, "substrate": substrate}


def count_loader_stats(text: str) -> dict:
    row = re.search(r"^Total = \d+\s+(\d+)\s+(\d+)\s+(\d+)", text, re.M)
    if not row:
        return {"classes": None, "chunk_bytes": None, "block_bytes": None}
    return {
        "classes": int(row.group(1)),
        "chunk_bytes": int(row.group(2)),
        "block_bytes": int(row.group(3)),
    }


def count_class_load(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {"lines": None, "substrate": None, "javac": None}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {
        "lines": len(lines),
        "substrate": sum(1 for line in lines if SUBSTRATE.search(line)),
        "javac": sum(1 for line in lines if JAVAC.search(line)),
    }


def count_compilation(text: str, log: pathlib.Path) -> dict:
    """Two different questions, which is why both flags are on for this pass.

    LogCompilation writes to a file and is the ground truth: it says what the JIT
    actually did. PrintCompilation writes to stdout and is the visibility: it says what
    a reader sitting in front of this substrate would get to see. On the default JShell
    provider those two answers are not the same number, and the gap is the finding.
    """
    visible = [line for line in text.splitlines() if COMPILE_LINE.match(line)]
    logged = 0
    substrate = 0
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if "<task_queued" in line or "<task " in line:
                logged += 1
                if SUBSTRATE.search(line):
                    substrate += 1
    return {
        "logged": logged,
        "logged_substrate": substrate,
        "visible_on_stdout": len(visible),
        "visible_substrate": sum(1 for line in visible if SUBSTRATE.search(line)),
        "visible_javac": sum(1 for line in visible if JAVAC.search(line)),
    }


def count_jfr(bin_dir: pathlib.Path, recording: pathlib.Path) -> dict:
    if not recording.is_file():
        return {"total": None, "types": {}}
    done = subprocess.run(
        [str(bin_dir / "jfr"), "summary", str(recording)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    types: dict[str, int] = {}
    for line in done.stdout.splitlines():
        row = JFR_ROW.match(line)
        if row:
            types[row.group(1)] = int(row.group(2))
    return {"total": sum(types.values()), "types": types}


def count_heap(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    found = hprof_count.count(path)
    substrate = sum(
        number
        for name, number in list(found["instances"].items()) + list(found["arrays"].items())
        if SUBSTRATE.search(name)
    )
    # The 50,000 widgets are the workload's own and are not noise, even though JShell
    # renames them into the REPL namespace. Counted separately so the noise figure is
    # not swamped by the thing the probe deliberately allocated.
    widgets = sum(
        number for name, number in found["instances"].items() if name.endswith("Widget")
    )
    return {
        "file_bytes": found["file_bytes"],
        "classes_loaded": found["classes_loaded"],
        "instances": found["instance_total"],
        "arrays": found["array_total"],
        "substrate_objects": substrate,
        "workload_widgets": widgets,
        "top": dict(
            sorted(found["instances"].items(), key=lambda kv: -kv[1])[:15]
        ),
    }


def measure(arm: Arm, bin_dir: pathlib.Path) -> dict:
    result: dict = {"arm": arm.name}

    # Pass one: the heap, the histogram, the loader stats and the class load log. All
    # of these are either taken in process or written to a file, so they do not fight
    # over stdout with each other or with the workload.
    out = arm.work / f"{arm.name}-main"
    out.mkdir(exist_ok=True)
    load_log = out / "classload.log"
    _, seconds = run(arm.command(out, [f"-Xlog:class+load=info:file={load_log}"])[0])
    result["wall_seconds"] = round(seconds, 2)
    result["histogram"] = count_histogram((out / "histogram.txt").read_text())
    result["loader_stats"] = count_loader_stats((out / "loaderstats.txt").read_text())
    result["class_load"] = count_class_load(load_log)
    result["heap_dump"] = count_heap(out / "heap.hprof")

    # Pass two: compilation, with both flags on. PrintCompilation can only go to stdout,
    # so it gets a run of its own with nothing else competing for the stream, and
    # LogCompilation goes to a file beside it as the number to trust.
    out2 = arm.work / f"{arm.name}-compile"
    out2.mkdir(exist_ok=True)
    compile_log = out2 / "compilation.xml"
    text, _ = run(
        arm.command(
            out2,
            [
                "-XX:+PrintCompilation",
                "-XX:+UnlockDiagnosticVMOptions",
                "-XX:+LogCompilation",
                f"-XX:LogFile={compile_log}",
            ],
        )[0]
    )
    result["compilation"] = count_compilation(text, compile_log)

    # Pass three: JFR. A recording changes what the compiler does, so it is kept away
    # from the compilation numbers.
    out3 = arm.work / f"{arm.name}-jfr"
    out3.mkdir(exist_ok=True)
    recording = out3 / "recording.jfr"
    run(arm.command(out3, [f"-XX:StartFlightRecording:filename={recording},settings=profile"])[0])
    result["jfr"] = count_jfr(bin_dir, recording)

    result["class_stats"] = "GC.class_stats does not exist on this JDK"
    return result


def describe(bin_dir: pathlib.Path) -> dict:
    done = subprocess.run(
        [str(bin_dir / "java"), "-version"], capture_output=True, text=True
    )
    build = re.search(r'\(build ([^)]+)\)', done.stdout + done.stderr)
    # No hostname and no path to the JDK. Both of those identify a particular machine
    # and this file is committed to a public repository, where the platform, the core
    # count and the build string are the only parts anybody reproducing this needs.
    return {
        "java_build": build.group(1) if build else "unknown",
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "cpus": os.cpu_count(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="write the results here as JSON")
    ap.add_argument("--arm", action="append", choices=ARMS, help="only these arms")
    ap.add_argument("--keep", type=pathlib.Path, help="keep the raw output in this directory")
    args = ap.parse_args(argv)

    bin_dir = jdk()
    workload = (HERE / "Workload.java").read_text(encoding="utf-8")
    wanted = args.arm or ARMS

    holder = args.keep or pathlib.Path(tempfile.mkdtemp(prefix="jshell-noise-"))
    holder.mkdir(parents=True, exist_ok=True)

    results = {
        "probe": "jshell-noise",
        "issue": 2,
        "measured": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "pin": json.loads((ROOT / "docs" / "pin.json").read_text())["jdk_tag"],
        **describe(bin_dir),
        "arms": {},
    }

    for name in wanted:
        print(f"== {name}", flush=True)
        work = holder / name
        work.mkdir(parents=True, exist_ok=True)
        arm = Arm(name, bin_dir, work, workload)
        results["arms"][name] = measure(arm, bin_dir)
        found = results["arms"][name]
        print(
            f"   {found['class_load']['lines']} classes loaded, "
            f"{found['heap_dump'].get('instances')} instances, "
            f"{found['compilation']['logged']} compiles "
            f"({found['compilation']['visible_on_stdout']} visible), "
            f"{found['jfr']['total']} JFR events, "
            f"{found['wall_seconds']}s",
            flush=True,
        )

    text = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    if args.keep:
        print(f"raw output kept in {holder}")
    else:
        shutil.rmtree(holder, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
