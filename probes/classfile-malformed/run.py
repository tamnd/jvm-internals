#!/usr/bin/env python3
"""Build six class files that are wrong on purpose, and record who notices.

Issue #7. B11's boss fight and the whole class file fuzzer assume it is possible to make
a class file the JVM refuses, on demand and for a stated reason. `java.lang.classfile` is
a well designed API, and a well designed API stops you writing nonsense, so the question
is where it stops you and what is left once it does.

`Malformed.java` does the building and the loading, because both need to happen inside a
JVM. This side runs it, asks the two questions that need a second process, and writes one
JSON file per environment.

  JAVA_HOME=... python probes/classfile-malformed/run.py --out probes/classfile-malformed/results/osx-arm64.json

Nothing here touches the network and nothing needs root. It takes a few seconds.
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
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MALFORMED = HERE / "Malformed.java"

# The six from the issue, in the order the issue lists them, which is also roughly the
# order of how much a lesson would miss them.
CASES = [
    "pool_index_past_end",
    "method_ref_descriptor_mismatch",
    "stack_map_int_where_reference",
    "final_and_abstract",
    "max_stack_too_small",
    "version_above_the_pin",
]

# Which class file each case writes, so the tooling checks below know what to open.
CLASS_NAMES = {
    "pool_index_past_end": "PoolIndexPastEnd",
    "method_ref_descriptor_mismatch": "MethodRefMismatch",
    "stack_map_int_where_reference": "StackMapLies",
    "final_and_abstract": "FinalAndAbstract",
    "max_stack_too_small": "MaxStackTooSmall",
    "version_above_the_pin": "FromTheFuture",
}


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


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=300, **kwargs
    )


def build(home: pathlib.Path, into: pathlib.Path) -> dict[str, dict[str, str]]:
    """Run the Java half and turn its key/value lines into one dict per case."""
    done = run([tool(home, "java"), str(MALFORMED), str(into)])
    if done.returncode != 0:
        sys.exit(f"Malformed.java did not run:\n{done.stdout}\n{done.stderr}")

    facts: dict[str, dict[str, str]] = {name: {} for name in CASES}
    extra: dict[str, str] = {}
    for line in done.stdout.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key.startswith("case."):
            _, name, field = key.split(".", 2)
            facts.setdefault(name, {})[field] = value
        else:
            extra[key] = value
    return {"cases": facts, "probe": extra}


def readable(home: pathlib.Path, into: pathlib.Path) -> dict[str, str]:
    """Whether `javap` can still show a reader the file that the JVM rejected.

    Worth asking separately. A lesson that says "here is a broken class file" has to be
    able to print the broken part, and a tool that gives up on the whole file the moment
    it finds the bad byte would make that lesson a screenshot of an error message.
    """
    answers = {}
    for name, class_name in CLASS_NAMES.items():
        path = into / f"{class_name}.class"
        if not path.is_file():
            answers[name] = "no file"
            continue
        done = run([tool(home, "javap"), "-v", "-p", str(path)])
        text = done.stdout + done.stderr
        if done.returncode != 0:
            answers[name] = "refused: " + first_error(text)
        elif "Code:" in text or "major version" in text:
            answers[name] = "shows the file"
        else:
            answers[name] = "printed nothing useful"
    return answers


def first_error(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("Classfile"):
            return stripped[:160]
    return "no output"


UNLOCK = ["-XX:+UnlockDiagnosticVMOptions"]

# How many times each broken class is run with the verifier off. Ten, because the answer
# for at least one of them is not the same every time and a single sample would hide that.
REPEATS = 10

# The four ways anybody has ever been told to turn the verifier off. Two of them are the
# ones in every blog post from the last fifteen years and two of them are the ones that
# still work, and a lesson needs to know which is which before it tells a reader to type
# something.
VERIFIER_OFF = [
    "-Xverify:none",
    "-noverify",
    "-XX:-BytecodeVerificationLocal",
    "-XX:-BytecodeVerificationRemote",
]


def verifier_switch(home: pathlib.Path) -> dict[str, str]:
    """Can the verifier still be turned off, which decides how B11 can be taught.

    A lesson that wants to show what the verifier is for wants to run the same class with
    it and without it, so this asks each spelling and, when the VM says the option is
    locked, asks again with the unlock. Answering "rejected" to a flag that works two
    words later would be a worse result than not asking.
    """
    answers = {}
    for flag in VERIFIER_OFF:
        done = run([tool(home, "java"), flag, "-version"])
        text = done.stdout + done.stderr
        if "must be enabled via" in text:
            done = run([tool(home, "java"), *UNLOCK, flag, "-version"])
            text = done.stdout + done.stderr
            prefix = "with the diagnostic unlock: "
        else:
            prefix = ""
        if done.returncode != 0:
            answers[flag] = prefix + "rejected: " + first_error(text)
        elif "deprecated" in text.lower() or "ignor" in text.lower():
            answers[flag] = prefix + "accepted with a warning: " + first_error(text)
        else:
            answers[flag] = prefix + "accepted"
    return answers


def without_the_verifier(home: pathlib.Path, into: pathlib.Path) -> dict[str, str]:
    """Load each broken class again with verification off, and see what changes.

    Every generated class has a `main` that calls the broken method and prints "go ran",
    so there are three outcomes and they are all worth telling apart: it prints, which
    means the JVM executed bytecode nobody checked; it fails with something, which means
    a check that is not the verifier caught it; or the process dies, which is the outcome
    the verifier exists to prevent and the one B11 is about.

    Ten runs each and not one, because the answer is not the same every time. A class file
    whose constant pool index points past the end of the pool sends the interpreter to an
    address nobody chose, and whether that address is readable is a property of the run and
    not of the file. One sample would have reported whichever outcome came up first and it
    would have been true, which is worse than being wrong.
    """
    answers = {}
    for name, class_name in CLASS_NAMES.items():
        if not (into / f"{class_name}.class").is_file():
            answers[name] = {"no file": REPEATS}
            continue
        outcomes: dict[str, int] = {}
        for _ in range(REPEATS):
            done = run([
                tool(home, "java"), *UNLOCK,
                "-XX:-BytecodeVerificationLocal", "-XX:-BytecodeVerificationRemote",
                "-cp", str(into), class_name,
            ], cwd=into)
            text = done.stdout + done.stderr
            if "go ran" in text:
                outcome = "runs"
            elif "A fatal error has been detected" in text or done.returncode < 0:
                outcome = "the VM died: " + signal(text)
            else:
                outcome = first_error(text)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        answers[name] = dict(sorted(outcomes.items()))
    return answers


def signal(text: str) -> str:
    """Which signal killed it, without the pid, the address or the thread id.

    All three are different on every run, and a committed results file that changes when
    nothing changed is a file people stop reading diffs of.
    """
    for line in text.splitlines():
        if "#  " in line and ("SIG" in line or "EXCEPTION_" in line):
            return line.split("#")[1].strip().split(" ")[0]
    return "no signal named"


def describe(home: pathlib.Path) -> dict:
    done = run([tool(home, "java"), "-version"])
    text = done.stdout + done.stderr
    build_string = ""
    for line in text.splitlines():
        if "build" in line:
            build_string = line.split("build", 1)[1].strip(" )")
            break
    # No hostname and no home directory. This file is committed, and which machine it was
    # measured on is not one of the things it is measuring.
    return {
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build_string,
        "measured": datetime.date.today().isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--keep", type=pathlib.Path, help="keep the class files here")
    args = ap.parse_args(argv)

    home = java_home()
    print(f"asking {home}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as raw:
        into = args.keep if args.keep else pathlib.Path(raw)
        built = build(home, into)
        found = describe(home)
        found.update(built)
        found["javap"] = readable(home, into)
        found["verifier_off"] = verifier_switch(home)
        found["loaded_unverified"] = without_the_verifier(home, into)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
