"""Issue #1, as something a person can run: is a cold Colab runtime a JDK 27 kernel in 90 seconds.

This is the source. The notebook is built from it by `tools/gen_handoff.py` and the
protocol for running it is `docs/probes/handoff.md`. Editing the notebook by hand is
wasted work, because the next build overwrites it.

Everything here runs on Colab's Python kernel, including the part that installs the Java
kernel, because a notebook cannot measure its own installation from inside the thing
being installed. The three questions that need a pair of eyes on a browser are asked at
the end rather than assumed.
"""

# %% [markdown]
# # Can a cold Colab runtime become a pinned JDK 27 kernel in 90 seconds
#
# [Issue #1](https://github.com/tamnd/jvm-internals/issues/1). Every lesson in this
# project opens with one bootstrap cell, and the promise that a reader needs no install
# rests entirely on that cell working on a free Colab runtime. The installer is
# documented to work here. Documented is not measured, and this is the measurement.
#
# **Before you start.** Use a fresh runtime. Runtime, then Disconnect and delete
# runtime, then reconnect. A warm runtime with half of this already on it measures
# nothing, because the whole question is what a cold start costs.
#
# Run every cell top to bottom, once, without skipping. It takes about five minutes,
# most of which is one download. The last cell prints a JSON block. Copy that block and
# paste it back, and it becomes `probes/bootstrap/results/colab-free-<attempt>.json` and
# then the report.
#
# The three questions at the end are the ones no script can answer, because they are
# about what a menu shows and what a browser does. Answer them from what you see, not
# from what you expect.

# %%
# Which attempt this is. The issue asks for ten runs and this notebook does one, so run
# it three times in three fresh runtimes and change this number each time. Three is what
# fits in one sitting, and a probe that asks for more than somebody will do gets no
# answers at all.
ATTEMPT = 1

# The pin. Every number this project publishes came off this build, and a kernel running
# some other JDK 27 is a different measurement wearing the same name.
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
import time

STEPS = []
NOTES = []


def step(name, command, timeout=900, shell=True, env=None):
    """Run one step, time it, and keep the result whether it worked or not.

    A failed step is a result. The cost of the bootstrap is the cost of the path a
    reader actually takes, and if `install-kernel` fails on the day this runs then that
    is the answer to the issue rather than a reason to stop the notebook.
    """
    started = time.monotonic()
    try:
        done = subprocess.run(
            command, shell=shell, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})})
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        code, out, err = None, "", f"timed out after {timeout}s"
    seconds = round(time.monotonic() - started, 2)
    STEPS.append({
        "step": name,
        "command": command if isinstance(command, str) else " ".join(command),
        "seconds": seconds,
        "exit": code,
        "ok": code == 0,
        # Both streams, tail only. The reason a step failed is usually in the last few
        # lines and never in the first, and a full Maven log would swamp the paste.
        "stdout_tail": out[-2000:],
        "stderr_tail": err[-2000:],
    })
    print(f"[{seconds:7.2f}s] {'ok  ' if code == 0 else 'FAIL'} {name}")
    return STEPS[-1]


def seconds(*names):
    """Wall clock across a named set of steps, which is what the threshold is about."""
    return round(sum(s["seconds"] for s in STEPS if s["step"] in names), 2)


print("ready, attempt", ATTEMPT)

# %% [markdown]
# ## What machine this is
#
# A free runtime is not one machine, it is whatever the scheduler had. Two runs that
# disagree about the wall clock are worth nothing unless this cell says whether they
# were the same size of box.

# %%
MACHINE = {
    "python": sys.version.split()[0],
    "platform": platform.platform(),
    "machine": platform.machine(),
    "cpus": os.cpu_count(),
    "colab": "google.colab" in sys.modules or pathlib.Path("/content").is_dir(),
}

for path, key in [("/proc/meminfo", "memory"), ("/proc/cpuinfo", "cpu_model")]:
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        continue
    if key == "memory":
        MACHINE["memory_kb"] = int(text.split("MemTotal:")[1].split()[0])
    else:
        for line in text.splitlines():
            if line.startswith("model name"):
                MACHINE["cpu_model"] = line.split(":", 1)[1].strip()
                break

usage = shutil.disk_usage("/content" if pathlib.Path("/content").is_dir() else "/")
MACHINE["disk_free_gb"] = round(usage.free / 1e9, 1)
MACHINE["java_before"] = shutil.which("java")

print(json.dumps(MACHINE, indent=2))

# %% [markdown]
# ## Route A: the documented one
#
# `pip install jbang`, then the JBang trust step, then `install-kernel@jupyter-java`
# asking for Java 27. This is the path the issue names and the one a reader would find.
# Each part is timed separately so that a regression later can be attributed to a part
# rather than guessed at.

# %%
step("A.pip_install_jbang", "pip install --quiet jbang")
step("A.jbang_version", "python -m jbang exec --quiet version 2>&1 | tail -5")
step("A.trust", "python -m jbang exec trust add https://github.com/jupyter-java")
step("A.install_kernel", "python -m jbang exec install-kernel@jupyter-java --java 27")

