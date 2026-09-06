"""Issue #11, the missing column: what a free Colab runtime can actually do.

This is the source. The notebook is built from it by `tools/gen_handoff.py` and the
protocol for running it is `docs/probes/handoff.md`.

Nothing new gets measured here. `probes/capability/run.py` already asks 119 questions of
a JDK and the box under it, and it has answers from four machines. Colab is
the environment every lesson declares as E0 and the only one nobody has asked, which
makes it the one column in the matrix that matters most and the one that is empty.
"""

# %% [markdown]
# # What a free Colab runtime can actually do
#
# [Issue #11](https://github.com/tamnd/jvm-internals/issues/11). The capability matrix
# has four columns and none of them is the environment the lessons are written for. A
# lesson that declares `env: E0` is making a claim about a machine somebody else owns,
# and right now that claim is checked against three developer machines and a CI runner.
#
# This runs the existing probe here, unchanged. It takes about a minute of actual
# probing after a couple of minutes of download.
#
# **Before you start.** Fresh runtime, so that the answers describe a runtime a reader
# would get rather than one you have been installing things into. Run every cell. The
# last one prints a JSON block to paste back.

# %%
REPO = "https://github.com/tamnd/jvm-internals"
BRANCH = "main"

import datetime
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys

NOTES = []


def run(command, timeout=1200):
    print("$", command)
    done = subprocess.run(command, shell=True, capture_output=True, text=True,
                          timeout=timeout)
    if done.returncode != 0:
        print(done.stdout[-2000:])
        print(done.stderr[-2000:], file=sys.stderr)
    return done


run(f"git clone --depth 1 --branch {BRANCH} {REPO} /content/jvm-internals")

# The pinned build, hash checked. Asking these questions of whatever java the runtime
# image happens to ship would produce a column about Colab's default JDK, which is a
# different and much less useful fact than what the lessons run on.
run("python /content/jvm-internals/tools/fetch_jdk.py --dir /content/jvx")

HOME = None
for candidate in sorted(pathlib.Path("/content/jvx").glob("jdk-*")):
    if (candidate / "bin" / "java").exists():
        HOME = candidate
print("JAVA_HOME:", HOME)
if HOME is None:
    NOTES.append("the pinned JDK did not install, so there is nothing to ask")

# %% [markdown]
# ## The runtime a reader would get
#
# Recorded next to the answers because several of them depend on it. Whether an
# unprivileged process can attach to another one is a property of the box, not of the
# JDK, and a matrix column with no box description next to it is an assertion.

# %%
MACHINE = {
    "platform": platform.platform(),
    "machine": platform.machine(),
    "cpus": os.cpu_count(),
    "root": os.geteuid() == 0 if hasattr(os, "geteuid") else None,
    "disk_free_gb": round(shutil.disk_usage("/content").free / 1e9, 1),
    "colab": pathlib.Path("/content").is_dir(),
}

for path in ["/proc/sys/kernel/yama/ptrace_scope", "/proc/sys/kernel/perf_event_paranoid"]:
    try:
        MACHINE[pathlib.Path(path).name] = pathlib.Path(path).read_text().strip()
    except OSError:
        MACHINE[pathlib.Path(path).name] = None

print(json.dumps(MACHINE, indent=2))

# %% [markdown]
# ## The probe
#
# 119 checks: which tools are in the image, which flags the VM still accepts,
# whether it will let you attach to a process, whether it will let you open `java.base`,
# whether there is a disassembler behind `PrintAssembly`. It prints its progress to the
# error stream, so a long pause with nothing on screen is normal.

# %%
OUT = pathlib.Path("/content/colab.json")
done = subprocess.run(
    f"python /content/jvm-internals/probes/capability/run.py --out {OUT}",
    shell=True, capture_output=True, text=True, timeout=3600,
    env={**os.environ, "JAVA_HOME": str(HOME) if HOME else ""})
print(done.stderr[-3000:])

ANSWERS = {}
if OUT.exists():
    ANSWERS = json.loads(OUT.read_text())
    print(ANSWERS["checks"], "checks")
    for key, value in ANSWERS["answers"].items():
        print(f"{key:<44} {value}")
else:
    NOTES.append("the probe wrote no output")
    print(done.stdout[-3000:])

# %% [markdown]
# ## The two questions a script cannot answer
#
# Both are about the runtime rather than the JDK, and both change what a lesson is
# allowed to assume.

# %%
HUMAN = {
    "runtime_type": input(
        "Which runtime is this? [cpu/t4/tpu/other] ").strip().lower(),
    "signed_in_free_tier": input(
        "Free tier, not Pro? [y/n] ").strip().lower(),
    "anything_surprising": input(
        "Anything the cells would not have noticed? (one line, or blank) ").strip(),
}
print(json.dumps(HUMAN, indent=2))

# %% [markdown]
# ## The result
#
# Copy everything between the two rule lines and paste it back. It lands as
# `probes/capability/results/colab-free.json` and becomes the fifth column of the
# matrix, with the extra keys stripped back out into the report.

# %%
RESULT = {
    **ANSWERS,
    # Named here rather than left to whoever receives the paste. The rest of this file
    # is the capability probe's own output shape, which carries no probe name because it
    # has always arrived in a file that had one.
    "probe": "capability",
    "issue": 11,
    "platform": "colab-free",
    "measured": datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0).isoformat(),
    "colab_machine": MACHINE,
    "human": HUMAN,
    "handoff_notes": NOTES,
}

print("-" * 72)
print(json.dumps(RESULT, indent=1, sort_keys=True))
print("-" * 72)
