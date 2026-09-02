#!/usr/bin/env python3
"""Tests for the JShell noise chart.

A chart is a claim, and a chart drawn from a file is a claim that the file said this.
So these check the two ways a generated picture goes wrong: it stops agreeing with its
source, or it stops being reproducible and starts polluting every diff.

  python tools/test_gen_noise_chart.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_noise_chart  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict:
    return json.loads((ROOT / gen_noise_chart.RESULTS).read_text(encoding="utf-8"))


class TestSvg(unittest.TestCase):
    def setUp(self):
        self.data = results()
        self.svg = gen_noise_chart.build_svg(self.data)

    def test_it_is_well_formed_xml(self):
        ET.fromstring(self.svg)

    def test_every_arm_is_named(self):
        for label in gen_noise_chart.ARM_LABEL.values():
            self.assertIn(label, self.svg)

    def test_every_group_is_titled(self):
        for title, _ in gen_noise_chart.metrics(self.data):
            self.assertIn(title, self.svg)

    def test_the_worst_arm_has_the_longest_bar(self):
        # Not a cosmetic check. If the scale is ever computed per group rather than
        # once, a 14 times bar and a 3 times bar come out the same length and the
        # picture says the opposite of the measurement.
        root = ET.fromstring(self.svg)
        bars = [
            r for r in root.iter("{http://www.w3.org/2000/svg}rect")
            if r.get("fill") in gen_noise_chart.COLOUR.values()
        ]
        widest = max(bars, key=lambda r: float(r.get("width")))
        self.assertEqual(widest.get("fill"), gen_noise_chart.COLOUR["kernel-local"])

    def test_the_floor_arm_is_the_shortest_bar_in_every_group(self):
        rows = gen_noise_chart.metrics(self.data)
        for title, values in rows:
            floor = values["compiled"]
            for arm in gen_noise_chart.ARMS:
                self.assertGreaterEqual(values[arm], floor, f"{title} {arm}")

    def test_the_numbers_on_the_picture_come_from_the_results_file(self):
        arms = self.data["arms"]
        widgets = gen_noise_chart.WORKLOAD_OBJECTS
        background = arms["kernel-local"]["heap_dump"]["instances"] - widgets
        self.assertIn(f"{background:,}", self.svg)
        self.assertIn(f"{arms['compiled']['class_load']['lines']:,}", self.svg)

    def test_the_pin_and_the_platform_are_on_the_picture(self):
        self.assertIn(self.data["pin"], self.svg)
        self.assertIn(self.data["platform"], self.svg)

    def test_no_machine_is_identified(self):
        # The results are measured on private hosts. Their names do not belong in a
        # committed picture any more than they belong in the results file.
        for word in ["Users", "/root", "MacBook", "vmi"]:
            self.assertNotIn(word, self.svg)

    def test_it_is_deterministic(self):
        self.assertEqual(self.svg, gen_noise_chart.build_svg(results()))


class TestExcalidraw(unittest.TestCase):
    def setUp(self):
        self.data = results()
        self.document = json.loads(gen_noise_chart.build_excalidraw(self.data))

    def test_it_looks_like_something_excalidraw_will_open(self):
        self.assertEqual(self.document["type"], "excalidraw")
        self.assertEqual(self.document["version"], 2)
        self.assertTrue(self.document["elements"])

    def test_nothing_in_it_is_random(self):
        for index, element in enumerate(self.document["elements"]):
            self.assertEqual(element["seed"], 1000 + index)
            self.assertEqual(element["versionNonce"], 2000 + index)

    def test_every_arm_is_labelled(self):
        text = " ".join(
            e.get("text", "") for e in self.document["elements"] if e["type"] == "text"
        )
        for label in gen_noise_chart.ARM_LABEL.values():
            self.assertIn(label, text)

    def test_there_is_one_bar_per_arm_per_group(self):
        bars = [e for e in self.document["elements"] if e["type"] == "rectangle"]
        wanted = len(gen_noise_chart.ARMS) * len(gen_noise_chart.metrics(self.data))
        self.assertEqual(len(bars), wanted)

    def test_it_is_deterministic(self):
        self.assertEqual(
            json.dumps(self.document, sort_keys=True),
            json.dumps(json.loads(gen_noise_chart.build_excalidraw(results())), sort_keys=True),
        )


class TestCommitted(unittest.TestCase):
    def test_the_committed_files_match_the_results(self):
        data = results()
        for path, text in [
            (ROOT / gen_noise_chart.SVG, gen_noise_chart.build_svg(data)),
            (ROOT / gen_noise_chart.EXCALIDRAW, gen_noise_chart.build_excalidraw(data)),
        ]:
            self.assertTrue(path.is_file(), f"{path} is missing")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                text,
                f"{path} is stale, run tools/gen_noise_chart.py",
            )

    def test_both_platforms_agree_on_the_shape(self):
        """The chart is drawn from one machine, so the other one has to be checked too.

        A finding measured on a single laptop is an anecdote. These two files were
        produced by the same harness on osx-arm64 and linux-x64, and the point of the
        probe is the ordering of the arms, not the exact counts, so that is what is
        asserted here.
        """
        directory = ROOT / "probes" / "jshell-noise" / "results"
        files = sorted(directory.glob("*.json"))
        self.assertGreaterEqual(len(files), 2, "only one machine has been measured")
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            arms = data["arms"]
            floor = arms["compiled"]["heap_dump"]["instances"]
            with self.subTest(platform=data["platform"]):
                self.assertGreater(arms["kernel-local"]["heap_dump"]["instances"], 4 * floor)
                self.assertLess(arms["kernel"]["heap_dump"]["instances"], 1.2 * floor)
                self.assertEqual(arms["kernel"]["compilation"]["visible_on_stdout"], 0)
                self.assertGreater(arms["kernel"]["compilation"]["logged"], 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
