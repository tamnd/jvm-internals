#!/usr/bin/env python3
"""Draw the JShell noise floor from the measured results, not from an impression.

`probes/jshell-noise/run.py` writes a results file per machine. This turns one of them
into a picture, because the finding in that file is a shape and a table of eight rows
hides shapes. Every bar is a multiple of the `compiled` arm, which is a plain `javac`
then `java` run and the closest thing there is to a floor.

Two outputs from the same numbers, the same pair `gen_diagram.py` produces:

  docs/generated/jshell-noise.svg          what a page or a notebook shows
  docs/generated/jshell-noise.excalidraw   the same drawing, editable in excalidraw.com

  python tools/gen_noise_chart.py           regenerate both
  python tools/gen_noise_chart.py --check   regenerate in memory and fail on a difference

No network. It reads a committed results file and nothing else.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

RESULTS = pathlib.Path("probes/jshell-noise/results/osx-arm64.json")
SVG = pathlib.Path("docs/generated/jshell-noise.svg")
EXCALIDRAW = pathlib.Path("docs/generated/jshell-noise.excalidraw")

# The workload allocates this many objects on purpose. They are not noise, and leaving
# them in would flatten every bar towards 1 and hide the thing being measured.
WORKLOAD_OBJECTS = 50_000

ARMS = ["compiled", "launcher", "kernel", "kernel-local"]
ARM_LABEL = {
    "compiled": "javac then java",
    "launcher": "java Workload.java",
    "kernel": "jshell, default",
    "kernel-local": "jshell --execution local",
}
COLOUR = {
    "compiled": "#dee2e6",
    "launcher": "#a5d8ff",
    "kernel": "#b2f2bb",
    "kernel-local": "#ffc9c9",
}

INK = "#212529"
MUTED = "#868e96"

WIDTH = 1000
HEIGHT = 712
LABEL_X = 250
BAR_X = 262
BAR_MAX = 690
BAR_H = 20
BAR_GAP = 6
GROUP_TOP = 96
GROUP_H = 138


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def metrics(data: dict) -> list[tuple[str, dict[str, float]]]:
    """The four numbers worth drawing, each as a raw value per arm.

    Background objects rather than total objects, because the workload's own 50,000 are
    the same on every arm and including them turns a factor of fourteen into a factor
    of five and a half.
    """
    arms = data["arms"]
    return [
        (
            "objects in the heap, workload's own excluded",
            {a: arms[a]["heap_dump"]["instances"] - WORKLOAD_OBJECTS for a in ARMS},
        ),
        ("classes loaded", {a: arms[a]["class_load"]["lines"] for a in ARMS}),
        ("methods the JIT compiled", {a: arms[a]["compilation"]["logged"] for a in ARMS}),
        (
            "class metadata, megabytes",
            {a: arms[a]["loader_stats"]["chunk_bytes"] / 1e6 for a in ARMS},
        ),
    ]


class Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def rect(self, x, y, w, h, fill, stroke=INK, width=1.2) -> None:
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
        )

    def text(self, x, y, s, size=13, anchor="middle", fill=INK, weight="normal", mono=False) -> None:
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
            f'aria-label="How much noise each Java substrate adds, as a multiple of a plain run">'
        )
        body = "\n  ".join(self.parts)
        return f'{head}\n  <rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>\n  {body}\n</svg>\n'


def scale(rows: list[tuple[str, dict[str, float]]]) -> float:
    """One shared scale across all four groups, so bars are comparable between them."""
    worst = max(
        values[arm] / values["compiled"] for _, values in rows for arm in ARMS
    )
    return (BAR_MAX - BAR_X) / worst


def label_for(arm: str, value: float, multiple: float) -> str:
    if arm == "compiled":
        shown = f"{value:,.1f}" if value < 100 else f"{value:,.0f}"
        return f"{shown}, the floor"
    # Parentheses rather than spacing, because SVG collapses runs of whitespace and a
    # gap that looks right in the source renders as one space in the picture.
    shown = f"{value:,.1f}" if value < 100 else f"{value:,.0f}"
    return f"{shown} ({multiple:.1f} times)"


def build_svg(data: dict) -> str:
    svg = Svg()
    rows = metrics(data)
    per_unit = scale(rows)

    svg.text(40, 42, "What the substrate brings with it", size=22, anchor="start", weight="600")
    svg.text(
        40,
        66,
        "Same workload, four ways to run it. Bars are multiples of a plain javac then java run.",
        size=13,
        anchor="start",
        fill=MUTED,
    )

    for index, (title, values) in enumerate(rows):
        top = GROUP_TOP + index * GROUP_H
        svg.text(40, top + 2, title, size=14, anchor="start", weight="600")
        for slot, arm in enumerate(ARMS):
            y = top + 18 + slot * (BAR_H + BAR_GAP)
            multiple = values[arm] / values["compiled"]
            width = max(2.0, multiple * per_unit)
            svg.text(LABEL_X, y + 14, ARM_LABEL[arm], size=12, anchor="end", mono=True)
            svg.rect(BAR_X, y, width, BAR_H, COLOUR[arm])
            svg.text(
                BAR_X + width + 10,
                y + 14,
                label_for(arm, values[arm], multiple),
                size=12,
                anchor="start",
                fill=MUTED,
            )

    svg.line(40, HEIGHT - 44, WIDTH - 40, HEIGHT - 44)
    svg.text(
        40,
        HEIGHT - 22,
        f"{data['platform']}, {data['cpus']} cores, java {data['java_build']}, "
        f"pinned to {data['pin']}, measured {data['measured'][:10]}",
        size=11,
        anchor="start",
        fill=MUTED,
        mono=True,
    )
    return svg.render()


def element(index: int, kind: str, **kwargs) -> dict:
    """One excalidraw element, with every random field pinned to the index.

    Excalidraw normally fills seed and versionNonce with random numbers, which would
    make this file different on every run and useless to a diff. They are drawing
    parameters and nothing reads them back, so a counter does the job.
    """
    base = {
        "id": f"noise-{index}",
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


def excalidraw_text(index: int, x: float, y: float, text: str, size: int = 13, align: str = "left") -> dict:
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
        textAlign=align,
        verticalAlign="top",
        containerId=None,
        lineHeight=1.25,
    )


def build_excalidraw(data: dict) -> str:
    rows = metrics(data)
    per_unit = scale(rows)
    elements: list[dict] = []

    elements.append(excalidraw_text(len(elements), 40, 42, "What the substrate brings with it", 22))
    elements.append(
        excalidraw_text(
            len(elements),
            40,
            66,
            "Bars are multiples of a plain javac then java run.",
            13,
        )
    )

    for index, (title, values) in enumerate(rows):
        top = GROUP_TOP + index * GROUP_H
        elements.append(excalidraw_text(len(elements), 40, top + 2, title, 14))
        for slot, arm in enumerate(ARMS):
            y = top + 18 + slot * (BAR_H + BAR_GAP)
            multiple = values[arm] / values["compiled"]
            width = max(2.0, multiple * per_unit)
            elements.append(
                excalidraw_text(len(elements), 40, y + 14, ARM_LABEL[arm], 12)
            )
            elements.append(
                element(
                    len(elements),
                    "rectangle",
                    x=BAR_X,
                    y=y,
                    width=width,
                    height=BAR_H,
                    backgroundColor=COLOUR[arm],
                )
            )
            elements.append(
                excalidraw_text(
                    len(elements),
                    BAR_X + width + 10,
                    y + 14,
                    label_for(arm, values[arm], multiple),
                    12,
                )
            )

    elements.append(
        excalidraw_text(
            len(elements),
            40,
            HEIGHT - 22,
            f"{data['platform']}, java {data['java_build']}, pinned to {data['pin']}",
            11,
        )
    )

    document = {
        "type": "excalidraw",
        "version": 2,
        "source": "tools/gen_noise_chart.py",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed files differ")
    args = ap.parse_args(argv)

    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    wanted = {SVG: build_svg(data), EXCALIDRAW: build_excalidraw(data)}

    if args.check:
        for path, text in wanted.items():
            if not path.is_file():
                print(f"{path} is missing, run tools/gen_noise_chart.py", file=sys.stderr)
                return 1
            if path.read_text(encoding="utf-8") != text:
                print(
                    f"{path} does not match {RESULTS}, run tools/gen_noise_chart.py",
                    file=sys.stderr,
                )
                return 1
        print(f"the noise chart matches {RESULTS} at {data['pin']}")
        return 0

    for path, text in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
