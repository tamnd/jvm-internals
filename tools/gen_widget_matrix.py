#!/usr/bin/env python3
"""Turn the widget delivery results into a table and a picture.

`probes/widgets/run.py` asks twelve ways of putting something on the screen how far each
one gets in four places. This makes the table and the grid that go with the report.

  docs/generated/widget-delivery.md          the table
  docs/generated/widget-delivery.svg         the grid
  docs/generated/widget-delivery.excalidraw  the grid, editable

  python tools/gen_widget_matrix.py           regenerate all three
  python tools/gen_widget_matrix.py --check   fail if the committed files differ

No network. It reads committed results and nothing else.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

RESULTS = pathlib.Path("probes/widgets/results")
TABLE = pathlib.Path("docs/generated/widget-delivery.md")
SVG = pathlib.Path("docs/generated/widget-delivery.svg")
EXCALIDRAW = pathlib.Path("docs/generated/widget-delivery.excalidraw")

# The order a widget author reaches for them, plainest first.
TECHNIQUES = [
    "html_plain",
    "html_inline_style",
    "html_style_tag",
    "html_details",
    "html_checked_css",
    "html_inline_script",
    "html_onclick",
    "html_iframe_srcdoc",
    "html_img_data_uri",
    "markdown",
    "svg_mime",
    "javascript_mime",
]

TITLE = {
    "html_plain": "plain HTML with an id on it",
    "html_inline_style": "a style attribute on the element",
    "html_style_tag": "a style tag with a rule in it",
    "html_details": "a details and summary pair",
    "html_checked_css": "a radio button styled by :checked",
    "html_inline_script": "an inline script tag",
    "html_onclick": "an onclick attribute",
    "html_iframe_srcdoc": "an iframe with srcdoc",
    "html_img_data_uri": "an img with an SVG data URI",
    "markdown": "text/markdown output",
    "svg_mime": "image/svg+xml output",
    "javascript_mime": "application/javascript output",
}

SHORT = {
    "html_plain": "html, with an id",
    "html_inline_style": "style attribute",
    "html_style_tag": "style tag",
    "html_details": "details/summary",
    "html_checked_css": "radio and :checked",
    "html_inline_script": "script tag",
    "html_onclick": "onclick attribute",
    "html_iframe_srcdoc": "iframe srcdoc",
    "html_img_data_uri": "img data URI",
    "markdown": "markdown output",
    "svg_mime": "svg output",
    "javascript_mime": "javascript output",
}

# The four places, in the order a reader meets them.
PLACES = ["kernel", "nbconvert", "lab_saved", "lab_live"]
PLACE_TITLE = {
    "kernel": "the kernel",
    "nbconvert": "static HTML",
    "lab_saved": "Lab, unrun",
    "lab_live": "Lab, live",
}
PLACE_BLURB = {
    "kernel": "does it emit",
    "nbconvert": "the built site",
    "lab_saved": "before you run",
    "lab_live": "after you run",
}

# Every phrase the probe can write, sorted into three outcomes. Unknown phrases are an
# error rather than a default, because a new measurement quietly coloured green is the one
# way this picture could lie.
VERDICT = {
    "emitted": "works",
    "renders, id kept": "works",
    "renders, id dropped": "blocked",
    "style attribute kept": "works",
    "style attribute dropped": "blocked",
    "css applied": "works",
    "style tag dropped": "blocked",
    "opens and closes": "works",
    "present but will not open": "blocked",
    "clicks and highlights": "works",
    "clicks, css dropped": "weakened",
    "input disabled": "blocked",
    "script ran": "works",
    "script blocked": "blocked",
    "onclick ran": "works",
    "onclick stripped": "blocked",
    "iframe kept": "works",
    "iframe removed": "blocked",
    "image renders": "works",
    "image broken": "blocked",
    "rendered as markdown": "works",
    "shown as text": "blocked",
    "drawn inline": "works",
    "drawn as an image": "works",
    "shown as source text": "weakened",
    "executed": "works",
    "not executed": "blocked",
    "gone": "blocked",
    "nothing came back": "blocked",
    "no output": "blocked",
    "no cell": "blocked",
}

COLOUR = {"works": "#b2f2bb", "weakened": "#ffd8a8", "blocked": "#ffc9c9"}
MARK = {"works": "yes", "weakened": "part", "blocked": "no"}

INK = "#212529"
MUTED = "#868e96"

WIDTH = 1000
HEIGHT = 680
GRID_X = 300
COL_W = 130
TOP = 160
ROW_H = 34
CELL_W = 110
CELL_H = 24


def results() -> dict[str, dict]:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}")
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def verdict(phrase: str) -> str:
    # Two phrases carry a measured name in them, the MIME type an output was downgraded to
    # and the attribute an id was renamed into, so they are matched by their opening words.
    if phrase.startswith("downgraded to") or phrase.startswith("renders, id moved to"):
        return "weakened"
    if phrase not in VERDICT:
        sys.exit(f"tools/gen_widget_matrix.py has no verdict for {phrase!r}")
    return VERDICT[phrase]


def agree(data: dict[str, dict], technique: str, place: str) -> tuple[bool, str]:
    """One answer if every environment gave it, and a named disagreement otherwise."""
    seen = {name: d["techniques"][technique][place] for name, d in data.items()}
    values = set(seen.values())
    if len(values) == 1:
        return True, values.pop()
    return False, "; ".join(f"{name}: {value}" for name, value in sorted(seen.items()))


def build_table(data: dict[str, dict]) -> str:
    lines: list[str] = []
    lines.append("# What a Java kernel can put on the screen, and where it survives")
    lines.append("")
    lines.append(
        "Generated by `tools/gen_widget_matrix.py` from `probes/widgets/results`. "
        "Do not edit."
    )
    lines.append("")
    names = ", ".join(f"`{name}`" for name in sorted(data))
    builds = sorted({d["java_build"] for d in data.values()})
    measured = sorted({d["measured"] for d in data.values()})
    versions = next(iter(data.values()))["versions"]
    lines.append(
        f"Measured on {names}, java {', '.join(builds)}, jjava {versions['jjava']}, "
        f"jupyterlab {versions['jupyterlab']}, nbconvert {versions['nbconvert']}, "
        f"on {', '.join(measured)}."
    )
    lines.append("")
    lines.append(
        "`Lab, unrun` is a saved notebook the reader has opened and not executed, which is "
        "what anybody who clicks a link gets. `Lab, live` is the same output a second after "
        "the kernel produced it. Colab has no column here and it is the one that matters "
        "most, because it cannot be measured over SSH. It arrives with issue #1."
    )
    lines.append("")

    header = " | ".join(PLACE_TITLE[place] for place in PLACES)
    lines.append(f"| technique | {header} |")
    lines.append("|---" * (len(PLACES) + 1) + "|")
    for technique in TECHNIQUES:
        cells = []
        for place in PLACES:
            _, answer = agree(data, technique, place)
            cells.append(answer)
        lines.append(f"| {TITLE[technique]} | {' | '.join(cells)} |")
    lines.append("")

    survives = [
        technique
        for technique in TECHNIQUES
        if verdict(agree(data, technique, "lab_saved")[1]) == "works"
    ]
    lines.append(
        f"{len(survives)} of the {len(TECHNIQUES)} techniques come through a saved "
        f"notebook intact: "
        + ", ".join(SHORT[technique] for technique in survives)
        + "."
    )
    lines.append("")

    echoed = sorted({d["display_ids_echoed"] for d in data.values()})
    lines.append(
        f"Every `display` call also printed the id it assigned, on {', '.join(str(n) for n in echoed)} "
        f"of {len(TECHNIQUES)} cells, so a reader sees a line of hex under each widget "
        "until `jvx` swallows it."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def rect(self, x, y, w, h, fill, stroke=INK, width=1.0, dash=None) -> None:
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
            f'aria-label="Which ways of drawing a widget survive which notebook front end">'
        )
        body = "\n  ".join(self.parts)
        return (
            f'{head}\n  <rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>\n'
            f'  {body}\n</svg>\n'
        )


def build_svg(data: dict[str, dict]) -> str:
    svg = Svg()
    svg.text(40, 42, "What survives, and where", size=22, anchor="start", weight="600")
    svg.text(
        40, 66,
        "Twelve ways to draw a widget from a Java kernel, and how much of each one is "
        "left in four places.",
        size=13, anchor="start", fill=MUTED,
    )

    columns = PLACES + ["colab"]
    for index, place in enumerate(columns):
        x = GRID_X + index * COL_W
        title = PLACE_TITLE.get(place, "Colab")
        blurb = PLACE_BLURB.get(place, "not measured")
        svg.rect(x, TOP - 62, CELL_W, 40, "#f1f3f5",
                 dash="4 3" if place == "colab" else None)
        svg.text(x + CELL_W / 2, TOP - 44, title, size=12, weight="600")
        svg.text(x + CELL_W / 2, TOP - 30, blurb, size=10, fill=MUTED)

    for row, technique in enumerate(TECHNIQUES):
        y = TOP + row * ROW_H
        svg.text(GRID_X - 20, y + 4, SHORT[technique], size=11, anchor="end", mono=True)
        for index, place in enumerate(columns):
            x = GRID_X + index * COL_W
            if place == "colab":
                svg.rect(x, y - CELL_H / 2, CELL_W, CELL_H, "#ffffff", stroke=MUTED,
                         dash="4 3")
                svg.text(x + CELL_W / 2, y + 4, "?", size=12, fill=MUTED)
                continue
            _, answer = agree(data, technique, place)
            state = verdict(answer)
            svg.rect(x, y - CELL_H / 2, CELL_W, CELL_H, COLOUR[state])
            svg.text(x + CELL_W / 2, y + 4, MARK[state], size=11)

    bottom = TOP + len(TECHNIQUES) * ROW_H + 20
    svg.line(40, bottom, WIDTH - 40, bottom)
    survives = sum(
        1
        for technique in TECHNIQUES
        if verdict(agree(data, technique, "lab_saved")[1]) == "works"
    )
    svg.text(
        40, bottom + 24,
        f"A saved notebook nobody has run keeps {survives} of the {len(TECHNIQUES)}. "
        "No script, no style tag, no ids, and every form control arrives disabled.",
        size=12, anchor="start",
    )
    svg.text(
        40, bottom + 44,
        "So the widget has to be legible before it is interactive, and details and summary "
        "is the only interaction that always works.",
        size=12, anchor="start",
    )
    builds = sorted({d["java_build"] for d in data.values()})
    versions = next(iter(data.values()))["versions"]
    svg.text(
        40, bottom + 66,
        f"{', '.join(sorted(data))}, java {', '.join(builds)}, jjava {versions['jjava']}, "
        f"jupyterlab {versions['jupyterlab']}, measured "
        f"{sorted({d['measured'] for d in data.values()})[0]}",
        size=10, anchor="start", fill=MUTED, mono=True,
    )
    return svg.render()


def element(index: int, kind: str, **kwargs) -> dict:
    """One excalidraw element with every random field pinned, so the file is stable."""
    base = {
        "id": f"widget-{index}",
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
        "seed": 3000 + index,
        "version": 1,
        "versionNonce": 4000 + index,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
    }
    base.update(kwargs)
    return base


def excalidraw_text(index: int, x: float, y: float, text: str, size: int = 12) -> dict:
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
        excalidraw_text(len(elements), 40, 42, "What survives, and where", 22)
    )
    for index, place in enumerate(PLACES):
        x = GRID_X + index * COL_W
        elements.append(
            element(len(elements), "rectangle", x=x, y=TOP - 62, width=CELL_W,
                    height=40, backgroundColor="#f1f3f5")
        )
        elements.append(
            excalidraw_text(len(elements), x + 8, TOP - 36, PLACE_TITLE[place], 12)
        )
    for row, technique in enumerate(TECHNIQUES):
        y = TOP + row * ROW_H
        elements.append(excalidraw_text(len(elements), 40, y + 4, SHORT[technique], 11))
        for index, place in enumerate(PLACES):
            x = GRID_X + index * COL_W
            _, answer = agree(data, technique, place)
            state = verdict(answer)
            elements.append(
                element(len(elements), "rectangle", x=x, y=y - CELL_H / 2, width=CELL_W,
                        height=CELL_H, backgroundColor=COLOUR[state])
            )
            elements.append(
                excalidraw_text(len(elements), x + 44, y + 4, MARK[state], 11)
            )
    document = {
        "type": "excalidraw",
        "version": 2,
        "source": "tools/gen_widget_matrix.py",
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
                print(f"{path} is missing, run tools/gen_widget_matrix.py",
                      file=sys.stderr)
                return 1
            if path.read_text(encoding="utf-8") != text:
                print(f"{path} does not match {RESULTS}, run tools/gen_widget_matrix.py",
                      file=sys.stderr)
                return 1
        print(f"the widget delivery table and picture match {RESULTS}")
        return 0

    for path, text in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
