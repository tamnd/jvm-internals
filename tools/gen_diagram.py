#!/usr/bin/env python3
"""Draw the object header from the generated layout, rather than from memory.

There is a diagram of the mark word in a lot of blog posts and most of them are wrong
now, because they were drawn once by a person who read markWord.hpp on the day and the
file moved twice since. A picture is the most convincing wrong thing a page can have:
nobody re-derives a diagram, they just believe it.

So this draws the picture from `docs/generated/markword.json`, which
`tools/gen_markword.py` works out from HotSpot's own header at the pinned tag. When a
field moves, the check in CI fails, the JSON is regenerated, and the picture moves with
it. No step in that chain involves anybody remembering to redraw anything.

Two outputs, from the same numbers:

  docs/generated/markword.svg          what a page or a notebook shows
  docs/generated/markword.excalidraw   the same drawing, editable in excalidraw.com

The excalidraw file exists because a diagram somebody cannot open and adjust is a
diagram that gets replaced with a hand drawn one the first time it is nearly right.
Open it, drag things, but do not fix a bit position there, fix it in the generator.

  python tools/gen_diagram.py           regenerate both files
  python tools/gen_diagram.py --check   regenerate in memory and fail on a difference

There is no network here. This reads the committed JSON and nothing else, which is why
it can run in the offline half of CI.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

LAYOUT = pathlib.Path("docs/generated/markword.json")
SVG = pathlib.Path("docs/generated/markword.svg")
EXCALIDRAW = pathlib.Path("docs/generated/markword.excalidraw")

# Colour never carries meaning on its own anywhere in this project. Every box below is
# labelled in words as well, and the drawing reads the same in greyscale and to a
# reader who cannot separate two of these hues. The colours are here so that the same
# field is the same colour in every lesson, which is a memory aid and nothing more.
# These are excalidraw's own palette values, so the .excalidraw file opens looking
# like the .svg rather than like a stranger.
INK = "#1e1e1e"
MUTED = "#868e96"
PALETTE = {
    "klass": "#a5d8ff",
    "hash": "#b2f2bb",
    "valhalla": "#e9ecef",
    "age": "#ffec99",
    "self_fwd": "#d0bfff",
    "lock": "#ffc9c9",
}
FALLBACK = "#e9ecef"

MARK = "#ced4da"
KLASS_FIELD = "#a5d8ff"
PAYLOAD = "#b2f2bb"
PADDING = "#f1f3f5"

# Panel A, the header in bytes. The example is `class Two { int a; int b; }` because
# that is the object the lesson makes the reader predict, and because it is the
# smallest class that shows both effects at once: the header shrinking by four bytes,
# and four of those bytes going straight back into padding.
BYTE_W = 30
BYTES_SHOWN = 24
A_X = 120

# Panel B, the mark word in bits.
B_X = 40
B_W = 920
BIT_W = B_W / 64.0

WIDTH = 1000
HEIGHT = 560


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class Svg:
    """A very small drawing surface. Nine primitives, and this file uses four of them."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def rect(self, x, y, w, h, fill, stroke=INK, width=1.5, dash=None) -> None:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{extra}/>'
        )

    def text(self, x, y, s, size=13, anchor="middle", fill=INK, weight="normal", mono=False) -> None:
        family = "ui-monospace, SFMono-Regular, Menlo, monospace" if mono else (
            "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
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
            f'aria-label="The HotSpot object header, in bytes and in bits">'
        )
        body = "\n  ".join(self.parts)
        return f"{head}\n  <rect width=\"{WIDTH}\" height=\"{HEIGHT}\" fill=\"#ffffff\"/>\n  {body}\n</svg>\n"


def byte_row(svg: Svg, y: int, label: str, boxes: list[tuple[int, int, str, str]], total: int) -> None:
    """One object, drawn as bytes. Each box is (first byte, length, text, fill)."""
    height = 42
    svg.text(A_X - 14, y + 26, label, size=13, anchor="end", weight="600")
    for start, length, text, fill in boxes:
        x = A_X + start * BYTE_W
        w = length * BYTE_W
        svg.rect(x, y, w, height, fill)
        svg.text(x + w / 2, y + 20, text, size=11)
        svg.text(x + w / 2, y + 34, f"{length} bytes", size=10, fill=MUTED)
    end = A_X + total * BYTE_W
    svg.line(end + 6, y, end + 6, y + height, stroke=INK, width=1.0)
    svg.text(end + 14, y + 26, f"{total} bytes", size=13, anchor="start", weight="600", mono=True)


