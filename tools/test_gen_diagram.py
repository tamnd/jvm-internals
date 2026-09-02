#!/usr/bin/env python3
"""Tests for the diagram generator.

The thing worth testing about a drawing is not that it looks right, because no test can
tell. It is that the numbers in it came from the layout file and not from the author,
and that the same layout draws the same bytes every time. Both are checkable, and both
are the reason the drawing is generated at all.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

import gen_diagram

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAYOUT = json.loads((ROOT / gen_diagram.LAYOUT).read_text(encoding="utf-8"))


class TestSvg(unittest.TestCase):

    def setUp(self) -> None:
        self.svg = gen_diagram.build_svg(LAYOUT)

    def test_every_field_is_named_somewhere_in_the_drawing(self) -> None:
        for field in LAYOUT["fields"]:
            self.assertIn(field["name"], self.svg, f"{field['name']} is not in the drawing")

    def test_a_field_that_moves_moves_the_box(self) -> None:
        moved = json.loads(json.dumps(LAYOUT))
        # Swap the widths of hash and valhalla. Nothing about this is realistic, it just
        # has to be a change the drawing cannot ignore.
        by_name = {f["name"]: f for f in moved["fields"]}
        by_name["valhalla"]["bits"] = 8
        by_name["hash"]["shift"] = 15
        by_name["hash"]["bits"] = 27
        self.assertNotEqual(gen_diagram.build_svg(moved), self.svg)

    def test_the_klass_box_starts_at_the_left_edge(self) -> None:
        # klass is the top 22 bits and the bar reads high bit first, so its box is the
        # first one and it sits on the left edge. If this ever fails, the bar is drawn
        # backwards, which is the single most likely way for the picture to be a lie.
        klass = next(f for f in LAYOUT["fields"] if f["name"] == "klass")
        self.assertEqual(klass["shift"] + klass["bits"], 64)
        # The boxes are emitted in field order, which is lowest bit first, so sort them
        # into the order a person reads them before looking at the leftmost one.
        bar = sorted(
            (float(x), float(w))
            for x, y, w in re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)"', self.svg)
            if y == "356.0"
        )
        self.assertEqual(len(bar), len(LAYOUT["fields"]))
        self.assertEqual(bar[0][0], float(gen_diagram.B_X))
        # The SVG rounds to a tenth of a pixel, so the comparison has to allow for that.
        self.assertAlmostEqual(bar[0][1], klass["bits"] * gen_diagram.BIT_W, delta=0.1)

    def test_the_bar_covers_the_whole_word_with_no_gap(self) -> None:
        rects = re.findall(r'<rect x="([\d.]+)" y="356.0" width="([\d.]+)"', self.svg)
        widths = sum(float(w) for _, w in rects)
        self.assertAlmostEqual(widths, gen_diagram.B_W, places=0)

    def test_the_pinned_tag_is_on_the_picture(self) -> None:
        # A diagram with no version on it is the thing this generator exists to stop.
        self.assertIn(LAYOUT["source"]["tag"], self.svg)
        self.assertIn(LAYOUT["source"]["path"], self.svg)

    def test_it_is_well_formed_xml(self) -> None:
        import xml.etree.ElementTree as ET
        ET.fromstring(self.svg)

    def test_the_same_layout_draws_the_same_bytes(self) -> None:
        self.assertEqual(gen_diagram.build_svg(LAYOUT), self.svg)


class TestExcalidraw(unittest.TestCase):

    def setUp(self) -> None:
        self.doc = json.loads(gen_diagram.build_excalidraw(LAYOUT))

    def test_it_is_a_file_excalidraw_will_open(self) -> None:
        self.assertEqual(self.doc["type"], "excalidraw")
        self.assertEqual(self.doc["version"], 2)
        for element in self.doc["elements"]:
            for required in ("id", "type", "x", "y", "width", "height", "seed", "version"):
                self.assertIn(required, element)

    def test_nothing_in_it_is_random(self) -> None:
        # Excalidraw normally puts a random seed on every element. If any of that leaked
        # in, the file would differ on every run and --check could never pass.
        again = json.loads(gen_diagram.build_excalidraw(LAYOUT))
        self.assertEqual(again, self.doc)

    def test_every_field_is_labelled(self) -> None:
        texts = " ".join(e.get("text", "") for e in self.doc["elements"])
        for field in LAYOUT["fields"]:
            self.assertIn(field["name"], texts)


class TestCommitted(unittest.TestCase):

    def test_the_committed_files_match_the_committed_layout(self) -> None:
        for path, text in (
            (gen_diagram.SVG, gen_diagram.build_svg(LAYOUT)),
            (gen_diagram.EXCALIDRAW, gen_diagram.build_excalidraw(LAYOUT)),
        ):
            target = ROOT / path
            self.assertTrue(target.is_file(), f"{path} is not committed")
            self.assertEqual(
                target.read_text(encoding="utf-8"), text,
                f"{path} is stale, run tools/gen_diagram.py",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
