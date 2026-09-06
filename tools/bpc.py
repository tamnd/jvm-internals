#!/usr/bin/env python3
"""The blueprint compiler. Nine sections in, one committed page and a checklist out.

`docs/blueprint-format.md` is the format this implements and the document to read first.
The short version: a blueprint is a directory of nine numbered section files, some of
which are one line pointing at a generated page, and `bpc build` turns it into
`docs/blueprints/<ID>.md` plus an entry in `docs/generated/conformance.json`.

  python tools/bpc.py new BP-CONSTPOOL   scaffold a blueprint
  python tools/bpc.py build              compile every blueprint
  python tools/bpc.py check              rebuild in memory and fail on any difference
  python tools/bpc.py list               every blueprint, its sections and its clauses
  python tools/bpc.py coverage           every clause and what observes and tests it

What makes this worth having as a compiler rather than a convention is section 4. A
clause that says the specification mandates something, and cites a line of HotSpot source
for it, is the one defect that would make this project worse than useless: it reads as
"Java guarantees this" and it is a statement about one implementation at one tag. So the
marker and the citation are checked against the subsection the clause is in, and a clause
in the wrong one fails the build rather than waiting for a reviewer to notice.

The second thing it exists for is coverage. Every clause has to be named by a section 5
item, which says how somebody watches it happen and with what tool, and by a section 8.2
item, which is what the M11 capstone harness consumes. A specified behaviour that nobody
can observe and no harness tests is a sentence rather than a specification, and this is
where that gets caught.

Standard library only, no network, and it reads nothing outside the repository.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "blueprints"
BUILT = ROOT / "docs" / "blueprints"
CONFORMANCE = ROOT / "docs" / "generated" / "conformance.json"
PIN = ROOT / "docs" / "pin.json"

SECTION_COUNT = 9
MAY_BE_EMPTY = {7, 9}
REASON_FLOOR = 40

FENCE = "---"
SECTION_FILE = re.compile(r"^(?P<number>[1-9])-(?P<slug>[a-z0-9-]+)\.md$")
BLUEPRINT_ID = re.compile(r"^BP-[A-Z0-9]+$")

GENERATED = re.compile(r"^<!-- generated: (?P<path>[\w./-]+) -->$")
EMPTY = re.compile(r"^<!-- empty: (?P<why>.*?) -->$")
HEADING = re.compile(r"^(?P<hashes>#{1,6}) (?P<text>.+)$")
GENERATED_H1 = re.compile(
    r"^# (?P<id>BP-[A-Z0-9]+) section (?P<number>\d+)\. (?P<title>.+)$"
)

# A clause is one line, its number is permanent, and the number carries the subsection
# it belongs to so a clause cannot be moved between 4a and 4b by cut and paste.
CLAUSE = re.compile(r"^- \*\*(?P<id>\d[a-c]?(?:\.\d+)+)\*\* (?P<body>.+)$")
CLAUSE_ID = re.compile(r"^(?P<section>\d)(?P<part>[a-c]?)\.(?P<rest>[\d.]+)$")
REFERENCE = re.compile(r"\b\d[a-c]?\.\d+\b")

# The house marker, the same one the lessons and the claim ledgers use, so one grep
# finds a clause and every claim that rests on it.
MARKER = re.compile(r"\{\[(?P<kind>JVMS|HOTSPOT) (?P<cite>[^\]]+)\]\}")

TOOL = re.compile(r"`[^`]+`")
UNOBSERVABLE = "unobservable."

# Section 6 has to cover these three, found by the words in its own subheadings. The
# concurrent case is the one that gets left out, and it is the one that costs a reader a
# week when they meet it.
EDGE_CASES = {
    "malformed input": ("malformed", "invalid", "corrupt"),
    "resource exhaustion": ("exhaust", "limit", "too large", "overflow"),
    "the concurrent case": ("concurren", "thread", "race"),
}

# A floor rather than a ceiling. No machine can tell that a paragraph is motivating
# rather than specifying, so the expert review is where that is answered. These are the
# phrases that mean the blueprint has started leaning on a document it may not assume the
# reader has, which is the failure that makes it unimplementable on its own.
BANNED_PHRASES = (
    "this chapter",
    "the chapter",
    "this lesson",
    "the lesson",
    "as we saw",
    "we saw earlier",
    "we will see",
    "think of it as",
    "analogy",
    "imagine ",
)

TABLE_ROW = re.compile(r"^\|(?P<first>[^|]*)\|")
SEPARATOR = re.compile(r"^\|[\s:|-]+\|?$")


class BlueprintError(Exception):
    """A problem in a blueprint source, reported with the file it came from."""


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def _scalar(raw: str) -> object:
    text = raw.strip()
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        return text[1:-1]
    if text in {"null", "~", ""}:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse_front_matter(text: str, path: pathlib.Path) -> dict:
    """The same small subset of YAML the lesson format accepts, and nothing else.

    A flat mapping of scalars, `key: [a, b]` lists, and one level of nesting written with
    two spaces. Anything else is refused with the line number rather than dropped.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != FENCE:
        raise BlueprintError(f"{path}:1: a blueprint starts with a '{FENCE}' fence")
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == FENCE:
            end = i
            break
    if end is None:
        raise BlueprintError(f"{path}:1: front matter fence is never closed")
    for i in range(end + 1, len(lines)):
        if lines[i].strip():
            raise BlueprintError(
                f"{path}:{i + 1}: blueprint.md holds the front matter and nothing else. "
                f"Section prose goes in a numbered section file."
            )

    data: dict = {}
    current: str | None = None
    for offset in range(1, end):
        lineno = offset + 1
        body = lines[offset].rstrip()
        if not body.strip():
            continue
        indented = body.startswith("  ")
        stripped = body.strip()
        if ":" not in stripped:
            raise BlueprintError(f"{path}:{lineno}: front matter line has no ':'")
        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()
        if indented:
            if current is None or not isinstance(data.get(current), dict):
                raise BlueprintError(
                    f"{path}:{lineno}: indented line with no mapping above it"
                )
            data[current][key] = _scalar(value)
            continue
        if value == "":
            data[key] = {}
            current = key
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_scalar(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            data[key] = _scalar(value)
        current = key
    return data


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Section:
    number: int
    path: pathlib.Path
    title: str
    lines: list[str]  # the body, headings as the author wrote them
    generated: str | None = None  # repository relative path of the included page
    empty: str | None = None  # the reason, if the section is empty


@dataclasses.dataclass
class Clause:
    id: str
    part: str  # 4a, 4b, 4c
    text: str
    kinds: tuple[str, ...]
    citations: tuple[str, ...]
    path: pathlib.Path


@dataclasses.dataclass
class Item:
    id: str
    refs: tuple[str, ...]
    text: str
    path: pathlib.Path


@dataclasses.dataclass
class Blueprint:
    id: str
    front: dict
    directory: pathlib.Path
    sections: dict[int, Section]
    clauses: dict[str, Clause]
    observations: dict[str, Item]
    conformance: dict[str, Item]

    @property
    def title(self) -> str:
        return str(self.front["title"])


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def load_section(path: pathlib.Path, number: int, blueprint_id: str) -> Section:
    lines = read(path).splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    body = [line for line in lines if line.strip()]
    if first is None:
        raise BlueprintError(f"{path}: empty file. A section that has nothing to say says why.")

    found = GENERATED.match(body[0])
    if found:
        if len(body) > 1:
            raise BlueprintError(
                f"{path}: a generated section is the directive and nothing else. The "
                f"build would overwrite anything written under it, and a hand written "
                f"paragraph that disagrees with the table above it is the failure the "
                f"split exists to prevent."
            )
        target = ROOT / found.group("path")
        if not target.is_file():
            raise BlueprintError(f"{path}: {found.group('path')} does not exist")
        head = read(target).splitlines()[0]
        declared = GENERATED_H1.match(head)
        if not declared:
            raise BlueprintError(
                f"{found.group('path')}:1: a page included as a blueprint section starts "
                f"with '# {blueprint_id} section {number}. <title>', not {head!r}"
            )
        if declared.group("id") != blueprint_id or int(declared.group("number")) != number:
            raise BlueprintError(
                f"{found.group('path')}:1: says it is {declared.group('id')} section "
                f"{declared.group('number')}, included as {blueprint_id} section {number}"
            )
        return Section(
            number=number,
            path=path,
            title=declared.group("title"),
            lines=[],
            generated=found.group("path"),
        )

    heading = HEADING.match(body[0])
    if not heading or heading.group("hashes") != "#":
        raise BlueprintError(f"{path}:1: a section starts with '# {number}. <title>'")
    text = heading.group("text")
    prefix = f"{number}. "
    if not text.startswith(prefix):
        raise BlueprintError(
            f"{path}:1: heading is {text!r}, and this is section {number}, so it starts "
            f"with {prefix!r}"
        )
    title = text[len(prefix):].strip()

    rest = lines[first + 1:]
    reasons = [EMPTY.match(line.strip()) for line in rest if line.strip()]
    if reasons and all(reasons):
        if number not in MAY_BE_EMPTY:
            raise BlueprintError(
                f"{path}: section {number} may not be empty. Sections 1 to 6 and 8 are "
                f"what a blueprint is, and only 7 and 9 can honestly have nothing in them."
            )
        why = " ".join(found.group("why").strip() for found in reasons)
        if len(why) < REASON_FLOOR:
            raise BlueprintError(
                f"{path}: an empty section says why in at least {REASON_FLOOR} "
                f"characters, and this one says {why!r}"
            )
        return Section(number=number, path=path, title=title, lines=[], empty=why)

    while rest and not rest[0].strip():
        rest.pop(0)
    while rest and not rest[-1].strip():
        rest.pop()
    if not rest:
        raise BlueprintError(f"{path}: section {number} has a heading and nothing under it")
    return Section(number=number, path=path, title=title, lines=rest)


def subsection_of(section: Section, index: int) -> str:
    """Which `## Na` subheading the line at `index` sits under."""
    part = ""
    for line in section.lines[: index + 1]:
        found = HEADING.match(line)
        if found and found.group("hashes") == "##":
            head = found.group("text").split()[0].rstrip(".")
            part = head
    return part


def clauses_of(section: Section) -> list[tuple[str, str, str, int]]:
    """Every clause line in a section, as (subsection, id, body, index)."""
    out = []
    fenced = False
    for index, line in enumerate(section.lines):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        found = CLAUSE.match(line)
        if found:
            out.append((subsection_of(section, index), found.group("id"),
                        found.group("body"), index))
    return out


def parse_clauses(bp_id: str, section: Section) -> dict[str, Clause]:
    found: dict[str, Clause] = {}
    order: list[str] = []
    for part, clause_id, body, _ in clauses_of(section):
        if part not in {"4a", "4b", "4c"}:
            raise BlueprintError(
                f"{section.path}: clause {clause_id} is not under a '## 4a', '## 4b' or "
                f"'## 4c' heading, and section 4 is only those three"
            )
        if not clause_id.startswith(part + "."):
            raise BlueprintError(
                f"{section.path}: clause {clause_id} is under {part}, so its number "
                f"starts with '{part}.'"
            )
        if clause_id in found:
            raise BlueprintError(f"{section.path}: clause {clause_id} appears twice")
        kinds = tuple(m.group("kind") for m in MARKER.finditer(body))
        cites = tuple(m.group("cite") for m in MARKER.finditer(body))
        found[clause_id] = Clause(
            id=clause_id, part=part, text=body, kinds=kinds, citations=cites,
            path=section.path,
        )
        order.append(clause_id)

    for part in ("4a", "4b", "4c"):
        numbers = [int(c.split(".")[1]) for c in order if c.startswith(part + ".")]
        if not numbers:
            raise BlueprintError(
                f"{section.path}: {part} has no clauses. {bp_id} either mandates "
                f"nothing, chooses nothing or orders nothing, and one of those is "
                f"unlikely enough to be worth writing down instead."
            )
        if numbers != sorted(numbers):
            raise BlueprintError(
                f"{section.path}: {part} clauses are out of order: {numbers}. Gaps are "
                f"fine, because a retired number is never reused, but the order is what "
                f"a reader navigates by."
            )
    return found


def parse_items(section: Section, prefix: str) -> dict[str, Item]:
    """Section 5 items, or section 8.2 items, each naming the clauses it covers."""
    found: dict[str, Item] = {}
    for part, item_id, body, _ in clauses_of(section):
        if not item_id.startswith(prefix):
            if section.number == 8:
                continue
            raise BlueprintError(
                f"{section.path}: item {item_id} does not start with {prefix!r}"
            )
        if section.number == 8 and part not in {"8.2", ""}:
            continue
        if ":" not in body:
            raise BlueprintError(
                f"{section.path}: item {item_id} is '<clauses>: <what>', and this one "
                f"has no colon"
            )
        head, _, rest = body.partition(":")
        refs = tuple(REFERENCE.findall(head))
        if not refs:
            raise BlueprintError(
                f"{section.path}: item {item_id} names no clause. An item that covers "
                f"nothing is an item nobody can act on."
            )
        if item_id in found:
            raise BlueprintError(f"{section.path}: item {item_id} appears twice")
        found[item_id] = Item(id=item_id, refs=refs, text=rest.strip(), path=section.path)
    return found


def load(directory: pathlib.Path) -> Blueprint:
    bp_id = directory.name
    if not BLUEPRINT_ID.match(bp_id):
        raise BlueprintError(f"{directory}: a blueprint directory is named BP-SOMETHING")
    meta = directory / "blueprint.md"
    if not meta.is_file():
        raise BlueprintError(f"{directory}: no blueprint.md")
    front = parse_front_matter(read(meta), meta)

    required = {"id", "title", "pin", "edition", "status"}
    missing = sorted(required - set(front))
    if missing:
        raise BlueprintError(f"{meta}: front matter is missing {', '.join(missing)}")
    if front["id"] != bp_id:
        raise BlueprintError(f"{meta}: id is {front['id']!r} and the directory is {bp_id}")
    if front["status"] not in {"draft", "final"}:
        raise BlueprintError(f"{meta}: status is 'draft' or 'final', not {front['status']!r}")

    pin = json.loads(read(PIN))
    if front["pin"] != pin["jdk_tag"]:
        raise BlueprintError(
            f"{meta}: pin is {front['pin']!r} and docs/pin.json says {pin['jdk_tag']!r}"
        )
    if front["edition"] != pin["jvms_edition"]:
        raise BlueprintError(
            f"{meta}: edition is {front['edition']!r} and docs/pin.json says "
            f"{pin['jvms_edition']!r}"
        )

    numbers: dict[int, pathlib.Path] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "blueprint.md":
            continue
        found = SECTION_FILE.match(path.name)
        if not found:
            raise BlueprintError(
                f"{path}: a section file is named '<1 to 9>-<slug>.md', so this one is "
                f"either misnamed or does not belong here"
            )
        number = int(found.group("number"))
        if number in numbers:
            raise BlueprintError(f"{path}: section {number} is also {numbers[number].name}")
        numbers[number] = path

    absent = [n for n in range(1, SECTION_COUNT + 1) if n not in numbers]
    if absent:
        raise BlueprintError(
            f"{directory}: no section {', '.join(str(n) for n in absent)}. All nine are "
            f"present in every blueprint, and one with nothing in it says why."
        )

    sections = {n: load_section(numbers[n], n, bp_id) for n in sorted(numbers)}
    clauses = parse_clauses(bp_id, sections[4]) if not sections[4].generated else {}
    observations = parse_items(sections[5], "5.")
    conformance = parse_items(sections[8], "8.2.")
    return Blueprint(
        id=bp_id, front=front, directory=directory, sections=sections,
        clauses=clauses, observations=observations, conformance=conformance,
    )


def load_all() -> list[Blueprint]:
    if not SOURCE.is_dir():
        return []
    return [load(p) for p in sorted(SOURCE.iterdir()) if p.is_dir()]


# ---------------------------------------------------------------------------
# The checks that are not about assembling the page
# ---------------------------------------------------------------------------


def table_keys(text: str) -> set[str]:
    """The first column of every table row, minus the header row and the separator.

    The header is dropped because two tables about different things can both have a
    column called `item`, and that is not duplication. The rows are the content.
    """
    keys: set[str] = set()
    in_body = False
    for line in text.splitlines():
        stripped = line.strip()
        row = TABLE_ROW.match(stripped)
        if not row:
            in_body = False
            continue
        if SEPARATOR.match(stripped):
            in_body = True
            continue
        if not in_body:
            continue  # the header row of a table that has not started yet
        cell = row.group("first").strip().strip("`").strip()
        if len(cell) > 1:
            keys.add(cell.lower())
    return keys


def check_duplication(bp: Blueprint) -> list[str]:
    generated: set[str] = set()
    for section in bp.sections.values():
        if section.generated:
            generated |= table_keys(read(ROOT / section.generated))
    if not generated:
        return []
    problems = []
    for section in bp.sections.values():
        if section.generated:
            continue
        for key in sorted(table_keys("\n".join(section.lines)) & generated):
            problems.append(
                f"{section.path}: a table row for `{key}`, which a generated section of "
                f"{bp.id} already has a row for. Name it in a sentence, do not retype "
                f"the table, because the retyped one is the one that will still be here "
                f"saying something else when the generated one has moved on."
            )
    return problems


def check_phrases(bp: Blueprint) -> list[str]:
    problems = []
    for section in bp.sections.values():
        if section.generated:
            continue
        fenced = False
        for index, line in enumerate(section.lines, start=2):
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            lowered = line.lower()
            for phrase in BANNED_PHRASES:
                if phrase in lowered:
                    problems.append(
                        f"{section.path}:{index}: {phrase!r}. A blueprint is read by "
                        f"somebody implementing from it cold, who has not read the "
                        f"chapter it accompanies and may not have one."
                    )
    return problems


def check_section4(bp: Blueprint) -> list[str]:
    problems = []
    for clause in bp.clauses.values():
        kinds = set(clause.kinds)
        if not kinds:
            problems.append(
                f"{clause.path}: clause {clause.id} carries no marker. Every clause is "
                f"either something the specification mandates or something HotSpot "
                f"chose, and which one it is is the entire content of section 4."
            )
            continue
        if clause.part == "4a" and "HOTSPOT" in kinds:
            problems.append(
                f"{clause.path}: clause {clause.id} is in 4a, what the specification "
                f"mandates, and cites HotSpot source. If the specification mandates it, "
                f"cite the specification. If HotSpot chose it, it is a 4b clause."
            )
        if clause.part == "4a" and "JVMS" not in kinds:
            problems.append(
                f"{clause.path}: clause {clause.id} is in 4a and cites no specification "
                f"section"
            )
        if clause.part == "4b" and "JVMS" in kinds:
            problems.append(
                f"{clause.path}: clause {clause.id} is in 4b, what HotSpot chose, and "
                f"carries a JVMS marker. The clause everybody wants to write here is "
                f"two clauses: a 4a one saying what is not mandated and a 4b one saying "
                f"what was chosen."
            )
        if clause.part == "4b" and "HOTSPOT" not in kinds:
            problems.append(
                f"{clause.path}: clause {clause.id} is in 4b and cites no source line"
            )
    return problems


def check_coverage(bp: Blueprint) -> list[str]:
    problems = []
    observed: dict[str, list[str]] = {}
    tested: dict[str, list[str]] = {}
    for item in bp.observations.values():
        for ref in item.refs:
            observed.setdefault(ref, []).append(item.id)
    for item in bp.conformance.values():
        for ref in item.refs:
            tested.setdefault(ref, []).append(item.id)

    for item in list(bp.observations.values()) + list(bp.conformance.values()):
        for ref in item.refs:
            if ref not in bp.clauses:
                problems.append(
                    f"{item.path}: item {item.id} covers {ref}, and there is no clause "
                    f"{ref} in {bp.id}"
                )
    for clause_id in bp.clauses:
        if clause_id not in observed:
            problems.append(
                f"{bp.sections[5].path}: no section 5 item observes clause "
                f"{clause_id}. Every specified behaviour has an observation and a tool, "
                f"or it says in one line why it cannot be watched."
            )
        if clause_id not in tested:
            problems.append(
                f"{bp.sections[8].path}: no section 8.2 item covers clause "
                f"{clause_id}, so the capstone harness has nothing to report against it"
            )

    for item in bp.observations.values():
        rest = item.text
        if rest.lower().startswith(UNOBSERVABLE):
            reason = rest[len(UNOBSERVABLE):].strip()
            if len(reason) < REASON_FLOOR:
                problems.append(
                    f"{item.path}: item {item.id} is unobservable and its reason is "
                    f"{reason!r}, which is under {REASON_FLOOR} characters"
                )
            continue
        if not TOOL.search(rest):
            problems.append(
                f"{item.path}: item {item.id} names no tool. An observation with no "
                f"tool is a description of an experiment nobody has run."
            )
    return problems


def check_edge_cases(bp: Blueprint) -> list[str]:
    section = bp.sections[6]
    if section.generated:
        return []
    heads = [
        HEADING.match(line).group("text").lower()
        for line in section.lines
        if HEADING.match(line)
    ]
    joined = " ".join(heads)
    problems = []
    for name, words in EDGE_CASES.items():
        if not any(word in joined for word in words):
            problems.append(
                f"{section.path}: section 6 has no subheading about {name}. The three "
                f"are malformed input, resource exhaustion and the concurrent case, and "
                f"the concurrent one is the one that gets left out."
            )
    return problems


def check(bp: Blueprint) -> list[str]:
    problems: list[str] = []
    problems += check_section4(bp)
    problems += check_coverage(bp)
    problems += check_edge_cases(bp)
    problems += check_duplication(bp)
    problems += check_phrases(bp)
    return problems


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def demote(lines: list[str]) -> list[str]:
    """Every heading one level deeper, because the page above it has an H1 of its own."""
    out = []
    fenced = False
    for line in lines:
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        found = HEADING.match(line) if not fenced else None
        if found and len(found.group("hashes")) < 6:
            out.append("#" + line)
        else:
            out.append(line)
    return out


def included(section: Section) -> list[str]:
    """The body of a generated page, with its own H1 dropped and the rest demoted."""
    lines = read(ROOT / section.generated).splitlines()
    while lines and not HEADING.match(lines[0]):
        lines.pop(0)
    lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    link = "../" + section.generated.split("docs/", 1)[1]
    note = (
        f"Included from [{section.generated}]({link}), which is generated. Edit the "
        f"generator, not this section and not that page."
    )
    return [note, ""] + demote(lines)


def render(bp: Blueprint) -> str:
    out: list[str] = [f"# {bp.id}. {bp.title}", ""]
    out.append(
        f"Generated by `tools/bpc.py` from `blueprints/{bp.id}/`. Do not edit it, edit "
        f"a section. The format is [docs/blueprint-format.md](../blueprint-format.md)."
    )
    out.append("")
    out.append(
        f"Written against `{bp.front['pin']}` and JVMS {bp.front['edition']}. Every "
        f"clause in section 4a states what the specification mandates and cites a "
        f"section of it. Every clause in 4b states what HotSpot chose and cites a line "
        f"of its source at that tag. Nothing here refers to a lesson, so it can be "
        f"implemented by somebody who has not read one."
    )
    out.append("")
    if bp.front["status"] == "draft":
        out.append(
            "**Draft.** The clauses have not had an expert review, so read them as a "
            "claim about what is true rather than as a settled statement of it."
        )
        out.append("")
    lessons = bp.front.get("lessons") or []
    if lessons:
        out.append(
            f"Lessons that rest on it: {', '.join(str(lesson) for lesson in lessons)}."
        )
        out.append("")

    for number in range(1, SECTION_COUNT + 1):
        section = bp.sections[number]
        out.append(f"## {number}. {section.title}")
        out.append("")
        if section.generated:
            out.extend(included(section))
        elif section.empty:
            out.append(f"Empty. {section.empty}")
        else:
            out.extend(demote(section.lines))
        out.append("")

    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def conformance_json(blueprints: list[Blueprint]) -> str:
    """The checklist the M11 capstone harness consumes, and the clauses behind it."""
    pin = json.loads(read(PIN))
    payload = {
        "built_by": "tools/bpc.py",
        "pin": pin["jdk_tag"],
        "edition": pin["jvms_edition"],
        "blueprints": [],
    }
    for bp in blueprints:
        observed: dict[str, list[str]] = {}
        tested: dict[str, list[str]] = {}
        for item in bp.observations.values():
            for ref in item.refs:
                observed.setdefault(ref, []).append(item.id)
        for item in bp.conformance.values():
            for ref in item.refs:
                tested.setdefault(ref, []).append(item.id)
        payload["blueprints"].append({
            "id": bp.id,
            "title": bp.title,
            "status": bp.front["status"],
            "page": f"docs/blueprints/{bp.id}.md",
            "clauses": [
                {
                    "id": clause.id,
                    "part": clause.part,
                    "kinds": sorted(set(clause.kinds)),
                    "citations": list(clause.citations),
                    "text": MARKER.sub("", clause.text).replace("  ", " ").strip(),
                    "observed_by": observed.get(clause.id, []),
                    "tested_by": tested.get(clause.id, []),
                }
                for clause in bp.clauses.values()
            ],
            "checklist": [
                {"id": item.id, "clauses": list(item.refs), "requirement": item.text}
                for item in bp.conformance.values()
            ],
        })
    return json.dumps(payload, indent=2) + "\n"


def built_path(bp: Blueprint) -> pathlib.Path:
    return BUILT / f"{bp.id}.md"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def drift(committed: str, fresh: str, name: str) -> str:
    lines = list(difflib.unified_diff(
        committed.splitlines(), fresh.splitlines(),
        fromfile=f"{name} as committed", tofile=f"{name} as built", lineterm="", n=1,
    ))
    return "\n".join(lines[:40])


def cmd_build(write: bool) -> int:
    blueprints = load_all()
    if not blueprints:
        print("no blueprints yet", file=sys.stderr)
        return 0
    problems: list[str] = []
    for bp in blueprints:
        problems.extend(check(bp))
    if problems:
        for problem in problems:
            print(problem)
        print(f"bpc: {len(problems)} problems", file=sys.stderr)
        return 1

    BUILT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for bp in blueprints:
        fresh = render(bp)
        path = built_path(bp)
        if write:
            path.write_text(fresh, encoding="utf-8")
        elif not path.is_file() or read(path) != fresh:
            stale.append(drift(read(path) if path.is_file() else "", fresh, str(path)))
    fresh_json = conformance_json(blueprints)
    if write:
        CONFORMANCE.write_text(fresh_json, encoding="utf-8")
    elif not CONFORMANCE.is_file() or read(CONFORMANCE) != fresh_json:
        stale.append(drift(read(CONFORMANCE) if CONFORMANCE.is_file() else "",
                           fresh_json, str(CONFORMANCE)))

    if stale:
        for one in stale:
            print(one)
        print("bpc check: run 'python tools/bpc.py build' and commit the result",
              file=sys.stderr)
        return 1

    clauses = sum(len(bp.clauses) for bp in blueprints)
    items = sum(len(bp.conformance) for bp in blueprints)
    verb = "built" if write else "checked"
    print(f"bpc {verb}: {len(blueprints)} blueprints, {clauses} clauses, "
          f"{items} conformance items", file=sys.stderr)
    return 0


def cmd_list() -> int:
    for bp in load_all():
        generated = [str(n) for n, s in bp.sections.items() if s.generated]
        empty = [str(n) for n, s in bp.sections.items() if s.empty]
        print(f"{bp.id}  {bp.title}")
        print(f"  status {bp.front['status']}, {len(bp.clauses)} clauses, "
              f"{len(bp.conformance)} conformance items")
        print(f"  generated sections {', '.join(generated) or 'none'}, "
              f"empty sections {', '.join(empty) or 'none'}")
    return 0


def cmd_coverage() -> int:
    for bp in load_all():
        print(bp.id)
        observed: dict[str, list[str]] = {}
        tested: dict[str, list[str]] = {}
        for item in bp.observations.values():
            for ref in item.refs:
                observed.setdefault(ref, []).append(item.id)
        for item in bp.conformance.values():
            for ref in item.refs:
                tested.setdefault(ref, []).append(item.id)
        for clause in bp.clauses.values():
            print(f"  {clause.id:6} observed by {','.join(observed.get(clause.id, []))}"
                  f"  tested by {','.join(tested.get(clause.id, []))}")
    return 0


SCAFFOLD_META = """\
---
id: {id}
title: TODO
pin: {pin}
edition: {edition}
status: draft
lessons: []
reviews:
  expert: null
---
"""

SCAFFOLD_TITLES = {
    1: ("scope", "Scope"),
    2: ("data-structures", "Data structures"),
    3: ("operations", "Operations"),
    4: ("guarantees", "Guarantees"),
    5: ("observation", "Observation"),
    6: ("edge-cases", "Edge cases"),
    7: ("configuration", "Configuration"),
    8: ("conformance", "Conformance"),
    9: ("provenance", "Provenance"),
}

SCAFFOLD_BODIES = {
    4: "## 4a. What the specification mandates\n\n## 4b. What HotSpot chose\n\n"
       "## 4c. Ordering obligations\n",
    6: "## 6.1 Malformed input\n\n## 6.2 Resource exhaustion\n\n"
       "## 6.3 The concurrent case\n",
    8: "## 8.1 What conformance means here\n\n## 8.2 The checklist\n",
}


def cmd_new(bp_id: str) -> int:
    if not BLUEPRINT_ID.match(bp_id):
        print(f"a blueprint id looks like BP-CLASSFILE, not {bp_id!r}", file=sys.stderr)
        return 1
    directory = SOURCE / bp_id
    if directory.exists():
        print(f"{directory} already exists", file=sys.stderr)
        return 1
    pin = json.loads(read(PIN))
    directory.mkdir(parents=True)
    (directory / "blueprint.md").write_text(
        SCAFFOLD_META.format(id=bp_id, pin=pin["jdk_tag"], edition=pin["jvms_edition"]),
        encoding="utf-8",
    )
    for number, (slug, title) in SCAFFOLD_TITLES.items():
        body = SCAFFOLD_BODIES.get(number, "")
        (directory / f"{number}-{slug}.md").write_text(
            f"# {number}. {title}\n\n{body}", encoding="utf-8"
        )
    print(f"wrote {directory}. Write section 6 before section 3.", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="compile every blueprint into docs/blueprints/")
    sub.add_parser("check", help="rebuild in memory and fail on any difference")
    sub.add_parser("list", help="every blueprint, its sections and its clauses")
    sub.add_parser("coverage", help="every clause and what observes and tests it")
    new = sub.add_parser("new", help="scaffold a blueprint")
    new.add_argument("id")
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            return cmd_build(write=True)
        if args.command == "check":
            return cmd_build(write=False)
        if args.command == "list":
            return cmd_list()
        if args.command == "coverage":
            return cmd_coverage()
        if args.command == "new":
            return cmd_new(args.id)
    except BlueprintError as error:
        print(error)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
