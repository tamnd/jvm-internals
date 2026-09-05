#!/usr/bin/env python3
"""Can this project have a disassembler, and what does each one cost to have.

Issue #8. `-XX:+PrintAssembly` is accepted by every JDK on every platform this project
has measured, prints an nmethod header for every compile, and then says `Loading hsdis
library failed` and prints no instructions, because no GA build ships a disassembler
backend. A lesson family about what the JIT emits needs one. So this builds hsdis three
ways against the pinned JDK source, and for each backend records what it took to build,
what the result links against, whether it actually disassembles anything, and what the
licence of the thing it links against permits.

The three backends are the ones `src/utils/hsdis/README.md` documents at the pinned tag:
capstone, llvm and binutils. They are not equivalent. Capstone and LLVM are permissively
licensed libraries this project could plausibly ship. binutils is GPL, and the JDK's own
README says in as many words that a binutils build may not be distributable. That is the
whole reason the probe records linkage and licences rather than only a yes.

It runs in the container `probes/hsdis/Dockerfile` describes, because a JDK build
environment is a dozen development packages and the devcontainer question on issue #8 is
this same question in a different hat.

  docker build -t jvx-hsdis probes/hsdis
  docker run --rm -v "$PWD:/repo:ro" -v /tmp/hsdis:/out jvx-hsdis \\
      python3 /repo/probes/hsdis/run.py --out /out/linux-x64.json

It clones the JDK source at the pinned tag, which is about a gigabyte and the slowest
thing here, and then builds each backend in its own configuration. Give it half an hour
and ten gigabytes of scratch. Nothing it measures is written back into the repository
checkout, which is mounted read only for exactly that reason.
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
import time

REPO = pathlib.Path(os.environ.get("JVX_REPO", "/repo"))
WORK = pathlib.Path(os.environ.get("JVX_WORK", "/work"))

# The backends the JDK documents, and how configure is told to find each one. `system`
# for binutils is the Linux only spelling that uses the distribution's binutils-dev
# rather than building binutils from source, which is the form anybody would reach for
# and the form with the licence question attached.
BACKENDS = {
    "capstone": ["--with-hsdis=capstone"],
    "llvm": ["--with-hsdis=llvm"],
    "binutils": ["--with-hsdis=binutils", "--with-binutils=system"],
}

# What each backend arrives as on this distribution. The probe reads the version and the
# licence out of the installed package rather than stating them, because a licence this
# project believes in is worth less than the one the package declares.
PACKAGES = {
    "capstone": ["libcapstone-dev", "libcapstone4"],
    "llvm": ["llvm-dev", "llvm"],
    "binutils": ["binutils-dev", "binutils"],
}

# A method to compile and print. Small enough that the disassembly of it is short, hot
# enough that C2 gets to it under -Xbatch.
BENCH = """
public class Bench {
    static int sum(int[] xs) {
        int total = 0;
        for (int x : xs) total += x;
        return total;
    }

    public static void main(String[] args) {
        int[] xs = new int[1024];
        for (int i = 0; i < xs.length; i++) xs[i] = i;
        long seen = 0;
        for (int i = 0; i < 20_000; i++) seen += sum(xs);
        System.out.println(seen);
    }
}
"""

# `print` on one method rather than `-XX:+PrintAssembly`, which prints every compile the
# VM makes and buries the one the probe asked about in forty thousand lines.
PRINT_ASSEMBLY = [
    "-XX:+UnlockDiagnosticVMOptions", "-XX:CompileCommand=print,Bench::sum",
    "-XX:-TieredCompilation", "-Xbatch",
]

# The fallback named in the issue. C2's own printer, which needs nothing external.
PRINT_OPTO = [
    "-XX:+UnlockDiagnosticVMOptions", "-XX:+PrintOptoAssembly",
    "-XX:-TieredCompilation", "-Xbatch",
]

# What HotSpot says when it looked for a backend and found none. The string is the whole
# reason this probe exists, so it is matched exactly rather than inferred from an absence.
NO_BACKEND = "Loading hsdis library failed"

# With no backend the VM prints the code as hex in a MachCode section and says so. Those
# lines start with an address and a colon exactly like disassembly does, so counting
# address lines proves nothing on its own and the section marker is what settles it.
MACHCODE = "[MachCode]"
ADDRESS_LINE = re.compile(r"^\s*0x[0-9a-f]+:\s+\S")

# The heading C2 prints before each compilation it was asked to print. On a product build
# `-XX:+PrintOptoAssembly` prints these and nothing under them, so counting the headings
# separately from the lines under them is what tells an empty printer from a quiet one.
BANNER = "C2-compiled nmethod"

# A copyright file in Debian's machine readable format names its licences on License:
# lines. An older free form one, and binutils ships one, names none and has to be read.
LICENCE_LINE = re.compile(r"^License:\s*(\S.*)$", re.M)
GPL_VERSION = re.compile(r"General Public License.{0,200}?either version (\d)", re.S)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def timed(command: list[str], **kwargs) -> dict:
    """Run something slow and record how it went, in the shape the results file wants."""
    started = time.time()
    done = run(command, **kwargs)
    result = {"ok": done.returncode == 0, "seconds": round(time.time() - started, 1)}
    if done.returncode != 0:
        result["error"] = tail(done.stderr or done.stdout)
    return result


def tail(text: str, lines: int = 12) -> str:
    """The end of a build log, which is where the reason is."""
    kept = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])[:2000]


def pin() -> dict:
    return json.loads((REPO / "docs" / "pin.json").read_text(encoding="utf-8"))


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def boot_jdk() -> pathlib.Path:
    """The pinned JDK, installed by the repository's own installer so it is the pinned one."""
    home = WORK / "jdk"
    done = run([sys.executable, str(REPO / "tools" / "fetch_jdk.py"), "--dir", str(home)])
    if done.returncode != 0:
        sys.exit(f"could not install the pinned JDK: {tail(done.stderr or done.stdout)}")
    return pathlib.Path(done.stdout.strip().splitlines()[-1])


