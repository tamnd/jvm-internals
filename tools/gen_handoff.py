#!/usr/bin/env python3
"""Build the notebooks that get handed to a person, from sources a person can read.

Five probes in M0 cannot be run from here. Four of them need a browser signed in to a
Google account (#1, #6, #11, #4) and one needs quiet hardware rather than a shared box
at load 30 (#9). The answer is not to guess at the results, and it is not to leave the
issues open with no way forward. It is to write the measurement down as something
somebody can run in one sitting, and to make the output machine readable so that what
comes back is a results file rather than a story about what happened.

This builds the four Colab notebooks. `probes/jcstress/run.py` is the fifth and needs no
build step because a script is already runnable.

  python tools/gen_handoff.py            rewrite the notebooks
  python tools/gen_handoff.py --check    fail if a notebook is stale

The sources are `probes/<name>/colab.py` in the same `# %%` percent format the lessons
use, for the same reason: a pull request that changes a measurement should show the
change, and a diff of `.ipynb` JSON shows nothing anybody can review. The protocol for
running them is `docs/probes/handoff.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

NBFORMAT = 4
NBFORMAT_MINOR = 5

# The four that need a browser. The issue number is carried into the notebook metadata
# so that a results file pasted back three weeks from now still says what it answers.
HANDOFFS = {
    "bootstrap": {
        "issue": 1,
        "title": "Can a Colab runtime become a pinned JDK 27 kernel in 90 seconds",
    },
    "colabbuild": {
        "issue": 6,
        "title": "Does an OpenJDK build fit in a free Colab session",
    },
    "capability": {
        "issue": 11,
        "title": "What a free Colab runtime can actually do",
    },
    "widgets": {
        "issue": 4,
        "title": "Do the widget payloads survive Colab's output sandbox",
    },
}

MARKER = re.compile(r"^# %%(?P<rest>.*)$")


def source(name: str) -> pathlib.Path:
    return ROOT / "probes" / name / "colab.py"


def output(name: str) -> pathlib.Path:
    return ROOT / "probes" / name / "colab.ipynb"


def parse(text: str, path: pathlib.Path) -> list[tuple[str, str]]:
    """Split a percent format file into `(cell_type, source)` pairs.

    Markdown cells are comment blocks, so the `# ` prefix comes off on the way in. A
    blank comment line is a paragraph break and has no trailing space to strip.
    """
    cells: list[tuple[str, str]] = []
    kind: str | None = None
    body: list[str] = []

    def flush() -> None:
        if kind is None:
            return
        lines = body
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if kind == "markdown":
            lines = [line[2:] if line.startswith("# ") else line.lstrip("#")
                     for line in lines]
        cells.append((kind, "\n".join(lines)))

    for i, line in enumerate(text.splitlines()):
        found = MARKER.match(line)
        if found is None:
            body.append(line)
            continue
        flush()
        rest = found.group("rest").strip()
        if rest not in {"", "[markdown]"}:
            sys.exit(f"{path}:{i + 1}: unknown cell marker '# %% {rest}'")
        kind = "markdown" if rest == "[markdown]" else "code"
        body = []
    flush()

    if not cells:
        sys.exit(f"{path}: no cells, so there would be nothing to run")
    return cells


def digest(kind: str, text: str, salt: int) -> str:
    parts = [kind, text] + ([f"#{salt}"] if salt else [])
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def cell(kind: str, text: str, seen: dict[str, int]) -> dict:
    salt = 0
    found = digest(kind, text, salt)
    while found in seen:
        salt += 1
        found = digest(kind, text, salt)
    seen[found] = 1

    lines = text.split("\n")
    out: dict = {
        "cell_type": kind,
        "id": found,
        "metadata": {},
        "source": [line + "\n" for line in lines[:-1]] + [lines[-1]],
    }
    if kind == "code":
        # No execution_count and no outputs, same rule as the lessons. What comes back
        # from a run is a pasted results file, not a notebook with somebody's session
        # baked into it.
        out["execution_count"] = None
        out["outputs"] = []
    return out


def notebook(name: str) -> str:
    path = source(name)
    if not path.is_file():
        sys.exit(f"{path.relative_to(ROOT)} does not exist")
    seen: dict[str, int] = {}
    cells = [cell(kind, text, seen)
             for kind, text in parse(path.read_text(encoding="utf-8"), path)]

    found = {
        "cells": cells,
        "metadata": {
            "jvx": {
                "handoff": name,
                "issue": HANDOFFS[name]["issue"],
                "title": HANDOFFS[name]["title"],
                "generated_by": "tools/gen_handoff.py",
                "source": f"probes/{name}/colab.py",
                "protocol": "docs/probes/handoff.md",
            },
            # A Python kernel, deliberately, including in the one notebook whose subject
            # is the Java kernel. Installing the Java kernel is the thing being measured
            # there, so the notebook doing the measuring cannot already be running in it.
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
            "colab": {"provenance": []},
        },
        "nbformat": NBFORMAT,
        "nbformat_minor": NBFORMAT_MINOR,
    }
    return json.dumps(found, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if a notebook is not what its source says")
    args = ap.parse_args(argv)

    stale = []
    for name in sorted(HANDOFFS):
        built = notebook(name)
        out = output(name)
        if args.check:
            if not out.is_file() or out.read_text(encoding="utf-8") != built:
                stale.append(str(out.relative_to(ROOT)))
            continue
        out.write_text(built, encoding="utf-8")

    if args.check:
        if stale:
            sys.exit(f"stale, run tools/gen_handoff.py: {', '.join(stale)}")
        print(f"{len(HANDOFFS)} handoff notebooks match their sources")
        return 0
    print(f"wrote {len(HANDOFFS)} handoff notebooks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
