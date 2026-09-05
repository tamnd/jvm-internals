#!/usr/bin/env python3
"""Hold each lesson and its claim ledger to each other.

`CONTRIBUTING.md` rule 7: every claim carries a marker, either `[JVMS]` with the section
or `[HOTSPOT]` with the source line, there is no third option and there is no unmarked
claim. `refcheck` already proves that every citation resolves. What it cannot see is
whether the ledger and the lesson are describing the same lesson.

  python tools/claimcheck.py           check every lesson
  python tools/claimcheck.py O01       check one
  python tools/claimcheck.py --report  print the ledger as a table

The check that earns this file is the correspondence in both directions. A marker in the
prose with no entry in the ledger is a claim nobody wrote down the evidence for. An entry
in the ledger with no marker in the prose is worse: it means the lesson asserts something
the author knew needed a citation, in a sentence that does not carry one, and the ledger
is the only place that knows. Both are silent failures, and a reader cannot detect either.

The rest are cheaper and still worth having. A claim marked `JVMS` whose citation is a
source line is a category error rather than a typo, because the two markers mean opposite
things about whether a reader can rely on the claim. A claim marked observable that names
no cell is a promise the lesson does not keep. A claim marked observable with nothing in
`measured` is the failure this whole project is built to avoid: an assertion that sounds
measured and was not.

The cap on unobservable claims is two per lesson, from the CI plan in `ci.yml`. It is not
a style rule. An unobservable claim is one the reader has to take on trust, and a lesson
that runs to three of them has stopped being a lesson where you can check.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refcheck  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"

# `{[HOTSPOT src/hotspot/share/oops/markWord.hpp:124@jdk-27+35]}` and `{[JVMS §2.7@SE25]}`
MARKER = re.compile(r"\{\[(JVMS|HOTSPOT)\s+([^\]]+)\]\}")
# `# %% [markdown] id=hook` and `# %% id=measure_1 env=E0`
CELL = re.compile(r"^# %%.*?\bid=(\S+)", re.MULTILINE)

MARKERS = ("JVMS", "HOTSPOT")
REQUIRED = ("id", "claim", "marker", "citation", "observable")
UNOBSERVABLE_CAP = 2


def lessons(only: str | None) -> list[pathlib.Path]:
    found = [p for p in sorted(LESSONS.glob("*/")) if (p / "claims.json").is_file()]
    if only:
        found = [p for p in found if p.name == only]
        if not found:
            raise SystemExit(f"no lesson {only} with a claims.json")
    return found


def key(marker: str, citation: str) -> str | None:
    """One citation reduced to what it points at, so two spellings of it compare equal.

    `JVMS §2.7@SE25` in the prose and `JVMS 2.7@SE25` in the ledger are the same citation.
    Comparing the strings would call them different and send somebody hunting for a
    discrepancy that is a section sign.

    A marker in the prose reads `{[JVMS §2.7@SE25]}`, and what comes back from the marker
    pattern is the part after the marker word, so the word goes back on before the
    citation pattern sees it. The alternative is a second pattern that means the same
    thing as the first, which is how two tools start disagreeing about what a citation is.
    """
    if marker == "HOTSPOT":
        found = refcheck.SOURCE_CITE.search(citation)
        return f"{found.group(1)}:{found.group(2)}@{found.group(3)}" if found else None
    found = refcheck.SPEC_CITE.search(f"{marker} {citation}")
    return f"{found.group(1)} {found.group(2)}@{found.group(3)}" if found else None


def markers(source: str) -> dict[str, list[int]]:
    """Every marker in a lesson, reduced to citations, with the lines they appear on."""
    found: dict[str, list[int]] = {}
    for number, line in enumerate(source.splitlines(), start=1):
        for match in MARKER.finditer(line):
            reduced = key(match.group(1), match.group(2))
            if reduced:
                found.setdefault(reduced, []).append(number)
    return found


def shape(claims: list, lesson: str) -> list[str]:
    """The problems a single ledger has on its own, before the lesson is opened."""
    problems: list[str] = []
    seen: set[str] = set()
    for position, claim in enumerate(claims, start=1):
        name = claim.get("id", f"the claim at position {position}")
        for field in REQUIRED:
            if field not in claim:
                problems.append(f"{lesson} {name} has no {field}")
        if problems and name not in {c.get("id") for c in claims}:
            continue
        wanted = f"{lesson}-C{position}"
        if claim.get("id") != wanted:
            problems.append(
                f"{lesson} claim {position} is called {claim.get('id')!r} and should be "
                f"{wanted}, because a gap in the numbering hides a deleted claim")
        if claim.get("id") in seen:
            problems.append(f"{lesson} has two claims called {claim['id']}")
        seen.add(claim.get("id"))
        if claim.get("marker") not in MARKERS:
            problems.append(
                f"{lesson} {name} is marked {claim.get('marker')!r}. Rule 7 has two "
                f"markers and no third option")
            continue
        if key(claim["marker"], claim.get("citation", "")) is None:
            problems.append(
                f"{lesson} {name} is marked {claim['marker']} and its citation is "
                f"{claim.get('citation')!r}, which is not a {claim['marker']} citation. "
                f"The two markers mean opposite things about what a reader can rely on, "
                f"so this is a category error rather than a typo")
        if claim.get("observable"):
            if not claim.get("cell"):
                problems.append(
                    f"{lesson} {name} says it is observable and names no cell, so the "
                    f"lesson does not show what it says it shows")
            if not claim.get("measured"):
                problems.append(
                    f"{lesson} {name} says it is observable and records nothing under "
                    f"measured. An assertion that sounds measured and was not is the one "
                    f"failure this project exists to avoid")
        elif claim.get("cell"):
            problems.append(
                f"{lesson} {name} says it is not observable and names cell "
                f"{claim['cell']!r}. One of the two is wrong")
    unobservable = [c["id"] for c in claims if not c.get("observable")]
    if len(unobservable) > UNOBSERVABLE_CAP:
        problems.append(
            f"{lesson} has {len(unobservable)} claims a reader has to take on trust "
            f"({', '.join(unobservable)}) and the cap is {UNOBSERVABLE_CAP}")
    return problems


def against_lesson(claims: list, source: str, lesson: str) -> list[str]:
    """The problems that only appear when the ledger and the prose are read together."""
    problems: list[str] = []
    cells = set(CELL.findall(source))
    marked = markers(source)

    for claim in claims:
        name = claim.get("id", "a claim")
        cell = claim.get("cell")
        if cell and cell not in cells:
            problems.append(
                f"{lesson} {name} names cell {cell!r}, which is not in the lesson")
        reduced = key(claim.get("marker", ""), claim.get("citation", ""))
        if reduced and reduced not in marked:
            problems.append(
                f"{lesson} {name} cites {claim['citation']} and no sentence in the "
                f"lesson carries that marker. Either the claim is made without a marker, "
                f"which rule 7 forbids, or the ledger is describing a lesson that was "
                f"rewritten around it")

    known = {key(c.get("marker", ""), c.get("citation", "")) for c in claims}
    for reduced, lines in sorted(marked.items()):
        if reduced not in known:
            where = ", ".join(str(line) for line in lines)
            problems.append(
                f"{lesson} carries the marker {reduced} at line {where} and the ledger "
                f"has no claim for it, so nobody wrote down what it is evidence for")
    return problems


def check(path: pathlib.Path) -> list[str]:
    lesson = path.name
    claims = json.loads((path / "claims.json").read_text(encoding="utf-8"))
    if not isinstance(claims, list) or not claims:
        return [f"{lesson} has a claims.json that is not a list of claims"]
    problems = shape(claims, lesson)
    source_file = path / "lesson.py"
    if not source_file.is_file():
        return problems + [f"{lesson} has a claims.json and no lesson.py"]
    return problems + against_lesson(
        claims, source_file.read_text(encoding="utf-8"), lesson)


def report(path: pathlib.Path) -> str:
    lesson = path.name
    claims = json.loads((path / "claims.json").read_text(encoding="utf-8"))
    lines = [f"{lesson}: {len(claims)} claims, "
             f"{sum(1 for c in claims if not c.get('observable'))} a reader must trust"]
    for claim in claims:
        shown = claim["claim"]
        if len(shown) > 72:
            shown = shown[:69] + "..."
        seen = claim.get("cell", "not observable")
        lines.append(f"  {claim['id']:8s} {claim['marker']:8s} {seen:16s} {shown}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lesson", nargs="?", help="one lesson id, or every lesson")
    ap.add_argument("--report", action="store_true", help="print the ledgers as a table")
    args = ap.parse_args(argv)

    found = lessons(args.lesson)
    problems: list[str] = []
    for path in found:
        problems.extend(check(path))
        if args.report:
            print(report(path))

    for line in problems:
        print(f"claimcheck: {line}", file=sys.stderr)
    if problems:
        return 1
    total = sum(len(json.loads((p / "claims.json").read_text(encoding="utf-8")))
                for p in found)
    print(f"claimcheck: {len(found)} lesson{'' if len(found) == 1 else 's'}, {total} "
          f"claims, every one marked, cited, and matched to a sentence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
