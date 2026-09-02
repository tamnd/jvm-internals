#!/usr/bin/env python3
"""Turn the malformed class file results into a table and a picture.

`probes/classfile-malformed/run.py` writes one JSON file per environment. This makes the
two things a reader wants out of them: a table saying, for each of the six malformations,
whether the API will build it and where the JVM notices, and a drawing of the same six
falling out of the pipeline at four different points.

  docs/generated/malformed-class-files.md   the table
  docs/generated/malformed-class-files.svg  the picture
  docs/generated/malformed-class-files.excalidraw   the picture, editable

  python tools/gen_malformed_table.py           regenerate all three
  python tools/gen_malformed_table.py --check   fail if the committed files differ

No network. It reads committed results and nothing else.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

RESULTS = pathlib.Path("probes/classfile-malformed/results")
TABLE = pathlib.Path("docs/generated/malformed-class-files.md")
SVG = pathlib.Path("docs/generated/malformed-class-files.svg")
EXCALIDRAW = pathlib.Path("docs/generated/malformed-class-files.excalidraw")

# The order the issue lists them in, which is also least to most surprising.
CASES = [
    "pool_index_past_end",
    "method_ref_descriptor_mismatch",
    "stack_map_int_where_reference",
    "final_and_abstract",
    "max_stack_too_small",
    "version_above_the_pin",
]

TITLE = {
    "pool_index_past_end": "a constant pool index past the end of the pool",
    "method_ref_descriptor_mismatch": "a method reference whose descriptor matches nothing",
    "stack_map_int_where_reference": "a stack map frame saying int where a reference lives",
    "final_and_abstract": "a class that is both final and abstract",
    "max_stack_too_small": "a max_stack too small for the body under it",
    "version_above_the_pin": "a major version above what this JDK reads",
}

SHORT = {
    "pool_index_past_end": "pool index past the end",
    "method_ref_descriptor_mismatch": "method ref descriptor",
    "stack_map_int_where_reference": "stack map says int",
    "final_and_abstract": "final and abstract",
    "max_stack_too_small": "max_stack too small",
    "version_above_the_pin": "version from the future",
}

# The four places a class file can stop, in the order the JVM reaches them.
STAGES = ["parse", "link", "run", "accepted"]
STAGE_TITLE = {
    "parse": "parse",
    "link": "link, the verifier",
    "run": "run, resolution",
    "accepted": "it ran",
}
# Short enough to fit inside the box at ten points. A caption that overflows its own
# rectangle is worse than no caption, and the full sentence is in the report anyway.
STAGE_BLURB = {
    "parse": "defineClass",
    "link": "first active use",
    "run": "the instruction runs",
    "accepted": "nobody objected",
}
STAGE_COLOUR = {
    "parse": "#a5d8ff",
    "link": "#b2f2bb",
    "run": "#ffd8a8",
    "accepted": "#ffc9c9",
}

INK = "#212529"
MUTED = "#868e96"

WIDTH = 1000
HEIGHT = 560
LANE_X = 300
LANE_W = 150
TOP = 150
ROW_H = 52


def results() -> dict[str, dict]:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}")
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def agree(data: dict[str, dict], case: str, field: str) -> tuple[bool, str]:
    """One answer if every environment gave it, and a named disagreement otherwise."""
    seen = {name: d["cases"][case].get(field, "") for name, d in data.items()}
    values = set(seen.values())
    if len(values) == 1:
        return True, values.pop()
    return False, "; ".join(f"{name}: {value}" for name, value in sorted(seen.items()))


def crashes(data: dict[str, dict], case: str) -> dict[str, str]:
    """How the unverified run went in each environment, as a short phrase."""
    out = {}
    for name, d in data.items():
        outcomes = d["loaded_unverified"][case]
        total = sum(outcomes.values())
        died = {k: v for k, v in outcomes.items() if k.startswith("the VM died")}
        if died:
            how = ", ".join(k.split(": ", 1)[1] for k in died)
            gone = sum(died.values())
            survived = total - gone
            phrase = f"{gone} of {total} died with {how}"
            # Both halves, always. "3 of 10 died" and "7 of 10 ran" are the same fact and
            # a reader who only sees the first one will believe the other seven crashed.
            out[name] = phrase if survived == 0 else f"{phrase}, {survived} ran"
        elif "runs" in outcomes:
            out[name] = f"ran {outcomes['runs']} of {total} times"
        else:
            only = next(iter(outcomes))
            out[name] = shorten(only)
    return out


def shorten(message: str) -> str:
    """The name of the throwable and nothing else, because the table is wide already.

    The bare word "Error" is skipped on purpose. The launcher prefixes its own messages
    with it, so taking the first match would report `Error` for a class whose real answer
    is `LinkageError`, which is the kind of small wrongness nobody notices in a table.
    """
    for part in message.replace('Exception in thread "main" ', "").split():
        stripped = part.rstrip(":,'")
        if stripped in ("Error", "Exception"):
            continue
        if "Error" in stripped or "Exception" in stripped:
            return stripped
    return message[:60]


def build_table(data: dict[str, dict]) -> str:
    lines: list[str] = []
    lines.append("# Six class files that are wrong on purpose")
    lines.append("")
    lines.append(
        "Generated by `tools/gen_malformed_table.py` from "
        "`probes/classfile-malformed/results`. Do not edit."
    )
    lines.append("")
    names = ", ".join(f"`{name}`" for name in sorted(data))
    build = sorted({d["java_build"] for d in data.values()})
    measured = sorted({d["measured"] for d in data.values()})
    lines.append(
        f"Measured on {names}, java {', '.join(build)}, on {', '.join(measured)}."
    )
    lines.append("")

    lines.append("## Can the API build it, and where does the JVM notice")
    lines.append("")
    lines.append("| malformation | java.lang.classfile | caught at | what it says |")
    lines.append("|---|---|---|---|")
    for case in CASES:
        _, api = agree(data, case, "api")
        _, stage = agree(data, case, "stage")
        _, error = agree(data, case, "error")
        lines.append(
            f"| {TITLE[case]} | {api} | {STAGE_TITLE.get(stage, stage)} "
            f"| `{shorten(error)}` |"
        )
    lines.append("")

    lines.append("## With the verifier turned off")
    lines.append("")
    lines.append(
        "Ten runs of each, with `-XX:-BytecodeVerificationLocal` and "
        "`-XX:-BytecodeVerificationRemote`."
    )
    lines.append("")
    header = " | ".join(sorted(data))
    lines.append(f"| malformation | {header} |")
    lines.append("|---" * (len(data) + 1) + "|")
    for case in CASES:
        row = crashes(data, case)
        cells = " | ".join(row[name] for name in sorted(data))
        lines.append(f"| {TITLE[case]} | {cells} |")
    lines.append("")

    lines.append("## Ways to turn the verifier off")
    lines.append("")
    lines.append("| flag | what happens |")
    lines.append("|---|---|")
    flags = sorted(next(iter(data.values()))["verifier_off"])
    for flag in flags:
        seen = {d["verifier_off"][flag] for d in data.values()}
        answer = seen.pop() if len(seen) == 1 else "; ".join(sorted(seen))
        lines.append(f"| `{flag}` | {answer} |")
    lines.append("")

    lines.append("## Can javap still show the file")
    lines.append("")
    lines.append("| malformation | javap -v |")
    lines.append("|---|---|")
    for case in CASES:
        seen = {d["javap"][case] for d in data.values()}
        answer = seen.pop() if len(seen) == 1 else "; ".join(sorted(seen))
        lines.append(f"| {TITLE[case]} | {answer} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def rect(self, x, y, w, h, fill, stroke=INK, width=1.2, dash=None) -> None:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="4" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{extra}/>'
        )

    def text(self, x, y, s, size=13, anchor="middle", fill=INK, weight="normal",
             mono=False) -> None:
        family = (
            "ui-monospace, SFMono-Regular, Menlo, monospace"
            if mono
            else "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
        )
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(s)}</text>'
        )

    def line(self, x1, y1, x2, y2, stroke=MUTED, width=1.0, dash=None) -> None:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{width}"{extra}/>'
        )

    def render(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
            f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
            f'aria-label="Where each deliberately malformed class file stops">'
        )
        body = "\n  ".join(self.parts)
        return (
            f'{head}\n  <rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>\n'
            f'  {body}\n</svg>\n'
        )


def stage_of(data: dict[str, dict], case: str) -> str:
    _, stage = agree(data, case, "stage")
    return stage


def build_svg(data: dict[str, dict]) -> str:
    svg = Svg()
    svg.text(40, 42, "Where a broken class file stops", size=22, anchor="start",
             weight="600")
    svg.text(
        40, 66,
        "Six malformations, and how far into a running program each one gets.",
        size=13, anchor="start", fill=MUTED,
    )

    # The four gates across the top, each a column the rows travel through.
    for index, stage in enumerate(STAGES):
        x = LANE_X + index * LANE_W
        svg.rect(x, TOP - 66, LANE_W - 12, 44, STAGE_COLOUR[stage])
        svg.text(x + (LANE_W - 12) / 2, TOP - 44, STAGE_TITLE[stage], size=13,
                 weight="600")
        svg.text(x + (LANE_W - 12) / 2, TOP - 28, STAGE_BLURB[stage], size=10, fill=MUTED)

    for row, case in enumerate(CASES):
        y = TOP + row * ROW_H
        stage = stage_of(data, case)
        index = STAGES.index(stage) if stage in STAGES else len(STAGES) - 1
        stop_x = LANE_X + index * LANE_W + (LANE_W - 12) / 2

        svg.text(LANE_X - 20, y + 4, SHORT[case], size=12, anchor="end", mono=True)
        # The journey, solid up to where it died and dotted for the part it never made.
        svg.line(LANE_X, y, stop_x, y, stroke=INK, width=1.6)
        if index < len(STAGES) - 1:
            svg.line(stop_x, y, LANE_X + (len(STAGES) - 1) * LANE_W + (LANE_W - 12) / 2,
                     y, dash="3 4")
        svg.rect(stop_x - 9, y - 9, 18, 18, STAGE_COLOUR[stage])

        _, api = agree(data, case, "api")
        note = "built by the API" if api == "built" else "needs a byte patch"
        svg.text(
            LANE_X + (len(STAGES) - 1) * LANE_W + (LANE_W - 12) / 2 + 22, y + 4, note,
            size=11, anchor="start", fill=MUTED,
        )

    bottom = TOP + len(CASES) * ROW_H + 26
    svg.line(40, bottom, WIDTH - 40, bottom)
    svg.text(
        40, bottom + 24,
        "With the verifier off, five of the six load. The one with a bad pool index "
        "sometimes runs and sometimes kills the VM.",
        size=12, anchor="start",
    )
    builds = sorted({d["java_build"] for d in data.values()})
    svg.text(
        40, bottom + 46,
        f"{', '.join(sorted(data))}, java {', '.join(builds)}, measured "
        f"{sorted({d['measured'] for d in data.values()})[0]}",
        size=11, anchor="start", fill=MUTED, mono=True,
    )
    return svg.render()


def element(index: int, kind: str, **kwargs) -> dict:
    """One excalidraw element with every random field pinned, so the file is stable."""
    base = {
        "id": f"malformed-{index}",
        "type": kind,
        "x": 0,
        "y": 0,
        "width": 0,
        "height": 0,
        "angle": 0,
        "strokeColor": INK,
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 0,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": 1000 + index,
        "version": 1,
        "versionNonce": 2000 + index,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
    }
    base.update(kwargs)
    return base


def excalidraw_text(index: int, x: float, y: float, text: str, size: int = 13) -> dict:
    return element(
        index,
        "text",
        x=x,
        y=y - size,
        width=max(8.0, len(text) * size * 0.55),
        height=size * 1.25,
        text=text,
        originalText=text,
        fontSize=size,
        fontFamily=2,
        textAlign="left",
        verticalAlign="top",
        containerId=None,
        lineHeight=1.25,
    )


def build_excalidraw(data: dict[str, dict]) -> str:
    elements: list[dict] = []
    elements.append(
        excalidraw_text(len(elements), 40, 42, "Where a broken class file stops", 22)
    )
    for index, stage in enumerate(STAGES):
        x = LANE_X + index * LANE_W
        elements.append(
            element(len(elements), "rectangle", x=x, y=TOP - 66, width=LANE_W - 12,
                    height=44, backgroundColor=STAGE_COLOUR[stage])
        )
        elements.append(
            excalidraw_text(len(elements), x + 8, TOP - 40, STAGE_TITLE[stage], 13)
        )
    for row, case in enumerate(CASES):
        y = TOP + row * ROW_H
        stage = stage_of(data, case)
        index = STAGES.index(stage) if stage in STAGES else len(STAGES) - 1
        stop_x = LANE_X + index * LANE_W + (LANE_W - 12) / 2
        elements.append(excalidraw_text(len(elements), 40, y + 4, SHORT[case], 12))
        elements.append(
            element(len(elements), "line", x=LANE_X, y=y, width=stop_x - LANE_X,
                    height=0, points=[[0, 0], [stop_x - LANE_X, 0]])
        )
        elements.append(
            element(len(elements), "rectangle", x=stop_x - 9, y=y - 9, width=18,
                    height=18, backgroundColor=STAGE_COLOUR[stage])
        )
    document = {
        "type": "excalidraw",
        "version": 2,
        "source": "tools/gen_malformed_table.py",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed files differ")
    args = ap.parse_args(argv)

    data = results()
    wanted = {
        TABLE: build_table(data),
        SVG: build_svg(data),
        EXCALIDRAW: build_excalidraw(data),
    }

    if args.check:
        for path, text in wanted.items():
            if not path.is_file():
                print(f"{path} is missing, run tools/gen_malformed_table.py",
                      file=sys.stderr)
                return 1
            if path.read_text(encoding="utf-8") != text:
                print(f"{path} does not match {RESULTS}, run "
                      f"tools/gen_malformed_table.py", file=sys.stderr)
                return 1
        print(f"the malformed class file table and picture match {RESULTS}")
        return 0

    for path, text in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
