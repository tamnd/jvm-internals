#!/usr/bin/env python3
"""Ask an environment what it can actually do, and write the answers down.

Issue #11 asks for a capability matrix rather than a list of assertions, on the grounds
that a lesson which declares `env: E0` is making a claim about a machine somebody else
owns. This is the thing that checks the claim. It asks around sixty questions of a JDK
and the box it is sitting on, and writes one JSON file per environment.

The questions are the ones a lesson can be blocked by: is the tool in the image, is the
flag still there, will the VM let you attach to another process, will it let you open
java.base, is there a disassembler behind PrintAssembly. Each answer is a fact with a
date and a platform on it, not an opinion.

  python probes/capability/run.py --out probes/capability/results/osx-arm64.json
  python probes/capability/run.py --print

Nothing here touches the network and nothing needs root. It runs about thirty short JVMs
and takes well under a minute on every machine it has been tried on.
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
CAPABILITY = HERE / "Capability.java"

# Tools a lesson might reach for by name. A jlink'd runtime or a JRE has almost none of
# these, and "jshell is not on this image" is a much better error than whatever the
# reader would otherwise see.
TOOLS = [
    "java", "javac", "jshell", "jar", "javap", "jcmd", "jinfo", "jmap", "jstack",
    "jfr", "jdb", "jhsdb", "jdeps", "jlink", "jpackage", "serialver",
]

# Flags, and what asking for each one is meant to establish. A flag that has been removed
# is the interesting case: HotSpot keeps accepting the argument and prints a warning, so
# a script that only checks the exit status believes the flag worked.
FLAGS = [
    "-XX:+UseCompactObjectHeaders",
    "-XX:+UseSerialGC",
    "-XX:+UseParallelGC",
    "-XX:+UseG1GC",
    "-XX:+UseZGC",
    "-XX:+UseShenandoahGC",
    "-XX:+UseEpsilonGC",
    "-XX:+UseCompressedOops",
    "-XX:+UseCompressedClassPointers",
    "-XX:ObjectAlignmentInBytes=16",
    "-XX:+PrintCompilation",
    "-XX:+LogCompilation",
    "-XX:+PrintInlining",
    "-XX:+PrintFieldLayout",
    "-XX:+PrintAssembly",
    "-XX:+TieredCompilation",
]

# Some of the above are locked behind one of these, and a probe that does not unlock them
# reports "not available" for flags that are available to anybody who reads the message.
UNLOCK = ["-XX:+UnlockDiagnosticVMOptions", "-XX:+UnlockExperimentalVMOptions"]

REMOVED = re.compile(r"Ignoring option (\w+); support was removed in ([\d.]+)")
UNRECOGNISED = re.compile(r"Unrecognized VM option '([^']+)'")


def java_home() -> pathlib.Path:
    home = os.environ.get("JAVA_HOME")
    if home:
        return pathlib.Path(home)
    binary = shutil.which("java")
    if binary is None:
        sys.exit("no JAVA_HOME and no java on PATH, so there is nothing to ask")
    # bin/java, so two parents up. resolve() first because on most Linux distributions
    # /usr/bin/java is a symlink into the actual image and the image is what matters.
    return pathlib.Path(binary).resolve().parent.parent


def tool(home: pathlib.Path, name: str) -> pathlib.Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    return home / "bin" / (name + suffix)


# Every JVM this probe starts runs here unless a check says otherwise. Not tidiness:
# -XX:+LogCompilation writes hotspot_pid<pid>.log into the working directory whatever
# else you asked it for, so a probe that runs where it was invoked leaves droppings in
# somebody's checkout, and the one place this gets run most is a checkout.
SCRATCH: pathlib.Path | None = None


def run(command: list[str], timeout: int = 120, **kwargs) -> subprocess.CompletedProcess:
    if SCRATCH is not None:
        kwargs.setdefault("cwd", SCRATCH)
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, **kwargs
    )


def why(text: str) -> str | None:
    """The one line of a failure that says something, rather than the first line.

    Tools that fail after printing progress put the reason in the middle. Taking the
    first line gets "Attaching to process 17023, please wait..." every time, which is
    the least informative sentence in the output.
    """
    interesting = re.compile(r"error|exception|denied|refused|not supported|failed", re.I)
    for line in reversed(text.strip().splitlines()):
        if interesting.search(line):
            return scrub(line)
    return scrub(text.strip().splitlines()[0]) if text.strip() else None


def scrub(line: str) -> str:
    """Take the process id out of a message before it is written to a committed file.

    A pid is different on every run, so a message with one in it makes the results file
    and everything generated from it change when nothing has changed. The reason the tool
    refused is the part worth keeping.
    """
    return re.sub(r"\b\d{4,}\b", "<pid>", line.strip())[:180]


class Probe:
    def __init__(self, home: pathlib.Path):
        self.home = home
        self.java = str(tool(home, "java"))
        self.answers: dict[str, object] = {}
        self.notes: dict[str, str] = {}

    def record(self, key: str, value: object, note: str | None = None) -> None:
        self.answers[key] = value
        if note:
            self.notes[key] = note

    # The checks.

    def tools(self) -> None:
        for name in TOOLS:
            self.record(f"tool.{name}", tool(self.home, name).is_file())

    def flags(self) -> None:
        for flag in FLAGS:
            done = run([self.java, *UNLOCK, flag, "-version"])
            text = done.stdout + done.stderr
            removed = REMOVED.search(text)
            missing = UNRECOGNISED.search(text)
            name = flag.lstrip("-XX:+-").split("=")[0]
            if removed:
                # The one that matters. The VM starts, the exit status is 0, and the flag
                # did nothing. Anything that trusted the exit status here is wrong.
                self.record(f"flag.{name}", "removed", f"removed in {removed.group(2)}")
            elif missing or done.returncode != 0:
                self.record(f"flag.{name}", False)
            else:
                self.record(f"flag.{name}", True)

    def in_process(self) -> None:
        """Everything Capability.java answers, run through the source launcher."""
        done = run([self.java, str(CAPABILITY)], timeout=300)
        if done.returncode != 0:
            self.record("inprocess.ok", False, done.stderr.strip()[:200])
            return
        self.record("inprocess.ok", True)
        for line in done.stdout.splitlines():
            if "\t" not in line:
                continue
            key, value = line.split("\t", 1)
            if value in ("true", "false"):
                self.record(key, value == "true")
            elif value.lstrip("-").isdigit():
                self.record(key, int(value))
            else:
                self.record(key, value)

    def add_opens(self) -> None:
        """Whether java.base can be opened on the command line, not whether it is open.

        Denied by default since 16, and several lessons need it. The question a lesson
        author has to answer is not "is it open" but "am I allowed to open it here", and
        a locked down runtime can say no to the second one.
        """
        program = (
            "public class Opens { public static void main(String[] a) throws Exception {"
            "  var f = String.class.getDeclaredField(\"value\"); f.setAccessible(true);"
            "  System.out.println(\"opened\"); } }"
        )
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw) / "Opens.java"
            source.write_text(program, encoding="utf-8")
            done = run([
                self.java, "--add-opens", "java.base/java.lang=ALL-UNNAMED", str(source)
            ])
        self.record("host.add_opens", "opened" in done.stdout)

    def source_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw) / "Hello.java"
            source.write_text(
                "public class Hello { public static void main(String[] a) {"
                " System.out.println(\"hello\"); } }",
                encoding="utf-8",
            )
            done = run([self.java, str(source)])
        self.record("host.source_launcher", "hello" in done.stdout)

    def jshell(self) -> None:
        """Both execution providers, because the noise probe showed they are not the same.

        See docs/probes/jshell-noise.md. The default one starts a second JVM and needs a
        socket to itself, which is the part a restricted environment can refuse.
        """
        jshell = tool(self.home, "jshell")
        if not jshell.is_file():
            self.record("host.jshell_default", False)
            self.record("host.jshell_local", False)
            return
        with tempfile.TemporaryDirectory() as raw:
            script = pathlib.Path(raw) / "probe.jsh"
            script.write_text('System.out.println("jshell " + (6 * 7));\n/exit\n', "utf-8")
            for name, extra in [("default", []), ("local", ["--execution", "local"])]:
                started = time.monotonic()
                done = run([str(jshell), "-q", *extra, str(script)], timeout=300)
                took = round(time.monotonic() - started, 1)
                self.record(f"host.jshell_{name}", "jshell 42" in done.stdout)
                self.record(f"host.jshell_{name}_seconds", took)

    def attach(self) -> None:
        """Can one process ask another one a question, which is what every tool does.

        This is the check most likely to come back no. Linux gates it on ptrace, macOS on
        code signing, and a container usually has neither the capability nor a shared
        /tmp for the attach socket. jcmd, jstack, jmap and jhsdb are all this mechanism
        wearing different hats, so one no here is a no for all of them.
        """
        jcmd = tool(self.home, "jcmd")
        if not jcmd.is_file():
            self.record("host.attach_jcmd", False)
            self.record("host.attach_jhsdb", False)
            return
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw) / "Sleeper.java"
            source.write_text(
                "public class Sleeper { public static void main(String[] a)"
                " throws Exception { System.out.println(\"up\"); System.out.flush();"
                " Thread.sleep(120_000); } }",
                encoding="utf-8",
            )
            child = subprocess.Popen(
                [self.java, str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                # Wait for the child to say it is up rather than sleeping a guessed
                # number of seconds. The source launcher has to compile first, and on a
                # slow machine that is several seconds.
                if child.stdout is None or child.stdout.readline().strip() != "up":
                    self.record("host.attach_jcmd", False, "the child never started")
                    self.record("host.attach_jhsdb", False, "the child never started")
                    return
                pid = str(child.pid)
                done = run([str(jcmd), pid, "VM.version"], timeout=120)
                self.record("host.attach_jcmd", "VM version" in done.stdout or "27" in done.stdout)

                jhsdb = tool(self.home, "jhsdb")
                if not jhsdb.is_file():
                    self.record("host.attach_jhsdb", False)
                else:
                    # The Serviceability Agent, which is what bpc needs for #3. It stops
                    # the target and walks its memory, so it needs strictly more
                    # permission than jcmd does and fails separately.
                    done = run([str(jhsdb), "jinfo", "--pid", pid], timeout=180)
                    text = done.stdout + done.stderr
                    worked = done.returncode == 0 and "java.vm.version" in text
                    self.record("host.attach_jhsdb", worked, None if worked else why(text))
            finally:
                child.kill()
                child.wait(timeout=60)

    def javaagent(self) -> None:
        """Build a real agent jar and load it, because instrumentation is an E0 promise.

        Nothing simulates this. An agent needs a manifest with a Premain-Class in it, and
        the failure modes are all in the packaging rather than in the code.
        """
        javac = tool(self.home, "javac")
        jar = tool(self.home, "jar")
        if not (javac.is_file() and jar.is_file()):
            self.record("host.javaagent", False, "no javac or no jar on this image")
            return
        with tempfile.TemporaryDirectory() as raw:
            work = pathlib.Path(raw)
            (work / "Agent.java").write_text(
                "import java.lang.instrument.Instrumentation;\n"
                "public class Agent {\n"
                "  public static void premain(String args, Instrumentation inst) {\n"
                "    System.out.println(\"agent sees \" + (inst != null));\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            (work / "manifest.txt").write_text("Premain-Class: Agent\n", encoding="utf-8")
            (work / "Main.java").write_text(
                "public class Main { public static void main(String[] a) {"
                " System.out.println(\"main ran\"); } }",
                encoding="utf-8",
            )
            compiled = run([str(javac), "Agent.java", "Main.java"], cwd=work)
            if compiled.returncode != 0:
                self.record("host.javaagent", False, compiled.stderr.strip()[:160])
                return
            run([str(jar), "cfm", "agent.jar", "manifest.txt", "Agent.class"], cwd=work)
            done = run(
                [self.java, "-javaagent:agent.jar", "-cp", ".", "Main"], cwd=work
            )
            self.record(
                "host.javaagent",
                "agent sees true" in done.stdout and "main ran" in done.stdout,
            )

    def jvmti(self) -> None:
        """A JVMTI agent library, using the one that ships with every JDK.

        jdwp is a JVMTI agent, so if it loads then the native agent path works, and a
        lesson that writes its own agent has somewhere to stand. Port 0 so the probe
        cannot collide with anything already listening.
        """
        done = run([
            self.java,
            "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:0",
            "-version",
        ])
        text = done.stdout + done.stderr
        self.record("host.jvmti_agent", "Listening for transport" in text)

    def disassembler(self) -> None:
        """Whether PrintAssembly has a disassembler behind it, which is issue #8.

        Without hsdis the VM prints a specific complaint and carries on, so the flag being
        accepted says nothing. The complaint is the answer.
        """
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw) / "Warm.java"
            source.write_text(
                "public class Warm { static long s; public static void main(String[] a) {"
                " for (int i = 0; i < 200000; i++) s += i; System.out.println(s); } }",
                encoding="utf-8",
            )
            done = run([
                self.java, "-XX:+UnlockDiagnosticVMOptions", "-XX:+PrintAssembly",
                str(source),
            ], timeout=300)
        text = done.stdout + done.stderr
        # Three different messages for the same no, depending on version and platform.
        # And "Compiled method" is not evidence of anything: the VM prints the nmethod
        # header whether or not it managed to disassemble the body, which is how a naive
        # version of this check reported a disassembler on a machine with none.
        refused = re.search(
            r"Loading hsdis library failed|PrintAssembly is disabled|Could not load hsdis",
            text,
        )
        if refused:
            self.record("host.hsdis", False, refused.group(0))
        else:
            # An address, a colon and a mnemonic. That is what disassembly looks like and
            # nothing else in the output does.
            self.record("host.hsdis", bool(re.search(r"^\s+0x[0-9a-f]+:\s+\w", text, re.M)))

    def native_tools(self) -> None:
        for name in ["gdb", "lldb", "perf", "git", "python3"]:
            self.record(f"host.{name}", shutil.which(name) is not None)
        # Linux gates ptrace on this, and it is the single most common reason a debugger
        # or the Serviceability Agent cannot attach inside a container.
        scope = pathlib.Path("/proc/sys/kernel/yama/ptrace_scope")
        if scope.is_file():
            self.record("host.ptrace_scope", scope.read_text(encoding="utf-8").strip())
        else:
            self.record("host.ptrace_scope", "not a linux kernel with yama")

    def describe(self) -> dict:
        done = run([self.java, "-version"])
        text = done.stdout + done.stderr
        build = re.search(r"\(build ([^)]+)\)", text)
        # No hostname and no path to the JDK, the same rule the noise probe follows. This
        # file is committed to a public repository, and the platform and the build string
        # are the only parts anybody reproducing it needs.
        return {
            "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
            "java_build": build.group(1) if build else "unknown",
            "cpus": os.cpu_count(),
            # Recorded because it changes the answers rather than as trivia. With
            # ptrace_scope at 1, an ordinary user cannot attach a debugger or the
            # Serviceability Agent to a process they did not start, and root can, so two
            # otherwise identical Linux boxes give different answers to the same check.
            "privileged": os.geteuid() == 0 if hasattr(os, "geteuid") else None,
            "measured": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
        }

    def everything(self) -> dict:
        described = self.describe()
        for step in [
            self.tools, self.flags, self.in_process, self.add_opens,
            self.source_launcher, self.jshell, self.attach, self.javaagent,
            self.jvmti, self.disassembler, self.native_tools,
        ]:
            started = time.monotonic()
            step()
            print(
                f"  {step.__name__:<16} {time.monotonic() - started:5.1f}s",
                file=sys.stderr,
            )
        return {
            **described,
            "checks": len(self.answers),
            "answers": dict(sorted(self.answers.items())),
            "notes": dict(sorted(self.notes.items())),
        }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--print", action="store_true", help="also print every answer")
    args = ap.parse_args(argv)

    home = java_home()
    print(f"asking {home}", file=sys.stderr)
    global SCRATCH
    with tempfile.TemporaryDirectory() as raw:
        SCRATCH = pathlib.Path(raw)
        found = Probe(home).everything()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}, {found['checks']} checks", file=sys.stderr)
    if args.print or not args.out:
        for key, value in found["answers"].items():
            note = found["notes"].get(key)
            print(f"{key:<44} {value}" + (f"   ({note})" if note else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
