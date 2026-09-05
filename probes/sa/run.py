#!/usr/bin/env python3
"""Who is allowed to read HotSpot's type database, and does it say the same thing every way in.

Issue #3. Blueprint section 2 is generated rather than transcribed, and the Serviceability
Agent's type database is where the struct layouts come from: already resolved, already per
configuration, already correct, and not a C++ parser. The open question was never whether
the database has the fields. It is who can get at it. #33 measured that `jhsdb` is refused
for an ordinary user on macOS and on Linux with `ptrace_scope` 1, which would make a
generated section 2 a root only artifact.

So this probe tries four ways in and records which ones open.

  attach_running       attach to a JVM that was already there
  attach_spawned       the reader starts the JVM and attaches to its own child
  attach_ptracer_any   the target opts in with prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY)
  core_self            the target dumps its own core and the SA reads that, no attach

and one control, `jhsdb jstack`, which is the thing #33 measured, so the two probes can be
lined up against each other.

Every route that opens is then asked for the same four types, and the answers are compared
with each other and with what `jhsdb clhsdb` prints, because a route that works and lies is
worse than one that is refused.

  JAVA_HOME=... python probes/sa/run.py --out probes/sa/results/linux-x64.json

Nothing here touches the network. It needs a few hundred megabytes of scratch space for the
core file and about a minute. Do not run it as root to see whether it works: root is a
separate measurement and the interesting one is the ordinary user.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import os
import pathlib
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
TYPE_DUMP = HERE / "TypeDump.java"
TARGET = HERE / "Target.java"

# The four from the issue. `markWord` is the one #28 already generates by parsing the
# header file, so it is the type where the two halves of section 2 can be checked against
# each other, and the other three are the ones that half cannot reach.
TYPES = ["oopDesc", "markWord", "Klass", "InstanceKlass"]

# The SA lives in a module nobody is meant to use, and says so.
OPEN = [
    "--add-modules", "jdk.hotspot.agent",
    "--add-exports", "jdk.hotspot.agent/sun.jvm.hotspot=ALL-UNNAMED",
    "--add-exports", "jdk.hotspot.agent/sun.jvm.hotspot.runtime=ALL-UNNAMED",
    "--add-exports", "jdk.hotspot.agent/sun.jvm.hotspot.types=ALL-UNNAMED",
]

# prctl(2), the constant spelled "Yama" in ASCII, and PR_SET_PTRACER_ANY, which is -1.
PR_SET_PTRACER = 0x59616D61
PR_SET_PTRACER_ANY = ctypes.c_ulong(-1)


def java_home() -> pathlib.Path:
    home = os.environ.get("JAVA_HOME")
    if home and (pathlib.Path(home) / "bin").is_dir():
        return pathlib.Path(home)
    sys.exit(
        "set JAVA_HOME to the pinned JDK first. tools/fetch_jdk.py will install it and "
        "print the path."
    )


def java_build(home: pathlib.Path) -> str:
    out = subprocess.run(
        [str(home / "bin" / "java"), "-XshowSettings:properties", "-version"],
        capture_output=True, text=True,
    )
    found = re.search(r"java\.runtime\.version = (\S+)", out.stdout + out.stderr)
    return found.group(1) if found else "unknown"


def first_line(text: str) -> str:
    """The one line of a stack trace worth writing down."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("at ") or line.startswith("Caused by"):
            continue
        return line[:300]
    return ""


def allow_any_tracer():
    """Let anything trace this process, from inside the process, before it becomes a JVM.

    This is the piece that makes an ordinary user's SA attach legal under ptrace_scope 1
    without asking anyone for anything: the *target* says who may trace it, and it says
    so about itself. It runs between fork and exec, so the JVM inherits it and never
    knows it happened.
    """
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(
        PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0
    )


