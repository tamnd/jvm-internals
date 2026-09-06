"""Issue #6, as something a person can run: does an OpenJDK build fit in a free Colab session.

This is the source. The notebook is built from it by `tools/gen_handoff.py` and the
protocol for running it is `docs/probes/handoff.md`.

One configuration per run, because a session that tries both is a session that gets
reclaimed halfway through the second and produces two half answers instead of one whole
one. Run it twice, in two fresh runtimes, changing CONFIG.
"""

# %% [markdown]
# # Does an OpenJDK build fit in a free Colab session
#
# [Issue #6](https://github.com/tamnd/jvm-internals/issues/6). X09 and much of the third
# pass are built on the reader compiling HotSpot themselves. If that does not fit in a
# free runtime, X09 becomes a recorded replay and a claim comes out of the README. The
# comparable project in this series got a flat no to the same question, which is why
# this one gets measured before it is advertised.
#
# **Before you start.** Fresh runtime. Runtime, then Disconnect and delete runtime, then
# reconnect. Then leave the tab open and visible. Colab reclaims idle runtimes, and a
# build that dies because you switched to another tab for forty minutes measures your
# browsing rather than the build.
#
# This takes between forty minutes and the two hour cap set below. The cells sample the
# machine while `make` runs, so a run that gets killed still produces a result saying
# how far it got and what ran out.
#
# The last cell prints a JSON block. Copy it and paste it back.

# %%
# Which build. Run this notebook twice, once for each, in two fresh runtimes.
# "release" is the one the threshold is about. "slowdebug" is the one that would let a
# reader use the assertions and the develop flags, and the likely answer is that it does
# not fit, which is a fine result as long as the lesson says so.
CONFIG = "release"

# The wall clock at which this stops and reports what it had. Two hours is longer than
# any published estimate for this build and short enough to leave room in a session to
# paste the answer back.
MAX_MINUTES = 120

REPO = "https://github.com/tamnd/jvm-internals"
JDK_REPO = "https://github.com/openjdk/jdk.git"

import datetime
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

STEPS = []
SAMPLES = []
NOTES = []
STARTED = time.monotonic()


def free_gb(path="/content"):
    return round(shutil.disk_usage(path).free / 1e9, 2)


def memory():
    try:
        text = pathlib.Path("/proc/meminfo").read_text()
    except OSError:
        return {}
    found = dict(re.findall(r"^(\w+):\s+(\d+) kB$", text, re.MULTILINE))
    return {"total_kb": int(found.get("MemTotal", 0)),
            "available_kb": int(found.get("MemAvailable", 0))}


def step(name, command, timeout=1800, env=None):
    started = time.monotonic()
    try:
        done = subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, **(env or {})})
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        code, out, err = None, "", f"timed out after {timeout}s"
    seconds = round(time.monotonic() - started, 2)
    STEPS.append({"step": name, "command": command, "seconds": seconds, "exit": code,
                  "ok": code == 0, "stdout_tail": out[-3000:],
                  "stderr_tail": err[-3000:], "disk_free_gb": free_gb()})
    print(f"[{seconds:8.2f}s] {'ok  ' if code == 0 else 'FAIL'} {name}")
    return STEPS[-1]


print("config:", CONFIG, "| cpus:", os.cpu_count(), "| free:", free_gb(), "GB")

# %% [markdown]
# ## What machine this is
#
# The answer to the issue is a wall clock, and a wall clock means nothing without the
# core count next to it. Free runtimes are not one machine.

