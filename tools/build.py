#!/usr/bin/env python3
"""Compile a lesson source into a notebook, and check that the committed notebook matches.

A lesson lives in `lessons/<id>/lesson.py`. It is Python in jupytext percent format
with a YAML front matter block at the top. `build.py notebooks` compiles it to
`notebooks/<id>/lesson.ipynb`, which is committed so that Colab can open it from a
raw GitHub URL, and marked `linguist-generated` so it stays out of your diffs.

The rule that makes this work is that the notebook is output and never input. Nobody
edits `notebooks/`. `build.py check` rebuilds every lesson in memory and compares
byte for byte against what is committed, so a hand edit to a notebook fails CI with
the file and the reason.

Byte stability comes from three decisions. Cell ids are a hash of the cell's type,
its directives and its source, so an unchanged cell keeps its id forever and a diff
shows only the cells that actually changed. Execution counts are never written, so a
notebook cannot carry hidden state about the order somebody happened to run things
in. Outputs are never captured from a live run: a cell that cannot produce stable
output is tagged `bake` and its output is read from a recorded file on disk, which
is the only place in this project where an output is allowed to come from anywhere
other than the reader's own machine.

Standard library only, no configuration file, and no YAML dependency. The front
matter parser accepts a deliberately small subset of YAML and refuses the rest with
a clear message, because a lesson header that needs more than a flat mapping, a
list of scalars and one level of nesting is a lesson header that has grown a second
job.

Subcommands:

  new <id>      scaffold a lesson directory with the ten blocks and a grader stub
  notebooks     compile every lesson to notebooks/<id>/lesson.ipynb
  check         rebuild in memory and fail on any difference, plus the structural rules
  run <id>      execute a lesson's tier 0 cells, if a Jupyter runtime is available
  list          print the lesson index as a table
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

# Percent format. A cell starts at a line beginning with `# %%`. Everything after
# that marker on the same line is the cell type and the directives.
CELL_START = re.compile(r"^# %%(?P<rest>.*)$")

# `[markdown]` or `[raw]` right after the marker, the way jupytext writes it.
CELL_TYPE = re.compile(r"^\s*\[(?P<type>markdown|raw)\]")

# One directive: key=value, where value is either a bare token or a bracketed list.
DIRECTIVE = re.compile(r"(?P<key>[a-z_]+)=(?P<value>\[[^\]]*\]|\S+)")

FRONT_MATTER_FENCE = "# ---"

CELL_TYPES = {"code", "markdown", "raw"}

# Directives a cell may carry. Anything else is a typo and is rejected rather than
# ignored, because a silently ignored directive is a lesson that does not do what
# its author thinks it does.
KNOWN_DIRECTIVES = {"id", "tags", "env", "replay"}

KNOWN_TAGS = {"predict", "bake", "slow", "solution", "skip-ci"}

KNOWN_ENVS = {"E0", "E1", "E2"}

# Front matter keys. `title`, `question` and `pin` are required on every lesson,
# because a lesson with no question is a chapter and a lesson with no pin is a
# lesson nobody can check.
REQUIRED_FRONT_MATTER = {"id", "title", "question", "part", "pin"}
KNOWN_FRONT_MATTER = REQUIRED_FRONT_MATTER | {
    "blueprints",
    "requires",
    "reviews",
    "flags",
    "terms",
    "status",
}

# Caps from the authoring guide. Enforced here rather than in review, because a cap
# that only a reviewer enforces is a cap that slips on the week everyone is busy.
CAP_HOOK_WORDS = 150
CAP_TOUR_WORDS = 1500
CAP_LESSON_WORDS = 2500
CAP_E0_CELLS = 24
CAP_NEW_FLAGS = 3
CAP_NEW_TERMS = 4
CAP_PREDICT_GATES = 5

NBFORMAT = 4
NBFORMAT_MINOR = 5


class LessonError(Exception):
    """A problem in a lesson source, reported with the file and line it came from."""


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def _scalar(raw: str) -> object:
    """Turn a front matter scalar into a Python value."""
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


def parse_front_matter(lines: list[str], path: pathlib.Path) -> tuple[dict, int]:
    """Parse the `# ---` fenced header. Returns the mapping and the line it ends on.

    The accepted subset is a flat mapping of scalars, `key: [a, b, c]` lists, and one
    level of nested mapping written with two space indentation. Anything else raises,
    with the line number, rather than being quietly dropped.
    """
    if not lines or lines[0].rstrip() != FRONT_MATTER_FENCE:
        raise LessonError(f"{path}:1: a lesson starts with a '{FRONT_MATTER_FENCE}' front matter fence")

    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == FRONT_MATTER_FENCE:
            end = i
            break
    if end is None:
        raise LessonError(f"{path}:1: front matter fence is never closed")

    data: dict = {}
    current_key: str | None = None
    for offset in range(1, end):
        lineno = offset + 1
        raw = lines[offset]
        if not raw.startswith("#"):
            raise LessonError(f"{path}:{lineno}: front matter lines start with '#'")
        body = raw[1:].rstrip()
        if not body.strip():
            continue

        indented = body.startswith("  ")
        stripped = body.strip()
        if ":" not in stripped:
            raise LessonError(f"{path}:{lineno}: front matter line has no ':'")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if indented:
            if current_key is None or not isinstance(data.get(current_key), dict):
                raise LessonError(f"{path}:{lineno}: indented line with no mapping above it")
            data[current_key][key] = _scalar(value)
            continue

        if value == "":
            data[key] = {}
            current_key = key
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_scalar(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            data[key] = _scalar(value)
        current_key = key

    return data, end


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


class Cell:
    def __init__(self, cell_type: str, directives: dict, source: str, lineno: int):
        self.cell_type = cell_type
        self.directives = directives
        self.source = source
        self.lineno = lineno

    @property
    def name(self) -> str | None:
        return self.directives.get("id")

    @property
    def tags(self) -> list[str]:
        return list(self.directives.get("tags", []))

    @property
    def env(self) -> str:
        return self.directives.get("env", "E0")

    def digest(self, salt: int = 0) -> str:
        """A content hash over everything that decides what the cell renders as.

        The salt only ever moves off zero when two cells in one lesson are byte for
        byte identical, which nbformat forbids because cell ids must be unique.
        """
        parts = [
            self.cell_type,
            json.dumps(self.directives, sort_keys=True, separators=(",", ":")),
            self.source,
        ]
        if salt:
            parts.append(f"#{salt}")
        blob = "\n".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]


def parse_directives(rest: str, path: pathlib.Path, lineno: int) -> tuple[str, dict]:
    """Split a `# %%` marker line into a cell type and its directives."""
    cell_type = "code"
    match = CELL_TYPE.match(rest)
    if match:
        cell_type = match.group("type")
        rest = rest[match.end() :]

    directives: dict = {}
    consumed = 0
    for found in DIRECTIVE.finditer(rest):
        key = found.group("key")
        value = found.group("value")
        if key not in KNOWN_DIRECTIVES:
            raise LessonError(
                f"{path}:{lineno}: unknown directive {key!r}, "
                f"the directives are {sorted(KNOWN_DIRECTIVES)}"
            )
        if key in directives:
            raise LessonError(f"{path}:{lineno}: directive {key!r} given twice")
        if value.startswith("["):
            items = [p.strip() for p in value[1:-1].split(",") if p.strip()]
            directives[key] = items
        else:
            directives[key] = value
        consumed += len(found.group(0))

    leftover = DIRECTIVE.sub("", rest).strip()
    if leftover:
        raise LessonError(f"{path}:{lineno}: cannot read {leftover!r} on the cell marker line")

    if "tags" in directives:
        if not isinstance(directives["tags"], list):
            raise LessonError(f"{path}:{lineno}: tags is a list, write tags=[bake]")
        for tag in directives["tags"]:
            if tag not in KNOWN_TAGS:
                raise LessonError(
                    f"{path}:{lineno}: unknown tag {tag!r}, the tags are {sorted(KNOWN_TAGS)}"
                )
    if "env" in directives and directives["env"] not in KNOWN_ENVS:
        raise LessonError(
            f"{path}:{lineno}: env is one of {sorted(KNOWN_ENVS)}, got {directives['env']!r}"
        )
    if "id" in directives and not re.fullmatch(r"[a-z0-9_]+", str(directives["id"])):
        raise LessonError(
            f"{path}:{lineno}: a cell id is lower case letters, digits and underscores, "
            f"got {directives['id']!r}"
        )

    return cell_type, directives


def parse_cells(lines: list[str], start: int, path: pathlib.Path) -> list[Cell]:
    """Split the body of a lesson source into cells."""
    cells: list[Cell] = []
    pending: list[str] | None = None
    cell_type = "code"
    directives: dict = {}
    lineno = start + 1

    def flush() -> None:
        if pending is None:
            return
        source = "\n".join(pending).strip("\n")
        if cell_type in {"markdown", "raw"}:
            source = strip_comment_prefix(source)
        cells.append(Cell(cell_type, directives, source, lineno))

    for i in range(start + 1, len(lines)):
        raw = lines[i]
        match = CELL_START.match(raw)
        if match:
            flush()
            cell_type, directives = parse_directives(match.group("rest"), path, i + 1)
            pending = []
            lineno = i + 1
            continue
        if pending is not None:
            pending.append(raw)

    flush()
    return [c for c in cells if c.source.strip()]


def strip_comment_prefix(source: str) -> str:
    """Turn a commented markdown cell back into markdown.

    Percent format writes markdown as `# ` prefixed comments so the file stays valid
    Python and so ruff, black and a Python language server all work on a lesson.
    """
    out = []
    for line in source.split("\n"):
        if line.startswith("# "):
            out.append(line[2:])
        elif line == "#":
            out.append("")
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


# ---------------------------------------------------------------------------
# The lesson
# ---------------------------------------------------------------------------


class Lesson:
    def __init__(self, path: pathlib.Path, front: dict, cells: list[Cell]):
        self.path = path
        self.front = front
        self.cells = cells

    @property
    def id(self) -> str:
        return str(self.front["id"])

    @property
    def dir(self) -> pathlib.Path:
        return self.path.parent

    def baked_path(self, cell: Cell) -> pathlib.Path:
        return self.dir / "baked" / f"{cell.name}.json"


def load_lesson(path: pathlib.Path) -> Lesson:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    front, end = parse_front_matter(lines, path)

    missing = REQUIRED_FRONT_MATTER - set(front)
    if missing:
        raise LessonError(f"{path}:1: front matter is missing {sorted(missing)}")
    unknown = set(front) - KNOWN_FRONT_MATTER
    if unknown:
        raise LessonError(
            f"{path}:1: unknown front matter keys {sorted(unknown)}, "
            f"the keys are {sorted(KNOWN_FRONT_MATTER)}"
        )
    if front["id"] != path.parent.name:
        raise LessonError(
            f"{path}:1: front matter id is {front['id']!r} but the directory is "
            f"{path.parent.name!r}, and they have to agree"
        )

    cells = parse_cells(lines, end, path)
    if not cells:
        raise LessonError(f"{path}:1: no cells")
    return Lesson(path, front, cells)


def load_all(root: pathlib.Path) -> list[Lesson]:
    lessons_dir = root / "lessons"
    if not lessons_dir.is_dir():
        return []
    out = []
    for source in sorted(lessons_dir.glob("*/lesson.py")):
        out.append(load_lesson(source))
    return out


# ---------------------------------------------------------------------------
# Notebook generation
# ---------------------------------------------------------------------------


def load_baked(lesson: Lesson, cell: Cell) -> list:
    """Read a baked cell's recorded output.

    A missing recording is not an error while a probe is still outstanding, because
    the alternative is inventing an output, and an invented output in a project whose
    first rule is that nothing is asserted the reader cannot watch happen would be
    the worst possible thing to ship. The notebook gets a visible placeholder saying
    the recording has not been made yet.
    """
    path = lesson.baked_path(cell)
    if not path.is_file():
        return [
            {
                "output_type": "display_data",
                "metadata": {"jvx": {"baked": "pending"}},
                "data": {
                    "text/plain": [
                        "[no recording yet]\n",
                        f"This cell is tagged bake and its output comes from "
                        f"{path.relative_to(lesson.dir.parent.parent)}, which has not been "
                        "recorded. Run the cell yourself and the real output is what you get.\n",
                    ]
                },
            }
        ]
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(recorded, list):
        raise LessonError(f"{path}: a baked recording is a JSON list of nbformat outputs")
    return recorded


def cell_to_json(lesson: Lesson, cell: Cell, cell_id: str) -> dict:
    metadata: dict = {}
    if cell.name:
        metadata["jvx_id"] = cell.name
    if cell.directives.get("env"):
        metadata["jvx_env"] = cell.env
    if cell.directives.get("replay"):
        metadata["jvx_replay"] = cell.directives["replay"]
    if cell.tags:
        metadata["tags"] = cell.tags

    source = cell.source.split("\n")
    source = [line + "\n" for line in source[:-1]] + [source[-1]]

    out: dict = {
        "cell_type": cell.cell_type,
        "id": cell_id,
        "metadata": metadata,
        "source": source,
    }
    if cell.cell_type == "code":
        # No execution_count, ever. A notebook that records the order somebody
        # happened to run cells in is a notebook that carries state nobody can see.
        out["execution_count"] = None
        out["outputs"] = load_baked(lesson, cell) if "bake" in cell.tags else []
    return out


def build_notebook(lesson: Lesson) -> str:
    seen: dict[str, int] = {}
    cells = []
    for cell in lesson.cells:
        salt = 0
        digest = cell.digest()
        while digest in seen:
            salt += 1
            digest = cell.digest(salt)
        seen[digest] = 1
        cells.append(cell_to_json(lesson, cell, digest))

    notebook = {
        "cells": cells,
        "metadata": {
            "jvx": {
                "id": lesson.id,
                "title": lesson.front["title"],
                "question": lesson.front["question"],
                "part": lesson.front["part"],
                "pin": lesson.front["pin"],
                "blueprints": lesson.front.get("blueprints", []),
                "generated_by": "tools/build.py",
                "source": f"lessons/{lesson.id}/lesson.py",
            },
            "kernelspec": {
                "display_name": "Java",
                "language": "java",
                "name": "java",
            },
            "language_info": {
                "file_extension": ".jshell",
                "mimetype": "text/x-java-source",
                "name": "java",
            },
        },
        "nbformat": NBFORMAT,
        "nbformat_minor": NBFORMAT_MINOR,
    }
    return json.dumps(notebook, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def notebook_path(root: pathlib.Path, lesson: Lesson) -> pathlib.Path:
    return root / "notebooks" / lesson.id / "lesson.ipynb"


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text) if w])


def check_structure(root: pathlib.Path, lessons: list[Lesson]) -> list[str]:
    problems: list[str] = []
    by_id = {lesson.id: lesson for lesson in lessons}

    for lesson in lessons:
        path = lesson.path

        names: dict[str, int] = {}
        for cell in lesson.cells:
            if not cell.name:
                continue
            if cell.name in names:
                problems.append(
                    f"{path}:{cell.lineno}: cell id {cell.name!r} is already used on line "
                    f"{names[cell.name]}, and a claim citing it could not tell them apart"
                )
            names[cell.name] = cell.lineno

        for cell in lesson.cells:
            if "bake" in cell.tags and not cell.name:
                problems.append(
                    f"{path}:{cell.lineno}: a bake cell needs an id=, because that is the "
                    f"filename its recorded output is read from"
                )
            if "predict" in cell.tags and cell.cell_type != "code":
                problems.append(
                    f"{path}:{cell.lineno}: a predict gate is a code cell, it renders a widget"
                )

        e0_cells = [c for c in lesson.cells if c.cell_type == "code" and c.env == "E0"]
        if len(e0_cells) > CAP_E0_CELLS:
            problems.append(
                f"{path}: {len(e0_cells)} tier 0 code cells, the cap is {CAP_E0_CELLS}. "
                f"This is two lessons"
            )

        gates = [c for c in lesson.cells if "predict" in c.tags]
        if len(gates) > CAP_PREDICT_GATES:
            problems.append(
                f"{path}: {len(gates)} prediction gates, the cap is {CAP_PREDICT_GATES}. "
                f"Past that it reads as a quiz"
            )
        if lesson.front.get("status") != "draft" and not gates:
            problems.append(
                f"{path}: no prediction gate. Every lesson has at least one, because a "
                f"reader who has not committed to an answer does not read the reveal"
            )

        prose = "\n".join(c.source for c in lesson.cells if c.cell_type == "markdown")
        if words(prose) > CAP_LESSON_WORDS:
            problems.append(
                f"{path}: {words(prose)} words of prose, the cap is {CAP_LESSON_WORDS}"
            )

        for block, cap in (("hook", CAP_HOOK_WORDS), ("tour", CAP_TOUR_WORDS)):
            found = [c for c in lesson.cells if c.name == block]
            if found and words(found[0].source) > cap:
                problems.append(
                    f"{path}:{found[0].lineno}: the {block} is {words(found[0].source)} words, "
                    f"the cap is {cap}"
                )

        flags = lesson.front.get("flags", [])
        if len(flags) > CAP_NEW_FLAGS:
            problems.append(
                f"{path}: {len(flags)} new flags introduced, the cap is {CAP_NEW_FLAGS}"
            )
        terms = lesson.front.get("terms", [])
        if len(terms) > CAP_NEW_TERMS:
            problems.append(
                f"{path}: {len(terms)} new terms defined, the cap is {CAP_NEW_TERMS}"
            )

        for needed in lesson.front.get("requires", []):
            if needed not in by_id:
                problems.append(f"{path}: requires {needed!r}, which is not a lesson")

        pin = load_pin(root)
        if pin and lesson.front.get("pin") != pin.get("jdk_tag"):
            problems.append(
                f"{path}: front matter pin is {lesson.front.get('pin')!r} and "
                f"docs/pin.json says {pin.get('jdk_tag')!r}"
            )

        grader = lesson.dir / "grade.py"
        if lesson.front.get("status") != "draft" and not grader.is_file():
            problems.append(f"{lesson.dir}: no grade.py, so the boss fight has no grader")

    problems.extend(check_requires_dag(lessons))
    return problems


def check_requires_dag(lessons: list[Lesson]) -> list[str]:
    """Report every cycle in the requires graph, so a reader always has an order."""
    graph = {lesson.id: list(lesson.front.get("requires", [])) for lesson in lessons}
    problems: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = trail[trail.index(node) :] + [node]
            problems.append("requires cycle: " + " -> ".join(cycle))
            return
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt in graph:
                visit(nxt, trail + [node])
        state[node] = 2

    for node in sorted(graph):
        visit(node, [])
    return sorted(set(problems))


def load_pin(root: pathlib.Path) -> dict:
    path = root / "docs" / "pin.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


SCAFFOLD = '''\
# ---
# id: {id}
# title: {title}
# question: Something a reader would actually wonder, phrased as a question
# part: 0
# pin: {pin}
# status: draft
# blueprints: []
# requires: []
# flags: []
# terms: []
# reviews:
#   beginner: null
#   expert: null
# ---

# %% [markdown] id=hook
# Under 150 words, and it contains a surprise the reader can run in the next cell.
# If there is no surprise, this is the wrong lesson or the interesting part has not
# been found yet.

# %% id=bootstrap tags=[bake] env=E0
// Generated by build.py. Do not hand write this cell.
jvx.banner.print()

# %% [markdown] id=tour
# The prose. Where the mechanism lives, what it does, why it is that way. Every claim
# carries a citation and a marker, either {{[JVMS x.y@SE25]}} or
# {{[HOTSPOT path/to/file.cpp:123@{pin}]}}.

# %% id=gate_1 tags=[predict] env=E0
jvx.gate("gate_1")

# %% [markdown] id=what_you_now_know
# Three to six bullets, each a capability rather than a topic.
'''

GRADER = '''\
#!/usr/bin/env python3
"""Grade the boss fight for {id}.