def build_svg(data: dict) -> str:
    svg = Svg()
    fields = data["fields"]
    tag = data["source"]["tag"]

    # -- panel A, where the header stops ---------------------------------------------
    svg.text(40, 34, "How big is an object", size=18, anchor="start", weight="700")
    svg.text(
        40, 54,
        "class Two { int a; int b; }   the same class, laid out by the same VM, two ways",
        size=12, anchor="start", fill=MUTED, mono=True,
    )

    ruler_y = 74
    for b in range(0, BYTES_SHOWN + 1, 4):
        x = A_X + b * BYTE_W
        svg.line(x, ruler_y, x, ruler_y + 5, stroke=MUTED)
        svg.text(x, ruler_y - 4, str(b), size=10, fill=MUTED, mono=True)
    svg.text(A_X - 14, ruler_y - 4, "byte", size=10, anchor="end", fill=MUTED)

    byte_row(
        svg, 88, "compact",
        [
            (0, 8, "mark word, class pointer inside", MARK),
            (8, 4, "int a", PAYLOAD),
            (12, 4, "int b", PAYLOAD),
        ],
        16,
    )
    byte_row(
        svg, 152, "legacy",
        [
            (0, 8, "mark word", MARK),
            (8, 4, "class pointer", KLASS_FIELD),
            (12, 4, "int a", PAYLOAD),
            (16, 4, "int b", PAYLOAD),
            (20, 4, "padding", PADDING),
        ],
        24,
    )
    svg.text(
        A_X, 224,
        "compact is the default from JDK 27. Turn it off with -XX:-UseCompactObjectHeaders and the",
        size=11, anchor="start", fill=MUTED,
    )
    svg.text(
        A_X, 239,
        "class pointer moves out of the mark word into four bytes of its own. Objects are 8 byte aligned,",
        size=11, anchor="start", fill=MUTED,
    )
    svg.text(
        A_X, 254,
        "so the four bytes saved are not always four bytes kept. Here they are. For Integer they are not.",
        size=11, anchor="start", fill=MUTED,
    )

    # -- panel B, inside the mark word -----------------------------------------------
    svg.text(40, 300, "Inside the mark word", size=18, anchor="start", weight="700")
    svg.text(
        40, 320,
        f"64 bits, with compact headers on, at {tag}",
        size=12, anchor="start", fill=MUTED, mono=True,
    )

    bar_y = 356
    bar_h = 48

    for bit in range(0, 65, 8):
        x = B_X + (64 - bit) * BIT_W
        svg.line(x, bar_y - 12, x, bar_y, stroke=MUTED)
        if bit != 0:
            svg.text(x + 2, bar_y - 16, str(bit - 1), size=10, anchor="start", fill=MUTED, mono=True)
    svg.text(B_X + B_W, bar_y - 16, "0", size=10, anchor="end", fill=MUTED, mono=True)

    leaders: list[dict] = []
    for field in fields:
        high = field["shift"] + field["bits"] - 1
        x = B_X + (63 - high) * BIT_W
        w = field["bits"] * BIT_W
        fill = PALETTE.get(field["name"], FALLBACK)
        svg.rect(x, bar_y, w, bar_h, fill)
        span = (
            f"bits {high}..{field['shift']}" if field["bits"] > 1 else f"bit {field['shift']}"
        )
        if w >= 90:
            svg.text(x + w / 2, bar_y + 22, field["name"], size=13, weight="600", mono=True)
            svg.text(x + w / 2, bar_y + 38, f"{span}, {field['bits']} wide", size=11, fill=MUTED)
        else:
            leaders.append({"field": field, "x": x + w / 2, "span": span})

    # Everything too narrow to write inside gets a line pointing at it. They are all at
    # the low end of the word, so the lines run left into the space under the wide
    # fields, one depth each. Leftmost box gets the shallowest depth, which is what
    # keeps the horizontal runs from crossing anybody else's vertical.
    leaders.sort(key=lambda leader: leader["x"])
    text_right = B_X + B_W * 0.72
    for index, leader in enumerate(leaders):
        y = bar_y + bar_h + 18 + index * 20
        svg.line(leader["x"], bar_y + bar_h, leader["x"], y, stroke=MUTED)
        svg.line(leader["x"], y, text_right + 8, y, stroke=MUTED)
        field = leader["field"]
        svg.text(
            text_right, y + 4,
            f"{field['name']}   {leader['span']}   {field['meaning']}",
            size=11, anchor="end",
        )

    footer = HEIGHT - 22
    svg.line(40, footer - 18, WIDTH - 40, footer - 18, stroke="#dee2e6")
    svg.text(
        40, footer,
        f"drawn by tools/gen_diagram.py from {data['source']['path']} at {tag}",
        size=10, anchor="start", fill=MUTED, mono=True,
    )
    svg.text(
        WIDTH - 40, footer,
        f"sha256 {data['source']['sha256'][:16]}",
        size=10, anchor="end", fill=MUTED, mono=True,
    )
    return svg.render()