def java_build(home: pathlib.Path) -> str:
    out = run([str(home / "bin" / "java"), "-XshowSettings:properties", "-version"])
    found = re.search(r"java\.runtime\.version = (\S+)", out.stdout + out.stderr)
    return found.group(1) if found else "unknown"


def source(tag: str, expected_commit: str) -> dict:
    """Clone the JDK at the pinned tag, and check it is the commit the pin file names."""
    into = WORK / "jdk-src"
    result: dict = {"tag": tag, "path": str(into)}
    if not (into / ".git").is_dir():
        clone = timed([
            "git", "clone", "--depth", "1", "--branch", tag,
            "https://github.com/openjdk/jdk.git", str(into),
        ])
        result.update({"cloned": clone})
        if not clone["ok"]:
            return result
    else:
        result["cloned"] = {"ok": True, "seconds": 0, "note": "already there"}
    head = run(["git", "-C", str(into), "rev-parse", "HEAD"]).stdout.strip()
    result["commit"] = head
    # A tag can be moved. The pin file records the commit the tag resolved to when it was
    # pinned, so a build against a different commit is a different measurement.
    result["commit_matches_pin"] = head == expected_commit
    return result


def packages(extra: list[str]) -> dict:
    """Version and declared licence of the packages that build a backend, and of the ones
    the built libraries turned out to link against, which are not the same list.

    `-dev` packages are what configure needs and the runtime library is what the artifact
    loads, and on this distribution they are separate packages with separate copyright
    files. The second list is the one the distribution question is about, so it is read
    off the artifacts rather than declared here.
    """
    found: dict[str, dict] = {}
    for name in [n for names in PACKAGES.values() for n in names] + extra:
        if name in found:
            continue
        version = run(["dpkg-query", "-W", "-f=${Version}", name])
        if version.returncode != 0:
            found[name] = {"installed": False}
            continue
        found[name] = {"installed": True, "version": version.stdout.strip(),
                       "copyright": copyright_of(name)}
    return found


