#!/usr/bin/env python3
"""Turn the hsdis probe into one page: what a disassembler costs, three ways.

`probes/hsdis/run.py` builds hsdis against the pinned JDK source once per backend and
writes what happened to `probes/hsdis/results/<name>.json`. This puts those files side by
side: what a reader gets with no backend at all, what each backend cost to build, where
the VM will load it from, what it drags in, and what it prints.

  python tools/gen_hsdis.py           regenerate the page
  python tools/gen_hsdis.py --check   regenerate in memory and fail on a diff

The licence columns are the package's own words, read out of its copyright file by the
probe. Where a copyright file names no licence in a parseable form, this says so rather
than filling the cell in from what everybody knows binutils to be. A licence table that
guesses is worse than no licence table.

The output goes to docs/generated/, which is marked linguist-generated, because it is
derived from the results files and editing it by hand would be editing a measurement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

RESULTS = pathlib.Path("probes/hsdis/results")
PAGE = pathlib.Path("docs/generated/hsdis.md")

# The order the backends are printed in, which is increasing size of what they bring with
# them rather than alphabetical. A backend measured but not named here is printed after
# these rather than dropped.
ORDER = ["capstone", "llvm", "binutils"]

# The two flags the probe runs, and what a lesson would be asking each one for.
FLAGS = [
    ("print_assembly", "`-XX:CompileCommand=print,Bench::sum`, one method disassembled"),
    ("print_opto_assembly", "`-XX:+PrintOptoAssembly`, C2's own printer"),
]

# The places a person would put the library, in the order they would try them.
PLACES = [
    ("beside_libjvm", "next to `libjvm.so`, inside the JDK"),
    ("jdk_lib", "in the JDK's `lib` directory"),
    ("ld_library_path", "anywhere, on `LD_LIBRARY_PATH`, JDK untouched"),
]


def load() -> dict[str, dict]:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}, run probes/hsdis/run.py first")
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}


def cell(text: str) -> str:
    """One table cell. A pipe in a licence name or a path would become a column."""
    return text.replace("|", "\\|")


def yes_no(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "not asked"


def backends(data: dict) -> list[str]:
    measured = data.get("backends", {})
    return [b for b in ORDER if b in measured] + [b for b in measured if b not in ORDER]


def licence(package: dict | None) -> str:
    """What the package's copyright file declares, in the page's words, or that it does not.

    Debian's machine readable format names a licence per file group and a package can
    name a dozen, so the first three are printed and the rest are counted. An older free
    form file names none of them in a parseable way, and the only thing this can honestly
    print for one of those is the version of the GPL its text offers, if it offers one.
    """
    if package is None:
        return "no package owns it"
    if not package.get("installed"):
        return "not installed"
    found = package.get("copyright", {})
    if not found.get("present"):
        return "no copyright file"
    names = found.get("names", [])
    if names:
        shown = ", ".join(f"`{trim(n)}`" for n in names[:3])
        rest = len(names) - 3
        return shown + (f", and {rest} more" if rest > 0 else "")
    versions = found.get("gpl_versions", [])
    if versions:
        offered = " and version ".join(f"{v} or later" for v in versions)
        return f"free form file, offering GPL version {offered}"
    return "free form file, no licence named in a parseable form"


def trim(name: str, width: int = 44) -> str:
    """A licence name long enough to be a sentence, cut to something a column can hold."""
    return name if len(name) <= width else name[: width - 1].rstrip() + "…"


def seconds(step: dict | None) -> str:
    if not step:
        return "not reached"
    if not step.get("ok"):
        return "failed"
    return f"{step['seconds']:.0f}s"


def pins(results: dict[str, dict]) -> None:
    """Stop if anything was built against source the pin file does not name.

    Every number on this page is about one JDK tag. A backend built against a moved tag
    is a measurement of a different JDK, and averaging that into the page would be the
    quietest possible way to publish a wrong build time next to a right one.
    """
    for name, data in results.items():
        source = data.get("source", {})
        if not source.get("commit_matches_pin"):
            sys.exit(
                f"{name} built against commit {source.get('commit')} at tag "
                f"{source.get('tag')}, which is not the commit docs/pin.json names. "
                f"Re-pin or re-measure, do not regenerate past this."
            )


def build(results: dict[str, dict]) -> str:
    names = list(results)
    pins(results)
    every = sorted({b for data in results.values() for b in backends(data)})

    out: list[str] = []
    out.append("# A disassembler for the JIT lessons, and what each backend costs")
    out.append("")
    out.append(
        "Generated by `tools/gen_hsdis.py` from the files in `probes/hsdis/results/`. "
        "Do not edit it, edit the measurement. The report that explains it is "
        "[docs/probes/hsdis.md](../probes/hsdis.md)."
    )
    out.append("")
    out.append(
        f"{len(every)} hsdis backends built from the pinned JDK source on "
        f"{len(names)} environment{'s' if len(names) != 1 else ''}, and then asked to "
        f"disassemble one C2 compiled method."
    )
    out.append("")

    out.append("## Where it ran")
    out.append("")
    out.append("| name | platform | os | image | compiler | boot jdk | jdk source | measured |")
    out.append("|---|---|---|---|---|---|---|---|")
    for name in names:
        data = results[name]
        source = data.get("source", {})
        out.append(
            f"| `{name}` | {data['platform']} | {cell(data['os'])} | "
            f"`{cell(str(data.get('image', 'not recorded'))[:24])}` | gcc "
            f"{data.get('compiler', 'unknown')} | {data.get('boot_jdk', 'unknown')} | "
            f"`{source.get('tag', 'unknown')}` at `{str(source.get('commit'))[:12]}` | "
            f"{data['measured'][:10]} |"
        )
    out.append("")

    out.append("## What a reader gets today, with no backend")
    out.append("")
    out.append(
        "The two flags on an untouched pinned JDK. `disassembles` is the whole question: "
        "the VM did not complain about a missing backend, did not fall back to a hex "
        "`[MachCode]` dump, and printed lines that begin with an address."
    )
    out.append("")
    out.append(
        "| environment | flag | complained | `[MachCode]` sections | address lines | "
        "nmethod banners | other lines | disassembles |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for name in names:
        for flag, blurb in FLAGS:
            seen = results[name].get("without_a_backend", {}).get(flag)
            if not seen:
                continue
            out.append(
                f"| `{name}` | {blurb} | {yes_no(seen['complained'])} | "
                f"{seen['machcode_sections']} | {seen['address_lines']} | "
                f"{seen.get('banners', 0)} | {seen.get('body_lines', 0)} | "
                f"{yes_no(seen['worked'])} |"
            )
    out.append("")

    out.append("## What each backend cost")
    out.append("")
    out.append(
        "Wall clock on the machine in the first table, which had "
        f"{results[names[0]].get('cpus', 'some')} cores. `configure` is one JDK "
        "configuration and `build` is `make build-hsdis`, which builds the library and "
        "nothing else."
    )
    out.append("")
    out.append(
        "| environment | backend | configure | build | artifact | size | linked libraries "
        "| disassembles |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for name in names:
        data = results[name]
        for backend in backends(data):
            found = data["backends"][backend]
            made = found.get("artifact")
            printed = found.get("with_backend", {}).get("print_assembly", {})
            out.append(
                f"| `{name}` | `{backend}` | {seconds(found.get('configure'))} | "
                f"{seconds(found.get('build'))} | "
                f"{'`' + made['name'] + '`' if made else 'none'} | "
                f"{str(made['bytes'] // 1024) + ' KiB' if made else '-'} | "
                f"{len(made['links']) if made else '-'} | "
                f"{yes_no(printed.get('worked')) if made else 'not reached'} |"
            )
    out.append("")

    out.append("## Where the VM will load it from")
    out.append("")
    out.append(
        "The same library, tried from three places, with the JDK otherwise untouched. "
        "This is what decides whether a reader needs write access to their JDK."
    )
    out.append("")
    header = "| place | " + " | ".join(
        f"`{n}` `{b}`" for n in names for b in backends(results[n])
    ) + " |"
    out.append(header)
    out.append("|---|" + "---|" * sum(len(backends(results[n])) for n in names))
    for place, blurb in PLACES:
        cells = " | ".join(
            yes_no(results[n]["backends"][b].get("loads_from", {}).get(place))
            for n in names for b in backends(results[n])
        )
        out.append(f"| {blurb} | {cells} |")
    out.append("")

    out.append("## What each backend brings with it")
    out.append("")
    out.append(
        "Every shared library the built hsdis links against, which package on the "
        "measured machine owns that library, and what that package's own copyright file "
        "declares. This is the distribution question in a table, and the packages that "
        "are the C and C++ runtime are left in it rather than filtered out, because "
        "which of these lines are unremarkable is a judgement a reader can make and a "
        "generator should not."
    )
    out.append("")
    for name in names:
        data = results[name]
        for backend in backends(data):
            made = data["backends"][backend].get("artifact")
            if not made:
                continue
            out.append(f"### {backend} on {name}")
            out.append("")
            out.append(
                f"`{made['name']}`, {made['bytes']} bytes, sha256 "
                f"`{made['sha256'][:16]}`, linking {len(made['links'])} libraries from "
                f"{len(made.get('link_packages', []))} packages."
            )
            out.append("")
            out.append("| library | package | version | declared licence |")
            out.append("|---|---|---|---|")
            owners = made.get("link_owners", {})
            for library in made["links"]:
                package = owners.get(library)
                held = data.get("packages", {}).get(package or "", None)
                out.append(
                    f"| `{library}` | {'`' + package + '`' if package else 'unknown'} | "
                    f"{held.get('version', 'unknown') if held else 'unknown'} | "
                    f"{cell(licence(held))} |"
                )
            out.append("")

    out.append("## What they print")
    out.append("")
    out.append(
        "The first lines of the same compiled method, from each backend. The addresses "
        "differ between runs and the syntax differs between backends, which is the "
        "reason this is here rather than a single example. Tabs are printed as four "
        "spaces, which is the only edit made to these lines."
    )
    out.append("")
    for name in names:
        data = results[name]
        for backend in backends(data):
            sample = data["backends"][backend].get(
                "with_backend", {}).get("print_assembly", {}).get("sample", [])
            if not sample:
                continue
            out.append(f"### {backend} on {name}")
            out.append("")
            out.append("```")
            out.extend(line.replace("\t", "    ").rstrip() for line in sample[:6])
            out.append("```")
            out.append("")

    out.append("## What hsdis itself is licensed under")
    out.append("")
    for name in names:
        found = results[name].get("hsdis", {})
        licence_file = found.get("license_file")
        if licence_file:
            out.append(
                f"`{licence_file['path']}` in the JDK source names "
                + ", ".join(f"{n}" for n in licence_file["names"])
                + f", sha256 `{licence_file['sha256'][:16]}`."
            )
            out.append("")
        note = found.get("distribution_note")
        if note:
            out.append(
                "The JDK's own README says this about shipping a build of it, at "
                "src/utils/hsdis/README.md:73@jdk-27+35."
            )
            out.append("")
            out.append(f"> {note}")
            out.append("")
        break

    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = ap.parse_args(argv)

    wanted = build(load())
    if args.check:
        if not PAGE.is_file():
            print(f"{PAGE} is missing, run tools/gen_hsdis.py", file=sys.stderr)
            return 1
        if PAGE.read_text(encoding="utf-8") != wanted:
            print(f"{PAGE} does not match {RESULTS}, run tools/gen_hsdis.py",
                  file=sys.stderr)
            return 1
        print(f"the hsdis page matches {RESULTS}")
        return 0

    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(wanted, encoding="utf-8")
    print(f"wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
