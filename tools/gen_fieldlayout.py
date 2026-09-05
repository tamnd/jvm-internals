#!/usr/bin/env python3
"""Turn the JOL comparison into a page, from the measurements and nothing else.

Issue #5. Part IV of the curriculum is built on compact object headers and O01 is the
pilot lesson, so whether JOL reports the real layout decides whether eleven lessons can
use it. `probes/fieldlayout/run.py` answers that by running JOL and the VM's own
`-XX:+PrintFieldLayout` inside one JVM, so the two halves cannot be describing two
different VMs, and writes the answer to `probes/fieldlayout/results/<platform>.json`.

  python tools/gen_fieldlayout.py            rewrite the page
  python tools/gen_fieldlayout.py --check    fail if the page is stale

The table is per configuration rather than per class, because the question is not whether
JOL is right about `Empty`, it is whether JOL is right under compact headers. A per class
table would be four hundred rows of agreement hiding the one disagreement.

That one disagreement gets its own section. It is a field that does not exist in Java,
and a reader who sees `11/12` without being told which one is missing will assume JOL got
an offset wrong, which is not what happened.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = pathlib.Path("probes/fieldlayout/results")
OUTPUT = pathlib.Path("docs/generated/fieldlayout.md")

# The order the configurations are worth reading in, rather than the order a dict gives.
# Default first because it is what a reader has, then the flag the question is about.
ORDER = ["default", "no_compact_headers", "no_compressed_oops", "heap_above_32g"]

WHAT = {
    "default": "what a reader gets",
    "no_compact_headers": "the legacy header, for contrast",
    "no_compressed_oops": "compact header, uncompressed references",
    "heap_above_32g": "a heap large enough to turn compression off",
}

RUNS = {"attach_self": "attach allowed", "no_attach": "attach refused"}


def load() -> dict[str, dict]:
    files = sorted((ROOT / RESULTS).glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}, run probes/fieldlayout/run.py first")
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}


def configurations(result: dict) -> list[str]:
    """Every configuration in the file, the ones this page knows how to order first."""
    found = list(result["configurations"])
    return [name for name in ORDER if name in found] + [
        name for name in found if name not in ORDER]


def agreement(run: dict) -> tuple[int, int, list[str]]:
    classes = run["classes"]
    disagreeing = sorted(name for name, one in classes.items() if not one["agrees"])
    return len(classes) - len(disagreeing), len(classes), disagreeing


def header_sizes(run: dict) -> tuple[set, set]:
    """What JOL said the header is, and what it could not answer for."""
    sizes, silent = set(), set()
    for name, one in run["classes"].items():
        if one.get("jol_answered"):
            sizes.add(one["jol"]["parseClass"]["header_size"])
        else:
            silent.add(name)
    return sizes, silent


def table(platform: str, result: dict) -> list[str]:
    lines = [
        f"### {platform}",
        "",
        f"`{result['environment']['java_build']}`, built from "
        f"`{result['environment']['built_from']['commit'][:12]}`.",
        "",
        "| configuration | UseCompactObjectHeaders | UseCompressedOops | attach | "
        "JOL header | classes agreeing |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in configurations(result):
        config = result["configurations"][name]
        flags = config["flags_reported"]
        for run_name, run in config["runs"].items():
            good, total, _ = agreement(run)
            sizes, silent = header_sizes(run)
            shown = ", ".join(f"{size}" for size in sorted(sizes)) or "no answer"
            if silent:
                shown += f" ({len(silent)} unanswered)"
            lines.append(
                f"| {name} | {flags.get('UseCompactObjectHeaders', '?')} | "
                f"{flags.get('UseCompressedOops', '?')} | "
                f"{RUNS.get(run_name, run_name)} | {shown} | {good} of {total} |")
    lines.append("")
    return lines


def disagreements(result: dict) -> dict[str, list[str]]:
    """Every class that disagreed anywhere, with the reasons, collapsed across runs."""
    found: dict[str, list[str]] = {}
    for config in result["configurations"].values():
        for run in config["runs"].values():
            for name, one in run["classes"].items():
                if one["agrees"]:
                    continue
                for why in one["differences"]:
                    if why not in found.setdefault(name, []):
                        found[name].append(why)
    return found


def offsets(result: dict, cls: str) -> list[str]:
    """The two sides of one class, side by side, from the default configuration."""
    run = result["configurations"]["default"]["runs"]["attach_self"]["classes"][cls]
    names = sorted(set(run["vm"]["fields"]) | set(run["jol"]["parseClass"]["fields"]))
    lines = ["| field | VM says | JOL says |", "| --- | --- | --- |"]
    for name in names:
        vm = run["vm"]["fields"].get(name)
        jol = run["jol"]["parseClass"]["fields"].get(name)
        lines.append(f"| `{name}` | {vm if vm is not None else 'absent'} | "
                     f"{jol if jol is not None else '**absent**'} |")
    lines.append("")
    lines.append(f"Instance size: VM {run['vm']['instance_size']}, "
                 f"JOL {run['jol']['parseClass']['instance_size']}.")
    return lines


def page(results: dict[str, dict]) -> str:
    one = next(iter(results.values()))
    jol = one["jol"]
    total = sum(
        len(run["classes"])
        for result in results.values()
        for config in result["configurations"].values()
        for run in config["runs"].values())
    everywhere = all(result["agrees_everywhere"] for result in results.values())

    lines = [
        "# Does JOL report the real object layout",
        "",
        "<!-- Generated by tools/gen_fieldlayout.py from "
        "probes/fieldlayout/results/. Do not edit it, edit the measurement. -->",
        "",
        f"JOL {jol['version']} against the VM's own `-XX:+PrintFieldLayout`, both read "
        f"out of the same JVM in the same run. {total} class comparisons across "
        f"{len(results)} platform{'' if len(results) == 1 else 's'}: "
        f"{', '.join(sorted(results))}.",
        "",
    ]

    if everywhere:
        lines += ["JOL and the VM agreed on every field of every class.", ""]
    else:
        names = sorted({name for result in results.values()
                        for name in disagreements(result)})
        lines += [
            f"JOL and the VM agreed on every field of every class except "
            f"{', '.join('`' + name + '`' for name in names)}.",
            "",
        ]

    for platform, result in results.items():
        lines += table(platform, result)

    for platform, result in results.items():
        for cls, why in sorted(disagreements(result).items()):
            lines += [
                f"### Where they disagree: `{cls}` on {platform}",
                "",
            ]
            lines += [f"- {reason}" for reason in why]
            lines += [""]
            lines += offsets(result, cls)
            lines += [""]

    said = banner(results)
    if said:
        lines += [
            "## What JOL says about itself",
            "",
            "JOL prints this before it prints a layout, in every configuration "
            "measured:",
            "",
            "```",
            *said,
            "```",
            "",
        ]
    return "\n".join(lines) + "\n"


def banner(results: dict[str, dict]) -> list[str]:
    """What JOL printed about the VM before it printed any layout.

    Worth quoting rather than summarising, because the warnings in it are the reason
    somebody would distrust the table above, and a reader deciding whether to trust JOL
    should see the exact words JOL used rather than this file's paraphrase of them.
    """
    for result in results.values():
        for config in result["configurations"].values():
            for run in config["runs"].values():
                details = run.get("jol", {}).get("details")
                if details:
                    return ["# " + part.strip()
                            for part in details.split("#") if part.strip()]
    return []


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the page is not what the measurements say")
    args = ap.parse_args(argv)

    built = page(load())
    out = ROOT / OUTPUT
    if args.check:
        if not out.is_file():
            sys.exit(f"{OUTPUT} does not exist, run tools/gen_fieldlayout.py")
        if out.read_text(encoding="utf-8") != built:
            sys.exit(f"{OUTPUT} does not match {RESULTS}, run tools/gen_fieldlayout.py")
        print(f"{OUTPUT} matches {RESULTS}")
        return 0
    out.write_text(built, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
