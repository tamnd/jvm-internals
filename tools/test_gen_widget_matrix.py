#!/usr/bin/env python3
"""Tests for the widget delivery table and grid.

The grid is a page of green boxes, and a page of green boxes is exactly the kind of picture
somebody believes without checking. So most of what is below is about the one way it could
be wrong: a measurement the generator does not recognise being coloured as if it passed.

  python tools/test_gen_widget_matrix.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_widget_matrix as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict[str, dict]:
    files = sorted((ROOT / gen.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheVerdicts(unittest.TestCase):
    def setUp(self):
        self.results = results()

    def test_every_phrase_in_every_results_file_has_a_verdict(self):
        # The important one. The probe writes prose, the picture colours it, and a phrase
        # nobody taught the generator is the way a red box turns green by accident.
        for name, data in self.results.items():
            for technique, places in data["techniques"].items():
                for place in gen.PLACES:
                    with self.subTest(environment=name, technique=technique, place=place):
                        self.assertIn(gen.verdict(places[place]),
                                      ("works", "weakened", "blocked"))

    def test_an_unknown_phrase_stops_the_build(self):
        with self.assertRaises(SystemExit):
            gen.verdict("something nobody has measured yet")

    def test_a_downgraded_mime_type_is_not_a_pass(self):
        self.assertEqual(gen.verdict("downgraded to text/plain"), "weakened")

    def test_nothing_is_coloured_green_that_the_probe_called_a_failure(self):
        for phrase, state in gen.VERDICT.items():
            if any(word in phrase for word in
                   ("dropped", "stripped", "blocked", "removed", "disabled", "gone")):
                with self.subTest(phrase=phrase):
                    self.assertNotEqual(state, "works")


class TestTheTable(unittest.TestCase):
    def setUp(self):
        self.results = results()
        self.text = gen.build_table(self.results)

    def test_every_technique_has_a_row(self):
        for technique in gen.TECHNIQUES:
            self.assertIn(gen.TITLE[technique], self.text, technique)

    def test_every_technique_the_probe_measured_is_one_the_table_knows(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sorted(data["techniques"]), sorted(gen.TECHNIQUES))

    def test_the_count_of_what_survives_a_saved_notebook_is_right(self):
        survives = sum(
            1
            for technique in gen.TECHNIQUES
            if gen.verdict(gen.agree(self.results, technique, "lab_saved")[1]) == "works"
        )
        self.assertIn(
            f"{survives} of the {len(gen.TECHNIQUES)} techniques come through", self.text
        )

    def test_the_versions_that_decide_the_answer_are_named(self):
        # This measurement is a property of a front end version, not of an operating
        # system, so a table without the version on it is a table with no shelf life.
        versions = next(iter(self.results.values()))["versions"]
        for tool in ["jjava", "jupyterlab", "nbconvert"]:
            self.assertIn(versions[tool], self.text, tool)

    def test_a_disagreement_is_named_and_not_averaged(self):
        made_up = json.loads(json.dumps(self.results))
        first, second = sorted(made_up)[:2]
        made_up[first]["techniques"]["html_details"]["lab_saved"] = "gone"
        agreed, answer = gen.agree(made_up, "html_details", "lab_saved")
        self.assertFalse(agreed)
        self.assertIn(first, answer)
        self.assertIn(second, answer)

    def test_no_machine_is_identified(self):
        for path in [gen.TABLE, gen.SVG, gen.EXCALIDRAW]:
            text = (ROOT / path).read_text(encoding="utf-8")
            for word in ["Users", "/root", "/home/", "AppData", "Temp", "@"]:
                self.assertNotIn(word, text, f"{path} mentions {word}")

    def test_it_is_deterministic(self):
        self.assertEqual(self.text, gen.build_table(results()))


class TestThePicture(unittest.TestCase):
    def setUp(self):
        self.results = results()

    def test_every_cell_is_drawn_in_the_colour_its_verdict_asks_for(self):
        svg = gen.build_svg(self.results)
        for row, technique in enumerate(gen.TECHNIQUES):
            for index, place in enumerate(gen.PLACES):
                state = gen.verdict(gen.agree(self.results, technique, place)[1])
                x = gen.GRID_X + index * gen.COL_W
                y = gen.TOP + row * gen.ROW_H - gen.CELL_H / 2
                with self.subTest(technique=technique, place=place):
                    self.assertIn(
                        f'<rect x="{x:.1f}" y="{y:.1f}" width="{gen.CELL_W:.1f}" '
                        f'height="{gen.CELL_H:.1f}" rx="4" fill="{gen.COLOUR[state]}"',
                        svg,
                    )

    def test_the_unmeasured_column_is_drawn_as_unmeasured(self):
        # Colab is the environment this project is for, and leaving its column out
        # entirely would let a reader forget it was never asked.
        svg = gen.build_svg(self.results)
        self.assertIn("Colab", svg)
        self.assertIn("not measured", svg)

    def test_nothing_is_drawn_past_the_bottom_of_the_canvas(self):
        # A caption that falls off the bottom is invisible in the rendered picture and
        # perfectly present in the file, so it survives every check except looking.
        svg = gen.build_svg(self.results)
        for part in svg.splitlines():
            for chunk in re.findall(r'\by="([\d.]+)"', part):
                self.assertLess(float(chunk), gen.HEIGHT, part[:80])

    def test_the_excalidraw_file_is_the_same_bytes_every_time(self):
        self.assertEqual(
            gen.build_excalidraw(self.results), gen.build_excalidraw(results())
        )

    def test_the_excalidraw_file_is_a_drawing_excalidraw_will_open(self):
        document = json.loads(gen.build_excalidraw(self.results))
        self.assertEqual(document["type"], "excalidraw")
        ids = [element["id"] for element in document["elements"]]
        self.assertEqual(len(ids), len(set(ids)), "two elements share an id")


class TestTheMeasurements(unittest.TestCase):
    """Not tests of the generator, tests of the results it is generated from."""

    def setUp(self):
        self.results = results()

    def test_more_than_one_environment_was_measured(self):
        self.assertGreaterEqual(len(self.results), 2, "one column is not a comparison")

    def test_every_environment_is_the_pinned_jdk(self):
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["java_build"], pin["jdk_build"])

    def test_every_environment_used_the_same_front_end_versions(self):
        # A grid whose columns were measured against two JupyterLab versions is two
        # measurements printed as one.
        versions = {name: json.dumps(d["versions"], sort_keys=True)
                    for name, d in self.results.items()}
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_the_kernel_emitted_every_payload(self):
        # If this ever fails the answer stops being about front ends and starts being
        # about the kernel, which is a different report.
        for name, data in self.results.items():
            for technique, places in data["techniques"].items():
                with self.subTest(environment=name, technique=technique):
                    self.assertEqual(places["kernel"], "emitted")

    def test_the_display_id_echo_was_counted(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertGreaterEqual(data["display_ids_echoed"], 0)


class TestCommitted(unittest.TestCase):
    def test_the_committed_files_match_the_results(self):
        data = results()
        for path, wanted in [
            (gen.TABLE, gen.build_table(data)),
            (gen.SVG, gen.build_svg(data)),
            (gen.EXCALIDRAW, gen.build_excalidraw(data)),
        ]:
            with self.subTest(path=str(path)):
                self.assertTrue((ROOT / path).is_file(), f"{path} is missing")
                self.assertEqual(
                    (ROOT / path).read_text(encoding="utf-8"),
                    wanted,
                    f"{path} is stale, run tools/gen_widget_matrix.py",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