# %%
MACHINE = {
    "python": sys.version.split()[0],
    "platform": platform.platform(),
    "machine": platform.machine(),
    "cpus": os.cpu_count(),
    "memory": memory(),
    "disk_free_gb_before": free_gb(),
    "colab": pathlib.Path("/content").is_dir(),
    "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
print(json.dumps(MACHINE, indent=2))

# %% [markdown]
# ## The pinned source and a boot JDK
#
# The tag comes out of `docs/pin.json` in this project rather than being typed here, so
# that a build measured today and a build measured after the version bump are labelled
# with what they actually were. The boot JDK is the same pinned build the rest of the
# project uses, fetched and hash checked by `tools/fetch_jdk.py`.

# %%
step("apt_build_deps",
     "apt-get -qq update && apt-get -qq install -y build-essential autoconf "
     "zip unzip libx11-dev libxext-dev libxrender-dev libxrandr-dev libxtst-dev "
     "libxt-dev libcups2-dev libfontconfig1-dev libasound2-dev")

step("clone_project", f"git clone --depth 1 {REPO} /content/jvm-internals")

PIN = {}
try:
    PIN = json.loads(pathlib.Path("/content/jvm-internals/docs/pin.json").read_text())
except OSError:
    NOTES.append("no pin.json, so the tag below is not pinned to anything")

TAG = PIN.get("jdk_tag", "jdk-27+35")
COMMIT = PIN.get("jdk_tag_commit")
print("building", TAG, COMMIT)

step("fetch_boot_jdk",
     "python /content/jvm-internals/tools/fetch_jdk.py --dir /content/jvx")
BOOT = None
for candidate in sorted(pathlib.Path("/content/jvx").glob("jdk-*")):
    if (candidate / "bin" / "javac").exists():
        BOOT = candidate
print("boot JDK:", BOOT)

# A shallow clone of one tag. The full history is over two gigabytes of objects nobody
# here needs, and disk is the resource most likely to run out.
step("clone_jdk",
     f"git clone --depth 1 --branch {TAG} {JDK_REPO} /content/jdk", timeout=3600)

HEAD = step("jdk_head", "git -C /content/jdk rev-parse HEAD")
AT_PIN = HEAD["ok"] and COMMIT is not None and HEAD["stdout_tail"].strip() == COMMIT
if not AT_PIN:
    NOTES.append(f"the checkout is not at {COMMIT}, so this is a build of "
                 f"{HEAD['stdout_tail'].strip() or 'nothing'}")
print("at the pinned commit:", AT_PIN)

# %% [markdown]
# ## Configure
#
# Fast enough to be uninteresting on its own, and worth timing separately anyway,
# because a configure that fails on a missing library is a completely different answer
# to the issue than a make that runs out of session.

# %%
DEBUG = {"release": "release", "slowdebug": "slowdebug", "fastdebug": "fastdebug"}[CONFIG]

step("configure",
     f"cd /content/jdk && bash configure --with-boot-jdk={BOOT} "
     f"--with-debug-level={DEBUG} --with-native-debug-symbols=none "
     f"--disable-warnings-as-errors",
     timeout=1800)

# %% [markdown]
# ## Make
#
# Sampled once a minute while it runs, because "it did not fit" needs a reason attached.
# Out of disk, out of memory and out of session are three different answers and only one
# of them is fixed by a smaller build.

# %%
LOG = pathlib.Path("/content/make.log")
DEADLINE = time.monotonic() + MAX_MINUTES * 60
stop = threading.Event()


def sample():
    while not stop.wait(60):
        SAMPLES.append({
            "minute": round((time.monotonic() - STARTED) / 60, 1),
            "disk_free_gb": free_gb(),
            "memory": memory(),
            "log_lines": sum(1 for _ in LOG.open()) if LOG.exists() else 0,
        })


watcher = threading.Thread(target=sample, daemon=True)
watcher.start()

started = time.monotonic()
with LOG.open("w") as sink:
    make = subprocess.Popen(f"cd /content/jdk && make images", shell=True,
                            stdout=sink, stderr=subprocess.STDOUT, text=True)
    killed = False
    while make.poll() is None:
        if time.monotonic() > DEADLINE:
            make.kill()
            killed = True
            NOTES.append(f"killed at the {MAX_MINUTES} minute cap")
            break
        time.sleep(5)
stop.set()

MAKE = {
    "seconds": round(time.monotonic() - started, 2),
    "exit": make.returncode,
    "ok": make.returncode == 0 and not killed,
    "killed_at_cap": killed,
    "log_lines": sum(1 for _ in LOG.open()) if LOG.exists() else 0,
    "log_tail": LOG.read_text()[-4000:] if LOG.exists() else "",
}
print("make:", round(MAKE["seconds"] / 60, 1), "minutes, ok:", MAKE["ok"])

# %% [markdown]
# ## Did it produce a JDK that runs
#
# An exit status of zero is a claim about `make`. This is the check that there is a
# launcher on disk that starts, reports the build it was made from, and runs a class.

# %%
BUILT = {}
images = sorted(pathlib.Path("/content/jdk/build").glob("*/images/jdk"))
if images:
    home = images[0]
    BUILT["home"] = str(home)
    done = step("built_version", f"{home}/bin/java -version")
    BUILT["version_output"] = (done["stderr_tail"] or done["stdout_tail"]).strip()
    BUILT["runs"] = done["ok"]
    pathlib.Path("/content/Hello.java").write_text(
        "public class Hello { public static void main(String[] a) {"
        " System.out.println(Runtime.version()); } }\n")
    done = step("built_runs_a_class", f"{home}/bin/java /content/Hello.java")
    BUILT["ran_a_class"] = done["ok"]
    BUILT["class_output"] = done["stdout_tail"].strip()
    BUILT["image_size_gb"] = round(
        sum(f.stat().st_size for f in home.rglob("*") if f.is_file()) / 1e9, 2)
else:
    NOTES.append("no images/jdk directory, so make did not get to the end")

print(json.dumps(BUILT, indent=2)[:1200])

# %% [markdown]
# ## The one question a script cannot answer
#
# Whether the tab stayed connected. A build that finished while you were watching and a
# build that finished while the runtime was being reclaimed are different results, and
# only you know which happened.

# %%
HUMAN = {
    "tab_stayed_connected": input(
        "Did the runtime stay connected the whole time? [y/n/unsure] ").strip().lower(),
    "reconnects": input(
        "How many times did you have to reconnect? (number) ").strip(),
    "anything_surprising": input(
        "Anything the cells would not have noticed? (one line, or blank) ").strip(),
}
print(json.dumps(HUMAN, indent=2))

# %% [markdown]
# ## The result
#
# Copy everything between the two rule lines and paste it back.

# %%
RESULT = {
    "probe": "colabbuild",
    "issue": 6,
    "platform": "colab-free",
    "configuration": CONFIG,
    "measured": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
    "pin": {"jdk_tag": PIN.get("jdk_tag"), "jdk_tag_commit": PIN.get("jdk_tag_commit")},
    "at_the_pinned_commit": AT_PIN,
    "machine": MACHINE,
    "make": MAKE,
    "built": BUILT,
    "fits": bool(MAKE["ok"] and BUILT.get("ran_a_class")),
    "total_minutes": round((time.monotonic() - STARTED) / 60, 1),
    "disk_free_gb_after": free_gb(),
    "samples": SAMPLES,
    "human": HUMAN,
    "notes": NOTES,
    "steps": STEPS,
}

print("-" * 72)
print(json.dumps(RESULT, indent=1, sort_keys=True))
print("-" * 72)