Exits 0 when the reader's artifact is correct and 1 when it is not. The failure
message names the thing that is wrong, because "incorrect" teaches nobody anything.
"""

from __future__ import annotations

import sys


def grade(artifact: str) -> tuple[bool, str]:
    raise NotImplementedError("write the grader before the lesson merges")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: grade.py <artifact>", file=sys.stderr)
        raise SystemExit(2)
    ok, message = grade(sys.argv[1])
    print(message)
    raise SystemExit(0 if ok else 1)
'''


def cmd_new(root: pathlib.Path, lesson_id: str) -> int:
    target = root / "lessons" / lesson_id
    if target.exists():
        print(f"{target} already exists", file=sys.stderr)
        return 1
    (target / "baked").mkdir(parents=True)
    pin = load_pin(root).get("jdk_tag", "unpinned")
    (target / "lesson.py").write_text(
        SCAFFOLD.format(id=lesson_id, title=lesson_id, pin=pin), encoding="utf-8"
    )
    (target / "grade.py").write_text(GRADER.format(id=lesson_id), encoding="utf-8")
    (target / "claims.json").write_text("[]\n", encoding="utf-8")
    print(f"scaffolded {target}")
    print(f"next: write the question in {target / 'lesson.py'}, then build.py notebooks")
    return 0


def cmd_notebooks(root: pathlib.Path) -> int:
    lessons = load_all(root)
    if not lessons:
        print("no lessons yet")
        return 0
    for lesson in lessons:
        path = notebook_path(root, lesson)
        path.parent.mkdir(parents=True, exist_ok=True)
        built = build_notebook(lesson)
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        if before == built:
            print(f"  unchanged  {path.relative_to(root)}")
            continue
        path.write_text(built, encoding="utf-8")
        print(f"  {'updated' if before else 'created'}    {path.relative_to(root)}")
    return 0


def cmd_check(root: pathlib.Path) -> int:
    try:
        lessons = load_all(root)
    except LessonError as err:
        print(str(err))
        print("build.py check: 1 problem", file=sys.stderr)
        return 1

    problems = check_structure(root, lessons)

    for lesson in lessons:
        path = notebook_path(root, lesson)
        built = build_notebook(lesson)
        if not path.is_file():
            problems.append(
                f"{path.relative_to(root)}: not committed. Run build.py notebooks"
            )
            continue
        committed = path.read_text(encoding="utf-8")
        if committed != built:
            problems.append(
                f"{path.relative_to(root)}: differs from a rebuild of "
                f"lessons/{lesson.id}/lesson.py. {describe_drift(committed, built)}. "
                f"Notebooks are output. Edit the lesson source and run build.py notebooks"
            )

    for problem in problems:
        print(problem)
    print(
        f"build.py check: {len(lessons)} lessons, {len(problems)} problems",
        file=sys.stderr,
    )
    return 1 if problems else 0


def describe_drift(committed: str, built: str) -> str:
    """Say what changed, in the smallest terms that are still useful."""
    try:
        a = json.loads(committed)
        b = json.loads(built)
    except json.JSONDecodeError:
        return "the committed file is not valid JSON"

    ids_a = [c.get("id") for c in a.get("cells", [])]
    ids_b = [c.get("id") for c in b.get("cells", [])]
    if ids_a != ids_b:
        gone = [i for i in ids_a if i not in ids_b]
        added = [i for i in ids_b if i not in ids_a]
        return f"cell ids changed, {len(gone)} gone and {len(added)} new"

    for cell_a, cell_b in zip(a.get("cells", []), b.get("cells", [])):
        if cell_a != cell_b:
            name = cell_b.get("metadata", {}).get("jvx_id") or cell_b.get("id")
            return f"cell {name} differs, and its id did not change, so it was edited in place"
    return "the metadata differs"


def cmd_run(root: pathlib.Path, lesson_id: str) -> int:
    lessons = {lesson.id: lesson for lesson in load_all(root)}
    if lesson_id not in lessons:
        print(f"no lesson {lesson_id!r}", file=sys.stderr)
        return 1
    path = notebook_path(root, lessons[lesson_id])
    if not path.is_file():
        print(f"{path} is not built yet, run build.py notebooks", file=sys.stderr)
        return 1
    if shutil.which("jupyter") is None:
        print(
            "jupyter is not on PATH, so there is nothing here that can execute a "
            "notebook. In CI this runs in the Colab parity image. Locally, install "
            "the JJava kernel first",
            file=sys.stderr,
        )
        return 1
    return subprocess.call(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--stdout",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
    )


def cmd_list(root: pathlib.Path) -> int:
    lessons = load_all(root)
    if not lessons:
        print("no lessons yet")
        return 0
    width = max(len(lesson.id) for lesson in lessons)
    for lesson in lessons:
        gates = len([c for c in lesson.cells if "predict" in c.tags])
        baked = len([c for c in lesson.cells if "bake" in c.tags])
        status = lesson.front.get("status", "written")
        print(
            f"{lesson.id:<{width}}  {status:<8}  {len(lesson.cells):>2} cells  "
            f"{gates} gates  {baked} baked  {lesson.front['title']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="build.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("notebooks", help="compile every lesson")
    sub.add_parser("check", help="rebuild in memory and fail on any difference")
    sub.add_parser("list", help="print the lesson index")
    p_new = sub.add_parser("new", help="scaffold a lesson")
    p_new.add_argument("id")
    p_run = sub.add_parser("run", help="execute a lesson")
    p_run.add_argument("id")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent

    try:
        if args.command == "notebooks":
            return cmd_notebooks(root)
        if args.command == "check":
            return cmd_check(root)
        if args.command == "list":
            return cmd_list(root)
        if args.command == "new":
            return cmd_new(root, args.id)
        if args.command == "run":
            return cmd_run(root, args.id)
    except LessonError as err:
        print(str(err), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