print()
print("route A wall clock:", seconds(
    "A.pip_install_jbang", "A.jbang_version", "A.trust", "A.install_kernel"), "s")

# %% [markdown]
# ## What Route A actually installed
#
# The question is not whether the command exited zero. It is whether there is now a
# kernel a reader can select, and whether the JDK behind it is 27. An installer that
# quietly resolves 21 and registers a working kernel passes every check except the one
# that matters.

# %%
KERNELS = {}
found = step("A.kernelspec_list", "jupyter kernelspec list --json")
if found["ok"]:
    try:
        specs = json.loads(found["stdout_tail"])["kernelspecs"]
    except (ValueError, KeyError):
        specs = {}
    for name, spec in specs.items():
        entry = {"display_name": spec.get("spec", {}).get("display_name"),
                 "language": spec.get("spec", {}).get("language"),
                 "argv": spec.get("spec", {}).get("argv", [])}
        KERNELS[name] = entry

JAVA_KERNELS = {n: k for n, k in KERNELS.items()
                if (k.get("language") or "").lower() == "java"}
print("kernels:", sorted(KERNELS))
print("java kernels:", sorted(JAVA_KERNELS))

# The JDK behind the kernel, taken from the kernel's own launch line rather than from
# PATH. What `java -version` says in a shell is not necessarily what the kernel runs.
KERNEL_JAVA = {}
for name, spec in JAVA_KERNELS.items():
    launcher = spec["argv"][0] if spec["argv"] else None
    if launcher and pathlib.Path(launcher).exists():
        done = step(f"A.version_of[{name}]", f"{launcher} -version", timeout=120)
        KERNEL_JAVA[name] = {"launcher": launcher,
                             "version_output": (done["stderr_tail"]
                                                or done["stdout_tail"]).strip()}
    else:
        KERNEL_JAVA[name] = {"launcher": launcher, "version_output": None}

print(json.dumps(KERNEL_JAVA, indent=2))

# %% [markdown]
# ## Route B: the pinned build, fetched by hand
#
# Route A resolves whatever JBang thinks Java 27 means today. This project pins one
# build, and the fallback in the issue is to fetch that tarball and register the kernel
# against it. `tools/fetch_jdk.py` checks the download against the SHA256 in
# `docs/pin.json` and refuses anything else, so this cell either produces the pinned
# build or produces nothing.
#
# Timed separately because if Route A resolves the wrong build then this is the cost a
# reader pays instead, and the report needs both numbers to say which shape the lessons
# should take.

# %%
step("B.clone", f"git clone --depth 1 --branch {BRANCH} {REPO} /content/jvm-internals")
step("B.fetch_pinned_jdk",
     "python /content/jvm-internals/tools/fetch_jdk.py --dir /content/jvx")

PIN = {}
try:
    PIN = json.loads(pathlib.Path(
        "/content/jvm-internals/docs/pin.json").read_text())
except OSError:
    NOTES.append("the clone did not produce docs/pin.json, so nothing here is pinned")

PINNED_HOME = None
for candidate in sorted(pathlib.Path("/content/jvx").glob("jdk-*")):
    if (candidate / "bin" / "java").exists():
        PINNED_HOME = candidate
    elif (candidate / "Contents" / "Home" / "bin" / "java").exists():
        PINNED_HOME = candidate / "Contents" / "Home"

print("pin:", PIN.get("jdk_tag"), PIN.get("jdk_build"))
print("pinned JAVA_HOME:", PINNED_HOME)

if PINNED_HOME:
    step("B.pinned_version", f"{PINNED_HOME}/bin/java -version")
    step("B.register_kernel",
         f"python -m jbang exec install-kernel@jupyter-java "
         f"--java-home {PINNED_HOME}")

print()
print("route B wall clock:", seconds("B.clone", "B.fetch_pinned_jdk",
                                     "B.pinned_version", "B.register_kernel"), "s")

# %% [markdown]
# ## Does the kernel's JDK still have the tools the lessons need
#
# `jcmd`, `jhsdb` and `-agentpath` are what most of the curriculum is built on. A kernel
# that runs Java but sits on a runtime with the serviceability tools stripped out would
# pass a hello world and fail from lesson four onwards.
#
# This asks the JDK the kernel launches, which is the same image but not the same
# process. Whether the kernel's own launch path strips options is the question the live
# check in the next section covers.

# %%
TOOLING = {}
home = PINNED_HOME
if home is None and KERNEL_JAVA:
    launcher = next(iter(KERNEL_JAVA.values()))["launcher"]
    home = pathlib.Path(launcher).resolve().parent.parent if launcher else None

if home is None:
    NOTES.append("no JDK to ask about tooling, so both routes failed")
