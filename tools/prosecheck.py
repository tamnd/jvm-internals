#!/usr/bin/env python3
"""Check the prose rules from CONTRIBUTING.md that a machine can check.

Standard library only and no configuration file, because the whole value of this
thing is that it runs in CI on a fresh checkout without anyone installing
anything first.

Five rules:

  em-dash     No em dashes. Use a comma, a full stop or brackets.
  banned      No "simply", "just", "obviously", "of course" or "trivially".
  wrap        One paragraph is one line. A sentence never gets broken across
              two lines, because a hard wrapped paragraph produces a diff where
              one word change rewraps six lines and the review is unreadable.
  whitespace  No trailing whitespace and no tabs.
  pin         Every source citation ends in the tag from docs/pin.json and
              every specification citation ends in the edition from the same
              file. A citation against a tag nobody pinned is a citation
              nobody checked.

A line ending in the comment <!-- prose-ok --> is exempt from em-dash and
banned, which is how a document quotes a rule in order to state it.

Every .md file is checked, and so are the markdown cells inside
lessons/<id>/lesson.py, reported against their real line in that file. Lesson prose
is the prose readers actually read, so exempting it because it lives in a .py file
would be exempting the part that matters.

The marker rule, meaning that every claim carries [JVMS] or [HOTSPOT], is not
checked here. It needs the lesson front matter and the claim ledger, so it lives
in build.py and arrives with the first lesson.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

EM_DASH = "—"
ALLOW = "<!-- prose-ok -->"

BANNED = re.compile(
    r"(?<![\w-])(simply|just|obviously|trivially)(?![\w-])|of course",
    re.IGNORECASE,
)

# A source citation looks like src/hotspot/share/oops/markWord.hpp:48@jdk-27+35
# and a specification citation looks like JVMS 5.4.3.1@SE25. Both are matched on
# the suffix rather than the whole form, because the whole form is refcheck's
# job and this is the cheap half that catches a stale tag in a pull request
# without a checkout of OpenJDK.
SOURCE_CITE = re.compile(r"\.(?:cpp|hpp|c|h|java|ad|xml|md):\d+@(\S+?)(?=[\s)\]}.,;]|$)")
SPEC_CITE = re.compile(r"(?:JVMS|JLS)\s*[^@\s]*@(\S+?)(?=[\s)\]}.,;]|$)")

# Lines that are not prose, so the one-paragraph-one-line rule does not apply.
# Lists, tables, headings, quotes, footnotes and anything indented as a code
# block wrap for their own reasons.
NOT_PROSE = re.compile(r"^(\s*$|#{1,6} |\s*[-*+] |\s*\d+\. |\s*>|\||\s{4,}|\[\^)")


def load_pin(root: pathlib.Path) -> tuple[set[str], set[str]]:
    """Return the accepted source tags and the accepted specification editions."""
    pin_file = root / "docs" / "pin.json"
    if not pin_file.is_file():
        return set(), set()
    pin = json.loads(pin_file.read_text(encoding="utf-8"))
    tags = {pin[k] for k in ("jdk_tag", "jdk_ga_tag") if pin.get(k)}
    editions = {pin[k] for k in ("jvms_edition", "jls_edition") if pin.get(k)}
    return tags, editions


def markdown_of_lesson(path: pathlib.Path) -> list[tuple[int, str]]:
    """Pull the markdown cells out of a lesson source, keeping their real line numbers.

    A lesson's prose lives in `lessons/<id>/lesson.py` rather than in a `.md` file, so
    without this the rules would apply to every document in the repository except the
    ones readers actually read.
    """
    out: list[tuple[int, str]] = []
    in_markdown = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("# %%"):
            in_markdown = "[markdown]" in line
            continue
        if not in_markdown:
            continue
        if line.startswith("# "):
            out.append((i, line[2:]))
        elif line.strip() in {"#", ""}:
            out.append((i, ""))
    return out


def numbered(path: pathlib.Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def check(path: pathlib.Path, tags: set[str], editions: set[str]) -> list[str]:
    problems: list[str] = []
    if path.suffix == ".py":
        numbered_lines = markdown_of_lesson(path)
    else:
        numbered_lines = numbered(path)

    lines = [text for _, text in numbered_lines]
    fenced = False
    for index, (i, line) in enumerate(numbered_lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            continue

        exempt = ALLOW in line

        if not fenced:
            if EM_DASH in line and not exempt:
                problems.append(f"{path}:{i}: em dash")
            if not exempt:
                hit = BANNED.search(line)
                if hit:
                    problems.append(f"{path}:{i}: banned word {hit.group(0)!r}")

        if line != line.rstrip():
            problems.append(f"{path}:{i}: trailing whitespace")
        if "\t" in line:
            problems.append(f"{path}:{i}: tab")

        # The pin rule applies inside fenced blocks too, because a citation in a
        # code block is still a citation somebody will follow.
        for found in SOURCE_CITE.finditer(line):
            if tags and found.group(1) not in tags:
                problems.append(
                    f"{path}:{i}: citation against {found.group(1)!r}, "
                    f"the pin says {sorted(tags)}"
                )
        for found in SPEC_CITE.finditer(line):
            if editions and found.group(1) not in editions:
                problems.append(
                    f"{path}:{i}: specification citation against "
                    f"{found.group(1)!r}, the pin says {sorted(editions)}"
                )

        if fenced:
            continue

        # The wrap rule. A prose line followed by another prose line means the
        # paragraph got hard wrapped, so the sentence is broken across a
        # newline. Checked forwards rather than backwards so the reported line
        # is the one somebody has to go and join.
        if NOT_PROSE.match(line):
            continue
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if nxt.strip().startswith("```") or nxt.strip().startswith("~~~"):
            continue
        if not NOT_PROSE.match(nxt):
            problems.append(f"{path}:{i}: hard wrapped paragraph, join it onto one line")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories")
    args = parser.parse_args()

    targets: list[pathlib.Path] = []
    for raw in args.paths:
        p = pathlib.Path(raw)
        if p.is_dir():
            targets.extend(sorted(q for q in p.rglob("*.md") if ".git" not in q.parts))
            targets.extend(sorted(p.glob("lessons/*/lesson.py")))
        elif p.suffix == ".md" or p.name == "lesson.py":
            targets.append(p)

    root = pathlib.Path(__file__).resolve().parent.parent
    tags, editions = load_pin(root)

    problems: list[str] = []
    for path in targets:
        problems.extend(check(path, tags, editions))

    for problem in problems:
        print(problem)

    print(f"prosecheck: {len(targets)} files, {len(problems)} problems", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
