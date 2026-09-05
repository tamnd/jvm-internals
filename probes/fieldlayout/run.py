#!/usr/bin/env python3
"""Is JOL right about object layout under compact object headers.

Issue #5. Part IV of the curriculum is built on compact headers and O01 is the pilot
lesson, so if JOL reports the legacy layout, or silently falls back to a guess, the pilot
lesson is wrong and so are ten others. The only thing that can settle it is the VM's own
layout log, `-XX:+PrintFieldLayout`, which #33 measured to be absent from every product
build on all four platforms because it is a develop flag. `probes/fieldlayout/build.py`
makes the fastdebug build this needs.

The comparison is made inside one JVM. `JolDump.java` runs under `-XX:+PrintFieldLayout`
and prints what JOL believes on the same stream the VM prints what it did, so the two
halves cannot be describing two different VMs. Twelve classes, four configurations, and
each configuration run twice: once with `-Djdk.attach.allowAttachSelf=true` and once
without, because JOL warns loudly that it is guessing when it cannot attach and whether
the guess is right is exactly the question.

  docker build -t jvx-fieldlayout probes/fieldlayout
  docker run --rm -v "$PWD:/repo:ro" -v /tmp/fieldlayout:/work jvx-fieldlayout \\
      python3 /repo/probes/fieldlayout/build.py
  docker run --rm -v "$PWD:/repo:ro" -v /tmp/fieldlayout:/work jvx-fieldlayout \\
      python3 /repo/probes/fieldlayout/run.py --out /work/linux-x64.json

The build is hours and this is seconds. Nothing here is written back into the repository
checkout, which is mounted read only for that reason.
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
import subprocess
import sys
import urllib.request

REPO = pathlib.Path(os.environ.get("JVX_REPO", "/repo"))
WORK = pathlib.Path(os.environ.get("JVX_WORK", "/work"))

# The newest release of JOL, which is from January 2023 and so predates JEP 450 by two
# years. That is not a reason to test something else. It is the version anybody adding
# JOL to a project today gets, and whether it is right about a header shape that did not
# exist when it was published is the whole question.
JOL = {
    "version": "0.17",
    "url": "https://repo1.maven.org/maven2/org/openjdk/jol/jol-core/0.17/jol-core-0.17.jar",
    "sha256": "bd73d9ad265d8478ccde06130200877f3a4f0a6e2d44e7fb8cbb2e6f4104cbdc",
}

# The twelve, in the order they get harder. The first eleven are declared in JolDump.java
# so their shape is controlled rather than borrowed, and the twelfth is a real class from
# the module the compact header work touched.
CLASSES = [
    "JolDump$Empty",
    "JolDump$OneBoolean",
    "JolDump$OneInt",
    "JolDump$OneLong",
    "JolDump$OneRef",
    "JolDump$TwoRefs",
    "JolDump$MixedPrimitives",
    "JolDump$RefsAndPrimitives",
    "JolDump$Parent",
    "JolDump$Child",
    "JolDump$Grandchild",
    "java.lang.String",
]

# The four configurations issue #5 names. The last two overlap in effect and are both
# here because they are different roads to it: one turns the flag off and the other asks
# for a heap the VM cannot address with compressed references, and a reader who does the
# second by accident should get the same answer as a reader who does the first on purpose.
CONFIGS = {
    "default": [],
    "no_compact_headers": ["-XX:-UseCompactObjectHeaders"],
    "no_compressed_oops": ["-XX:-UseCompressedOops"],
    "heap_above_32g": ["-Xmx33g"],
}

# Whether JOL is allowed to attach to the VM it is running in. Without it JOL prints
# "Unable to get Instrumentation" and computes sizes from a model of the layout instead
# of asking, which is the fallback path the issue is worried about.
ATTACH = {
    "attach_self": ["-Djdk.attach.allowAttachSelf=true"],
    "no_attach": [],
}

# The flags the VM reports for each configuration, so the run proves the configuration
# happened rather than assuming the command line took.
WATCHED = ["UseCompactObjectHeaders", "UseCompressedOops", "ObjectAlignmentInBytes"]

# " @8 "a" I 4/4 REGULAR" and its inherited, flattened and padding relatives.
VM_FIELD = re.compile(r'^ @(\d+) "([^"]+)" (\S+) (\d+)/(\S+) (\w+)$')
VM_FILLER = re.compile(r"^ @(\d+) (\d+)/(\S+) (\w+)$")
VM_CLASS = re.compile(r"^Layout of class (\S+)$")
VM_SIZE = re.compile(r"^Instance size = (\d+) bytes$")

JOL_LAYOUT = re.compile(
    r"^JVX\|layout\|(\w+)\|(\S+?)\|instanceSize=(\d+)\|headerSize=(\d+)$")
JOL_FIELD = re.compile(r"^JVX\|field\|(\w+)\|(\S+?)\|([^|]+)\|(\d+)\|(\d+)\|(.+)$")
JOL_VM = re.compile(r"^JVX\|vm\|(.*)$")
JOL_DETAILS = re.compile(r"^JVX\|details\|(.*)$")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def jol_jar() -> pathlib.Path:
    """The jar, downloaded once and checked every time.

    A layout probe that measured a jar somebody swapped would be worse than no probe, and
    this is the only thing here that comes off the network.
    """
    path = WORK / f"jol-core-{JOL['version']}.jar"
    if not path.is_file():
        with urllib.request.urlopen(JOL["url"], timeout=120) as response:
            path.write_bytes(response.read())
    found = sha256(path)
    if found != JOL["sha256"]:
        sys.exit(f"{path} hashes {found}, expected {JOL['sha256']}")
    return path


def fastdebug() -> dict:
    """The build `build.py` made, and the provenance that travels with the measurement."""
    path = WORK / "build.json"
    if not path.is_file():
        sys.exit(f"{path} is missing, run probes/fieldlayout/build.py first")
    built = json.loads(path.read_text(encoding="utf-8"))
    if built.get("error") or not built.get("jdk"):
        sys.exit(f"the build in {path} did not finish: {built.get('error')}")
    if not built["jdk"].get("print_field_layout_accepted"):
        sys.exit(
            f"the build in {path} does not accept -XX:+PrintFieldLayout, which is the "
            f"one thing it exists for"
        )
    return built


def compile_dump(home: pathlib.Path, jar: pathlib.Path) -> pathlib.Path:
    """`JolDump.java`, compiled by the build under test."""
    classes = WORK / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    done = run([
        str(home / "bin" / "javac"), "-cp", str(jar), "-d", str(classes),
        str(REPO / "probes" / "fieldlayout" / "JolDump.java"),
    ])
    if done.returncode != 0:
        sys.exit(f"JolDump.java did not compile: {done.stderr}")
    return classes


def flags(home: pathlib.Path, config: list[str]) -> dict:
    """What the VM says the watched flags are, once the configuration has been applied."""
    done = run([str(home / "bin" / "java"), "-XX:+PrintFlagsFinal"] + config + ["-version"])
    found: dict[str, str] = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] in WATCHED:
            found[parts[1]] = parts[3]
    found["warnings"] = "\n".join(
        line for line in done.stderr.splitlines() if "warning" in line.lower())
    return found


def parse_vm(text: str, wanted: set[str]) -> dict[str, dict]:
    """The VM's own layout log, for the classes asked about.

    Every line inside a block that is neither a field nor a filler is kept in `unparsed`
    rather than dropped, because a layout the probe silently could not read is the exact
    shape of a false agreement.
    """
    found: dict[str, dict] = {}
    current: dict | None = None
    section = ""
    for line in text.splitlines():
        start = VM_CLASS.match(line)
        if start:
            name = start.group(1).replace("/", ".")
            current = {"fields": {}, "kinds": {}, "fillers": [], "unparsed": [],
                       "instance_size": None} if name in wanted else None
            if current is not None:
                found[name] = current
            section = ""
            continue
        if current is None:
            continue
        if line in ("Instance fields:", "Static fields:"):
            section = line
            continue
        size = VM_SIZE.match(line)
        if size:
            current["instance_size"] = int(size.group(1))
            continue
        if line == "---":
            current = None
            continue
        if section != "Instance fields:":
            continue
        field = VM_FIELD.match(line)
        if field:
            offset, name, signature, width, _, kind = field.groups()
            current["fields"][name] = int(offset)
            current["kinds"][name] = {"kind": kind, "signature": signature,
                                      "size": int(width)}
            continue
        filler = VM_FILLER.match(line)
        if filler:
            current["fillers"].append(
                {"offset": int(filler.group(1)), "size": int(filler.group(2)),
                 "kind": filler.group(4)})
            continue
        current["unparsed"].append(line)
    return found


def parse_jol(text: str) -> dict:
    """What JOL said, and what it warned about while saying it."""
    found: dict = {"vm": None, "details": None, "layouts": {}, "warnings": []}
    for line in text.splitlines():
        described = JOL_VM.match(line)
        if described:
            found["vm"] = dict(
                pair.split("=", 1) for pair in described.group(1).split("|") if "=" in pair)
            continue
        details = JOL_DETAILS.match(line)
        if details:
            found["details"] = details.group(1).strip()
            continue
        layout = JOL_LAYOUT.match(line)
        if layout:
            mode, name, size, header = layout.groups()
            found["layouts"].setdefault(name, {})[mode] = {
                "instance_size": int(size), "header_size": int(header), "fields": {}}
            continue
        field = JOL_FIELD.match(line)
        if field:
            mode, name, field_name, offset, _, _ = field.groups()
            found["layouts"][name][mode]["fields"][field_name] = int(offset)
            continue
        if line.startswith("# WARNING") or line.startswith("WARNING"):
            found["warnings"].append(line.strip())
    return found


def compare(vm: dict[str, dict], jol: dict) -> dict:
    """Class by class, whether JOL and the VM say the same thing.

    A class the VM did not print is not an agreement. It is a missing measurement, and it
    is recorded as one rather than passed over, because a comparison of nothing with
    nothing succeeds.
    """
    found: dict = {}
    for name in CLASSES:
        said = jol["layouts"].get(name)
        told = vm.get(name)
        entry: dict = {"vm_printed": told is not None, "jol_answered": said is not None}
        if told is None or said is None:
            entry["agrees"] = False
            entry["why"] = "the VM did not print this class" if told is None \
                else "JOL did not answer for this class"
            found[name] = entry
            continue
        entry["vm"] = {"instance_size": told["instance_size"], "fields": told["fields"]}
        entry["jol"] = {}
        entry["differences"] = []
        for mode, answer in sorted(said.items()):
            entry["jol"][mode] = {"instance_size": answer["instance_size"],
                                  "header_size": answer["header_size"],
                                  "fields": answer["fields"]}
            if answer["instance_size"] != told["instance_size"]:
                entry["differences"].append(
                    f"{mode} says the instance is {answer['instance_size']} bytes and "
                    f"the VM says {told['instance_size']}")
            for field, offset in sorted(told["fields"].items()):
                if field not in answer["fields"]:
                    entry["differences"].append(f"{mode} has no field {field}")
                elif answer["fields"][field] != offset:
                    entry["differences"].append(
                        f"{mode} puts {field} at {answer['fields'][field]} and the VM "
                        f"puts it at {offset}")
            for field in sorted(answer["fields"]):
                if field not in told["fields"]:
                    entry["differences"].append(
                        f"{mode} has a field {field} the VM did not print")
        entry["agrees"] = not entry["differences"]
        found[name] = entry
    return found


def measure(home: pathlib.Path, classes: pathlib.Path, jar: pathlib.Path,
            config: list[str], attach: list[str]) -> dict:
    command = [str(home / "bin" / "java"), "-XX:+PrintFieldLayout", "-Xshare:off"] \
        + config + attach \
        + ["-cp", f"{classes}:{jar}", "JolDump"] + CLASSES
    done = run(command)
    text = done.stdout + "\n" + done.stderr
    vm = parse_vm(text, set(CLASSES))
    jol = parse_jol(text)
    return {
        "command": command[1:],
        "ok": done.returncode == 0,
        "jol": {"vm": jol["vm"], "details": jol["details"], "warnings": jol["warnings"]},
        "classes": compare(vm, jol),
        "unparsed": sorted({line for found in vm.values() for line in found["unparsed"]}),
    }


def environment(home: pathlib.Path, built: dict) -> dict:
    done = run([str(home / "bin" / "java"), "-version"])
    return {
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "java_build": (done.stderr or done.stdout).strip().splitlines()[-1].strip(),
        "container_image": os.environ.get("JVX_IMAGE"),
        "built_from": {
            "commit": built["source"]["commit"],
            "commit_matches_pin": built["source"]["commit_matches_pin"],
            "configure_flags": built["configure_flags"],
            "make_seconds": built["make"]["seconds"],
            "jobs": built["jobs"],
        },
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results file")
    args = ap.parse_args(argv)

    built = fastdebug()
    home = pathlib.Path(built["jdk"]["home"])
    jar = jol_jar()
    classes = compile_dump(home, jar)

    found: dict = {
        "probe": "fieldlayout",
        "issue": 5,
        "measured": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "jol": JOL,
        "classes_asked": CLASSES,
        "environment": environment(home, built),
        "configurations": {},
    }

    for config, config_flags in CONFIGS.items():
        print(f"== {config}", file=sys.stderr, flush=True)
        entry: dict = {"flags_asked": config_flags,
                       "flags_reported": flags(home, config_flags), "runs": {}}
        for mode, attach_flags in ATTACH.items():
            entry["runs"][mode] = measure(home, classes, jar, config_flags, attach_flags)
        found["configurations"][config] = entry

    # The threshold issue #5 set, computed rather than eyeballed.
    found["agrees_everywhere"] = all(
        found["configurations"][c]["runs"][m]["classes"][k]["agrees"]
        for c in CONFIGS for m in ATTACH for k in CLASSES
    )
    return write(args.out, found)


def write(out: pathlib.Path | None, found: dict) -> int:
    text = json.dumps(found, indent=2, sort_keys=True) + "\n"
    if out is None:
        print(text)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