else:
    for tool in ["java", "javac", "jshell", "jcmd", "jhsdb", "jmap", "jstack", "jfr",
                 "javap", "jdb"]:
        TOOLING[tool] = (home / "bin" / tool).exists()
    done = step("tooling.jcmd_self", f"{home}/bin/jcmd -l", timeout=120)
    TOOLING["jcmd_lists_processes"] = done["ok"]
    # An agent that does nothing, attached to a JVM that does nothing. The point is
    # whether the launcher accepts -agentpath at all, not what the agent does.
    done = step("tooling.agentpath_rejected_cleanly",
                f"{home}/bin/java -agentpath:/nonexistent.so -version", timeout=120)
    text = (done["stderr_tail"] + done["stdout_tail"]).lower()
    TOOLING["agentpath_understood"] = ("could not find agent library" in text
                                       or "agent library failed" in text)
    TOOLING["agentpath_message"] = (done["stderr_tail"] or done["stdout_tail"])[-300:]

print(json.dumps(TOOLING, indent=2))

# %% [markdown]
# ## The bootstrap cell itself
#
# The lessons open with one cell that defines the `jvx` helper surface and prints a
# banner. Piping it through `jshell` is not the same as running it in the kernel, but it
# is the same source through the same JDK, and it is the part of the 90 seconds that
# belongs to this project rather than to the installer.

# %%
BOOTSTRAP = {}
lesson = pathlib.Path("/content/jvm-internals/notebooks/O01/lesson.ipynb")
if home and lesson.exists():
    cells = json.loads(lesson.read_text())["cells"]
    code = [c for c in cells if c["cell_type"] == "code"]
    first = "".join(code[0]["source"]) if code else ""
    pathlib.Path("/content/bootstrap.jsh").write_text(first + "\n/exit\n")
    done = step("bootstrap.jshell",
                f"{home}/bin/jshell --execution local -q /content/bootstrap.jsh",
                timeout=600)
    BOOTSTRAP = {
        "ran": done["ok"],
        "seconds": done["seconds"],
        "chars": len(first),
        "banner_tail": done["stdout_tail"][-600:],
    }
else:
    NOTES.append("no bootstrap cell to run, so the clone or both routes failed")

print(json.dumps(BOOTSTRAP, indent=2)[:1500])

# %% [markdown]
# ## The three questions a script cannot answer
#
# Answer from what the browser shows you, right now, not from what you remember.
#
# 1. **The menu.** Open Runtime, then Change runtime type. Is there a Java entry in the
#    list? If the only way to reach the Java kernel is a menu the reader has to be told
#    about, the lesson format needs a different shape and the issue says so.
# 2. **The switch.** Switch to it if it is there, then run `System.out.println(1 + 1);`
#    in a new cell. Does it print 2, and how long did the switch take before the cell
#    would run?
# 3. **The reclaim.** This one costs you the session. Runtime, then Disconnect and
#    delete runtime. Reconnect and run this whole notebook again from the top. Does it
#    complete with no half installed state? Answer no on your first pass and yes or no
#    on your second, and set ATTEMPT to 2 before you rerun.

# %%
def ask(question, allowed=("y", "n", "skip")):
    while True:
        answer = input(f"{question} [{'/'.join(allowed)}] ").strip().lower()
        if answer in allowed:
            return answer
        print("  one of:", ", ".join(allowed))


HUMAN = {
    "java_in_runtime_menu": ask("1. Does Runtime > Change runtime type list Java?"),
    "cell_printed_two": ask("2. After switching, did System.out.println(1 + 1) print 2?"),
    "seconds_to_switch": input("2b. Roughly how many seconds from clicking to a "
                               "runnable cell? (number, or skip) ").strip(),
    "clean_after_reclaim": ask("3. Did a delete and full rerun complete cleanly?"),
    "anything_surprising": input("4. Anything the cells above would not have "
                                 "noticed? (one line, or blank) ").strip(),
}
print(json.dumps(HUMAN, indent=2))

# %% [markdown]
# ## The result
#
# Copy everything between the two rule lines and paste it back. That is the whole
# deliverable. Nothing else in this notebook needs to be saved, and a screenshot of the
# timings is not a substitute, because the report is generated from the numbers.

# %%
COLD = seconds("A.pip_install_jbang", "A.jbang_version", "A.trust", "A.install_kernel")

RESULT = {
    "probe": "bootstrap",
    "issue": 1,
    "platform": "colab-free",
    "attempt": ATTEMPT,
    "measured": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
    "pin": {"jdk_tag": PIN.get("jdk_tag"), "jdk_build": PIN.get("jdk_build")},
    "machine": MACHINE,
    "route_a_seconds": COLD,
    "route_b_seconds": seconds("B.clone", "B.fetch_pinned_jdk", "B.pinned_version",
                               "B.register_kernel"),
    # The thresholds from the issue, evaluated here rather than in prose later, so that
    # a run either passes or does not and nobody gets to decide afterwards what counted.
    "under_40_seconds": COLD < 40,
    "under_90_seconds": COLD < 90,
    "kernels": KERNELS,
    "java_kernels": sorted(JAVA_KERNELS),
    "kernel_java": KERNEL_JAVA,
    "pinned_home": str(PINNED_HOME) if PINNED_HOME else None,
    "tooling": TOOLING,
    "bootstrap": BOOTSTRAP,
    "human": HUMAN,
    "notes": NOTES,
    "steps": STEPS,
}

print("-" * 72)
print(json.dumps(RESULT, indent=1, sort_keys=True))
print("-" * 72)