# -- the same drawing, as something a person can open and move ------------------------
#
# Excalidraw wants a seed and a version on every element. Both are normally random,
# which would mean this file had a different byte content every time it was generated
# and --check could never pass. They are derived from the element's own index instead,
# so the file is a function of the layout and nothing else.


def element(index: int, kind: str, **kwargs) -> dict:
    base = {
        "id": f"jvx-{index:03d}",
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


def build_excalidraw(data: dict) -> str:
    elements: list[dict] = []

    def box(x, y, w, h, fill):
        elements.append(
            element(len(elements), "rectangle", x=x, y=y, width=w, height=h, backgroundColor=fill)
        )

    def label(x, y, text, size=13, align="center", color=INK):
        elements.append(
            element(
                len(elements), "text", x=x, y=y,
                width=max(len(text) * size * 0.55, 10), height=size + 5,
                strokeColor=color, text=text, originalText=text,
                fontSize=size, fontFamily=2, textAlign=align,
                verticalAlign="top", baseline=size, containerId=None, lineHeight=1.25,
            )
        )

    label(40, 20, "How big is an object", size=20, align="left")
    rows = [
        (88, "compact", [(0, 8, "mark word, class pointer inside", MARK),
                         (8, 4, "int a", PAYLOAD), (12, 4, "int b", PAYLOAD)], 16),
        (152, "legacy", [(0, 8, "mark word", MARK), (8, 4, "class pointer", KLASS_FIELD),
                         (12, 4, "int a", PAYLOAD), (16, 4, "int b", PAYLOAD),
                         (20, 4, "padding", PADDING)], 24),
    ]
    for y, name, boxes, total in rows:
        label(40, y + 12, name, align="left")
        for start, length, text, fill in boxes:
            box(A_X + start * BYTE_W, y, length * BYTE_W, 42, fill)
            label(A_X + start * BYTE_W + 6, y + 12, text, size=11, align="left")
        label(A_X + total * BYTE_W + 12, y + 12, f"{total} bytes", align="left")

    label(40, 290, "Inside the mark word", size=20, align="left")
    bar_y = 340
    for field in data["fields"]:
        high = field["shift"] + field["bits"] - 1
        x = B_X + (63 - high) * BIT_W
        w = field["bits"] * BIT_W
        box(x, bar_y, w, 48, PALETTE.get(field["name"], FALLBACK))
    # Every field is named in the list below the bar, not just the wide ones, because
    # in an editable drawing the reader may have moved the boxes around.
    y = bar_y + 70
    for field in reversed(data["fields"]):
        high = field["shift"] + field["bits"] - 1
        span = (
            f"bits {high}..{field['shift']}" if field["bits"] > 1 else f"bit {field['shift']}"
        )
        label(B_X, y, f"{field['name']}   {span}   {field['meaning']}", size=11, align="left")
        y += 20

    document = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/tamnd/jvm-internals",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if a committed file is stale")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    layout = root / LAYOUT
    if not layout.is_file():
        print(f"{LAYOUT} is missing. Run tools/gen_markword.py first.", file=sys.stderr)
        return 1
    data = json.loads(layout.read_text(encoding="utf-8"))

    outputs = {SVG: build_svg(data), EXCALIDRAW: build_excalidraw(data)}

    if args.check:
        stale = []
        for path, text in outputs.items():
            target = root / path
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(path)
        if stale:
            for path in stale:
                print(f"{path} does not match the committed layout.", file=sys.stderr)
            print("Run tools/gen_diagram.py and commit the result.", file=sys.stderr)
            return 1
        print(f"the diagrams match {LAYOUT} at {data['source']['tag']}")
        return 0

    for path, text in outputs.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