def start_target(home: pathlib.Path, cwd: pathlib.Path, *, ptracer_any=False,
                 core=False) -> subprocess.Popen:
    """Start a JVM and wait until it says it is up."""
    before = []
    if ptracer_any:
        before.append(allow_any_tracer)
    if core:
        before.append(
            lambda: resource.setrlimit(
                resource.RLIMIT_CORE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        )

    def preexec():
        for step in before:
            step()

    started = subprocess.Popen(
        [str(home / "bin" / "java"), "-Xmx64m", str(TARGET)],
        cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        preexec_fn=preexec if before else None,
        # A new session, so the target is not in the probe's process group. It is still
        # the probe's child, which is the honest shape: the thing doing the tracing is a
        # separate java process either way, and being a sibling of it is no help at all.
        start_new_session=True,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        line = started.stdout.readline()
        if not line:
            break
        if line.strip() == "ready":
            return started
    started.kill()
    raise RuntimeError("the target never came up")


def dump(home: pathlib.Path, args: list[str], cwd: pathlib.Path | None = None) -> dict:
    """Run TypeDump one way and turn what happened into a result."""
    done = subprocess.run(
        [str(home / "bin" / "java"), *OPEN, str(TYPE_DUMP), "--types", ",".join(TYPES),
         *args],
        capture_output=True, text=True, timeout=600,
        cwd=str(cwd) if cwd else None,
    )
    if done.returncode != 0:
        return {"worked": False, "error": first_line(done.stderr or done.stdout)}
    try:
        return {"worked": True, "types": json.loads(done.stdout)}
    except json.JSONDecodeError:
        return {"worked": False, "error": "the dump was not JSON: " + done.stdout[:200]}


def route_attach_running(home: pathlib.Path, scratch: pathlib.Path) -> dict:
    """The way a person would try first, and the way #33 found is refused."""
    target = start_target(home, scratch)
    try:
        return dump(home, ["--pid", str(target.pid)])
    finally:
        target.kill()


def route_attach_spawned(home: pathlib.Path, scratch: pathlib.Path) -> dict:
    """The tool starts the JVM itself, so the target is a child of the tracer.

    ptrace_scope 1 permits tracing a descendant, so this is the route that would let a
    generator work with no privileges and no cooperation from the target. It is also the
    shape `bpc` would want anyway: one command that starts a VM, reads it and stops it.
    """
    return dump(home, ["--spawn", str(home / "bin" / "java"), "-Xmx64m", str(TARGET)],
                cwd=scratch)


def route_attach_ptracer_any(home: pathlib.Path, scratch: pathlib.Path) -> dict:
    if platform.system() != "Linux":
        return {"worked": False, "error": "prctl(PR_SET_PTRACER) is Linux only"}
    target = start_target(home, scratch, ptracer_any=True)
    try:
        return dump(home, ["--pid", str(target.pid)])
    finally:
        target.kill()


def core_destination() -> str:
    """Where this machine puts a core, in the machine's own words."""
    pattern = pathlib.Path("/proc/sys/kernel/core_pattern")
    if pattern.exists():
        return pattern.read_text().strip()
    if platform.system() == "Darwin":
        out = subprocess.run(["sysctl", "-n", "kern.corefile"], capture_output=True,
                             text=True)
        return out.stdout.strip() or "/cores/core.%P"
    return "unknown"


def route_core_self(home: pathlib.Path, scratch: pathlib.Path) -> dict:
    """No attach at all: the target dumps itself and the SA reads the file.

    Sending a signal to a process of your own uid needs no permission anybody has to grant,
    so if this works it works everywhere, including in a container with no capabilities and
    on a machine where ptrace is switched off entirely.
    """
    where = core_destination()
    if where.startswith("|"):
        return {"worked": False, "error": f"core_pattern pipes to {where[1:80]}"}

    room = shutil.disk_usage(scratch).free
    if room < 2 * 1024 * 1024 * 1024:
        return {"worked": False, "error": f"only {room // (1024 * 1024)}MB of scratch space"}

    target = start_target(home, scratch, core=True)
    os.kill(target.pid, signal.SIGABRT)
    try:
        target.wait(timeout=300)
    except subprocess.TimeoutExpired:
        target.kill()
        return {"worked": False, "error": "the target did not die on SIGABRT"}

    core = find_core(scratch, target.pid, where)
    if core is None:
        # Say enough that nobody has to wonder whether the probe forgot to raise the
        # limit. It raised it, the target really did die, and the file is still not there.
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        into = pathlib.Path(where).parent if where.startswith("/") else scratch
        return {"worked": False, "error": (
            f"no core appeared. core goes to {where}, the target's limit was raised, "
            f"this process sees {soft}/{hard}, {into} "
            f"{'is' if os.access(into, os.W_OK) else 'is not'} writable")}
    result = dump(home, ["--exe", str(home / "bin" / "java"), "--core", str(core)],
                  cwd=scratch)
    result["core_bytes"] = core.stat().st_size
    core.unlink(missing_ok=True)
    return result


def find_core(scratch: pathlib.Path, pid: int, pattern: str) -> pathlib.Path | None:
    """Look where this machine said it would put the core, then look around it."""
    named = pattern.replace("%p", str(pid)).replace("%P", str(pid))
    for candidate in [pathlib.Path(named), scratch / named, scratch / "core",
                      scratch / f"core.{pid}", pathlib.Path(f"/cores/core.{pid}")]:
        if candidate.is_file():
            return candidate
    found = sorted(scratch.glob("core*")) + sorted(pathlib.Path("/cores").glob(f"*{pid}*"))
    return found[0] if found else None


def route_jhsdb_jstack(home: pathlib.Path, scratch: pathlib.Path) -> dict:
    """The control. #33 measured this one, so the two probes can be compared."""
    jhsdb = home / "bin" / "jhsdb"
    if not jhsdb.exists():
        return {"worked": False, "error": "no jhsdb in this JDK"}
    target = start_target(home, scratch)
    try:
        done = subprocess.run([str(jhsdb), "jstack", "--pid", str(target.pid)],
                              capture_output=True, text=True, timeout=300)
        if done.returncode != 0 or "main" not in done.stdout:
            return {"worked": False, "error": first_line(done.stderr or done.stdout)}
        return {"worked": True}
    finally:
        target.kill()


ROUTES = {
    "attach_running": route_attach_running,
    "attach_spawned": route_attach_spawned,
    "attach_ptracer_any": route_attach_ptracer_any,
    "core_self": route_core_self,
    "jhsdb_jstack": route_jhsdb_jstack,
}


def clhsdb(home: pathlib.Path, args: list[str], cwd: pathlib.Path) -> dict | None:
    """The same four types as `jhsdb clhsdb` prints them, parsed back into the same shape.

    This is the threshold on the issue: the type database read as data has to agree with
    the tool a person would check it with by hand. `type X` prints name, super, three
    booleans and the size. `field X` prints one line per field with its name, its type,
    whether it is static and its offset.
    """
    script = "".join(f"type {name}\nfield {name}\n" for name in TYPES) + "quit\n"
    done = subprocess.run([str(home / "bin" / "jhsdb"), "clhsdb", *args],
                          input=script, capture_output=True, text=True, timeout=600,
                          cwd=str(cwd))
    if done.returncode != 0:
        return None
    types: dict[str, dict] = {}
    for line in done.stdout.splitlines():
        line = line.replace("hsdb> ", "").strip()
        parts = line.split()
        if len(parts) == 7 and parts[0] == "type":
            _, name, super_, _is_oop, _is_int, _is_unsigned, size = parts
            types.setdefault(name, {"fields": []})
            types[name]["size"] = int(size)
            types[name]["super"] = None if super_ == "null" else super_
        # Seven columns, the last of which is the value the field holds right now:
        #   field oopDesc _mark markWord false 0 0x0
        # A static field prints its address there and 0 in the offset column, which is why
        # the offset is dropped rather than believed:
        #   field Universe _collectedHeap CollectedHeap* true 0 0x00007d0e1aba4868
        # The first version of this accepted six columns only, so every field line was
        # skipped, every type came back with no fields at all, and the comparison against
        # the API reported a disagreement that was entirely this parser's.
        elif len(parts) in (6, 7) and parts[0] == "field":
            _, owner, field, kind, static, offset = parts[:6]
            types.setdefault(owner, {"fields": []})
            types[owner]["fields"].append({
                "name": field, "type": kind, "static": static == "true",
                "offset": None if static == "true" else int(offset),
            })
    return types or None


def same(left: dict | None, right: dict | None) -> bool:
    """Two type dumps say the same thing, whatever order the fields came out in."""
    if left is None or right is None:
        return False
    if set(left) != set(right):
        return False
    for name in left:
        one, other = left[name], right[name]
        if one is None or other is None:
            if one != other:
                return False
            continue
        if one.get("size") != other.get("size") or one.get("super") != other.get("super"):
            return False
        key = lambda f: (f["name"], f["type"], f["static"], f["offset"])
        if sorted(map(key, one["fields"])) != sorted(map(key, other["fields"])):
            return False
    return True


def environment(home: pathlib.Path) -> dict:
    scope = pathlib.Path("/proc/sys/kernel/yama/ptrace_scope")
    return {
        "probe": "sa-types",
        "measured": datetime.date.today().isoformat(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "java_build": java_build(home),
        "root": os.geteuid() == 0 if hasattr(os, "geteuid") else None,
        "ptrace_scope": scope.read_text().strip() if scope.exists() else None,
        "core_pattern": core_destination(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--skip", default="", help="routes to leave out, comma separated")
    args = ap.parse_args(argv)

    home = java_home()
    found = environment(home)
    print(f"asking {home}", file=sys.stderr)
    if found["root"]:
        print("running as root, which measures the easy case", file=sys.stderr)

    skip = {name for name in args.skip.split(",") if name}
    routes: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as raw:
        scratch = pathlib.Path(raw)
        for name, run in ROUTES.items():
            if name in skip:
                routes[name] = {"worked": False, "error": "skipped"}
                continue
            print(f"  {name}", file=sys.stderr, flush=True)
            try:
                routes[name] = run(home, scratch)
            except Exception as trouble:  # a route that blows up is a result
                routes[name] = {"worked": False, "error": f"{type(trouble).__name__}: {trouble}"}
            print(f"    {'yes' if routes[name]['worked'] else routes[name].get('error', 'no')}",
                  file=sys.stderr, flush=True)

        # The types, from whichever route opened first, and then every other route's
        # answer checked against it. Two routes disagreeing would matter more than either
        # of them working.
        opened = [name for name, r in routes.items() if r.get("types")]
        found["types_from"] = opened[0] if opened else None
        found["types"] = routes[opened[0]]["types"] if opened else None
        found["routes_agree"] = all(
            same(routes[name]["types"], found["types"]) for name in opened
        ) if opened else None

        by_hand = None
        if found["types"]:
            print("  clhsdb", file=sys.stderr, flush=True)
            by_hand = clhsdb_for(home, routes, scratch)
        found["clhsdb_agrees"] = same(by_hand, found["types"]) if by_hand else None

    for route in routes.values():
        route.pop("types", None)
    found["routes"] = routes

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


def clhsdb_for(home: pathlib.Path, routes: dict, scratch: pathlib.Path) -> dict | None:
    """Get clhsdb in by whatever this machine allows.

    Not the same route the dump used, because clhsdb cannot start a JVM for itself and
    `attach_spawned` is exactly the route that depends on doing so. What is being checked
    here is the type database against the tool a person would use, so any door into the
    same VM will do.
    """
    if routes.get("core_self", {}).get("worked"):
        # The core from that route is gone by now, so make another one the same way.
        target = start_target(home, scratch, core=True)
        os.kill(target.pid, signal.SIGABRT)
        target.wait(timeout=300)
        core = find_core(scratch, target.pid, core_destination())
        if core is None:
            return None
        try:
            return clhsdb(home, ["--core", str(core), "--exe", str(home / "bin" / "java")],
                          scratch)
        finally:
            core.unlink(missing_ok=True)

    for route, opt_in in (("attach_ptracer_any", True), ("attach_running", False)):
        if not routes.get(route, {}).get("worked"):
            continue
        target = start_target(home, scratch, ptracer_any=opt_in)
        try:
            return clhsdb(home, ["--pid", str(target.pid)], scratch)
        finally:
            target.kill()
    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