def copyright_of(package: str) -> dict:
    """What the package's own copyright file says, without deciding what it means.

    Two shapes turn up. Debian's machine readable format names a licence per file group,
    so a package can name several and all of them are kept. An older free form file, and
    binutils ships one, names none of them in a parseable way and says the version in a
    sentence instead. Both are recorded, along with the digest of the file, because the
    thing a lesson author needs is the file rather than this probe's reading of it.
    """
    path = pathlib.Path("/usr/share/doc") / package / "copyright"
    if not path.is_file():
        return {"present": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    names: list[str] = []
    for name in LICENCE_LINE.findall(text):
        if name.strip() not in names:
            names.append(name.strip())
    return {
        "present": True,
        "path": str(path),
        "sha256": sha256(path),
        "machine_readable": text.startswith("Format:"),
        "names": names,
        # The version a free form GPL notice offers, which is the difference between a
        # licence this project could ship under and one it could not.
        "gpl_versions": sorted(set(GPL_VERSION.findall(text))),
    }


def owner(path: str) -> str | None:
    """Which package a file on this machine came from, or None if no package claims it."""
    found = run(["dpkg", "-S", str(pathlib.Path(path).resolve())])
    if found.returncode != 0 or ":" not in found.stdout:
        return None
    return found.stdout.split(":", 1)[0].strip()


def configure(src: pathlib.Path, name: str, flags: list[str], home: pathlib.Path) -> dict:
    return timed(
        ["bash", "configure", f"--with-conf-name={name}", f"--with-boot-jdk={home}",
         "--disable-warnings-as-errors", *flags],
        cwd=str(src),
    )


def artifact(src: pathlib.Path, name: str) -> dict | None:
    """The library the build produced, whatever this platform decided to call it."""
    built = sorted((src / "build" / name / "support" / "hsdis").glob("hsdis-*"))
    libraries = [p for p in built if p.suffix in (".so", ".dylib", ".dll")]
    if not libraries:
        return None
    library = libraries[0]
    # What it links against is the licence question in one command: a backend compiled in
    # is a different distribution problem from one loaded at run time. The path each
    # soname resolved to is what says which package it came from, so both are kept.
    resolved: dict[str, str] = {}
    for line in run(["ldd", str(library)]).stdout.splitlines():
        if "=>" not in line:
            continue
        soname, _, rest = line.strip().partition("=>")
        target = rest.strip().split(" ")[0]
        resolved[soname.strip()] = target
    owners = {name: owner(path) for name, path in resolved.items()
              if path.startswith("/")}
    return {
        "name": library.name,
        "bytes": library.stat().st_size,
        "sha256": sha256(library),
        "links": sorted(resolved),
        # Which package each of them came from, asked of the package manager rather than
        # guessed from the name, because a page that joins a library to a licence by
        # spelling would eventually attach the wrong licence to something.
        "link_owners": {k: v for k, v in sorted(owners.items()) if v},
        "link_packages": sorted({v for v in owners.values() if v}),
        "path": str(library),
    }


def observe(home: pathlib.Path, bench: pathlib.Path, flags: list[str],
            env: dict | None = None) -> dict:
    """Compile the workload one way and record what the VM printed about the code.

    `worked` means the VM disassembled: it did not complain about a missing backend, and
    it did not fall back to printing hex in a MachCode section. Both halves are recorded
    next to it, because the counts are what somebody would check this judgement against.

    `banners` and `body_lines` are here for the other flag. A printer that has nothing to
    print still prints its heading, so a run that is all heading and no body is a
    measurable thing rather than an absence somebody has to take on trust.
    """
    done = run([str(home / "bin" / "java"), *flags, str(bench)],
               cwd=str(bench.parent), env=env)
    text = done.stdout + done.stderr
    complained = NO_BACKEND in text
    machcode = text.count(MACHCODE)
    lines = text.splitlines()
    addresses = [line.rstrip() for line in lines if ADDRESS_LINE.match(line)]
    banners = [line for line in lines if BANNER in line]
    return {
        "worked": done.returncode == 0 and not complained and machcode == 0
        and bool(addresses),
        "exit": done.returncode,
        "complained": complained,
        "machcode_sections": machcode,
        "address_lines": len(addresses),
        "banners": len(banners),
        "body_lines": len([line for line in lines if line.strip() and BANNER not in line]),
        "total_lines": len(lines),
        "sample": addresses[:8],
    }


def placements(home: pathlib.Path, library: pathlib.Path, bench: pathlib.Path) -> dict:
    """Where the VM will accept the library from, asked rather than read off a wiki.

    Three places a person would try: next to libjvm.so, in the JDK's lib directory, and
    on LD_LIBRARY_PATH with the JDK untouched. Which of them work decides whether a
    reader can use a disassembler without write access to their JDK.
    """
    found: dict[str, bool] = {}
    for name, target in {
        "beside_libjvm": home / "lib" / "server" / library.name,
        "jdk_lib": home / "lib" / library.name,
    }.items():
        shutil.copy2(library, target)
        try:
            found[name] = observe(home, bench, PRINT_ASSEMBLY)["worked"]
        finally:
            target.unlink(missing_ok=True)

    loose = WORK / "loose"
    loose.mkdir(exist_ok=True)
    shutil.copy2(library, loose / library.name)
    found["ld_library_path"] = observe(
        home, bench, PRINT_ASSEMBLY, env=dict(os.environ, LD_LIBRARY_PATH=str(loose))
    )["worked"]
    shutil.rmtree(loose, ignore_errors=True)
    return found


def hsdis_licence(src: pathlib.Path) -> dict:
    """What the JDK's own hsdis directory says about hsdis itself, and about shipping it."""
    licence = src / "src" / "utils" / "hsdis" / "hsdis-license.txt"
    readme = src / "src" / "utils" / "hsdis" / "README.md"
    found: dict = {}
    if licence.is_file():
        first = licence.read_text(encoding="utf-8", errors="replace").splitlines()
        found["license_file"] = {
            "path": "src/utils/hsdis/hsdis-license.txt",
            "sha256": sha256(licence),
            "names": [line.strip() for line in first if "License" in line][:3],
        }
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        warning = re.search(r"\*\*NOTE:\*\*(.+?)\n\n", text, re.S)
        found["distribution_note"] = (
            " ".join(warning.group(1).split()) if warning else "not found in the README"
        )
    return found


def environment(home: pathlib.Path) -> dict:
    release = {}
    for line in pathlib.Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    return {
        "probe": "hsdis",
        "measured": datetime.date.today().isoformat(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "os": release.get("PRETTY_NAME", "unknown"),
        "in_container": pathlib.Path("/.dockerenv").exists(),
        "image": os.environ.get("JVX_IMAGE", "not recorded"),
        "compiler": run(["gcc", "-dumpfullversion"]).stdout.strip(),
        "cpus": os.cpu_count(),
        "boot_jdk": java_build(home),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--only", default="", help="backends to try, comma separated")
    args = ap.parse_args(argv)

    WORK.mkdir(parents=True, exist_ok=True)
    home = boot_jdk()
    found = environment(home)

    settings = pin()
    print(f"cloning openjdk/jdk at {settings['jdk_tag']}", file=sys.stderr, flush=True)
    found["source"] = source(settings["jdk_tag"], settings["jdk_tag_commit"])
    src = pathlib.Path(found["source"]["path"])
    if not found["source"]["cloned"]["ok"]:
        return write(args.out, found)
    found["hsdis"] = hsdis_licence(src)

    bench = WORK / "Bench.java"
    bench.write_text(BENCH, encoding="utf-8")

    # The JDK the disassembly is tried in is a copy, because a probe that leaves a
    # library inside the pinned JDK has changed the thing every other probe measures.
    trial = WORK / "jdk-trial"
    if not trial.is_dir():
        shutil.copytree(home, trial, symlinks=True)
    # What a reader gets today, on a JDK with no backend in it, both ways: the flag the
    # lessons want and the fallback the issue names.
    found["without_a_backend"] = {
        "print_assembly": observe(trial, bench, PRINT_ASSEMBLY),
        "print_opto_assembly": observe(trial, bench, PRINT_OPTO),
    }

    wanted = [b for b in BACKENDS if not args.only or b in args.only.split(",")]
    found["backends"] = {}
    for name in wanted:
        print(f"  {name}", file=sys.stderr, flush=True)
        result: dict = {"configure": configure(src, name, BACKENDS[name], home)}
        if result["configure"]["ok"]:
            result["build"] = timed(["make", f"CONF={name}", "build-hsdis"], cwd=str(src))
            if result["build"]["ok"]:
                result["artifact"] = artifact(src, name)
                if result["artifact"]:
                    library = pathlib.Path(result["artifact"]["path"])
                    result["loads_from"] = placements(trial, library, bench)
                    installed = trial / "lib" / "server" / library.name
                    shutil.copy2(library, installed)
                    try:
                        # The same two questions as without_a_backend, so the pair can be
                        # read as a before and an after rather than as two measurements.
                        result["with_backend"] = {
                            "print_assembly": observe(trial, bench, PRINT_ASSEMBLY),
                            "print_opto_assembly": observe(trial, bench, PRINT_OPTO),
                        }
                    finally:
                        installed.unlink(missing_ok=True)
        print(f"    {'built' if result.get('artifact') else 'no'}", file=sys.stderr,
              flush=True)
        found["backends"][name] = result

    # Last, because the list of packages worth describing includes the ones the artifacts
    # turned out to link against, which is not knowable until they are built.
    linked = [p for b in found["backends"].values()
              for p in (b.get("artifact") or {}).get("link_packages", [])]
    found["packages"] = packages(sorted(set(linked)))

    return write(args.out, found)


def write(out: pathlib.Path | None, found: dict) -> int:
    text = json.dumps(found, indent=2, sort_keys=True) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
