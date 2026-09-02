#!/usr/bin/env python3
"""Tests for the malformed class file table and picture.

The table is the file a lesson author reads instead of running the probe, and the picture
is the one a reader looks at instead of reading the table, so both have to say what the
results files say. The tests below are split the same way as the capability ones: what the
generator does with the numbers, what the numbers themselves have to look like, and
whether the committed copies are stale.

  python tools/test_gen_malformed_table.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_malformed_table as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict[str, dict]:
    files = sorted((ROOT / gen.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheTable(unittest.TestCase):
    def setUp(self):
        self.results = results()
        self.text = gen.build_table(self.results)

    def test_every_case_has_a_row(self):
        for case in gen.CASES:
            self.assertIn(gen.TITLE[case], self.text, case)

    def test_every_case_the_probe_measured_is_one_the_table_knows(self):
        # The one that stops a seventh malformation being added to the probe and then
        # quietly not appearing here.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sorted(data["cases"]), sorted(gen.CASES))

    def test_every_environment_has_a_column(self):
        for name in self.results:
            self.assertIn(name, self.text)

    def test_the_stage_names_are_spelled_out(self):
        # `link` on its own means nothing to a reader on their first pass.
        self.assertIn("link, the verifier", self.text)
        self.assertIn("run, resolution", self.text)

    def test_a_disagreement_is_named_and_not_averaged(self):
        made_up = json.loads(json.dumps(self.results))
        first, second = sorted(made_up)[:2]
        made_up[first]["cases"]["final_and_abstract"]["stage"] = "run"
        agreed, answer = gen.agree(made_up, "final_and_abstract", "stage")
        self.assertFalse(agreed)
        self.assertIn(first, answer)
        self.assertIn(second, answer)

    def test_a_run_that_sometimes_dies_reports_both_halves(self):
        made_up = json.loads(json.dumps(self.results))
        first = sorted(made_up)[0]
        made_up[first]["loaded_unverified"]["final_and_abstract"] = {
            "runs": 7,
            "the VM died: SIGSEGV": 3,
        }
        said = gen.crashes(made_up, "final_and_abstract")[first]
        self.assertIn("3 of 10 died with SIGSEGV", said)
        self.assertIn("7 ran", said)

    def test_the_launcher_prefix_is_not_mistaken_for_the_throwable(self):
        # java prints `Error: LinkageError occurred ...`, and reporting that as `Error`
        # loses the only word in the line worth printing.
        self.assertEqual(
            gen.shorten("Error: LinkageError occurred while loading main class Foo"),
            "LinkageError",
        )
        self.assertEqual(
            gen.shorten('Exception in thread "main" java.lang.NoSuchMethodError: x'),
            "java.lang.NoSuchMethodError",
        )

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

    def test_each_row_stops_in_the_lane_the_results_name(self):
        # The picture is the part most people will only ever look at, so this checks the
        # marker sits under the gate the measurement says caught the file, rather than
        # trusting that the drawing code and the table read the same field.
        svg = gen.build_svg(self.results)
        for row, case in enumerate(gen.CASES):
            stage = gen.stage_of(self.results, case)
            index = gen.STAGES.index(stage)
            x = gen.LANE_X + index * gen.LANE_W + (gen.LANE_W - 12) / 2 - 9
            y = gen.TOP + row * gen.ROW_H - 9
            with self.subTest(case=case):
                self.assertIn(f'<rect x="{x:.1f}" y="{y:.1f}"', svg)

    def test_every_stage_the_results_use_has_a_gate_drawn(self):
        for case in gen.CASES:
            self.assertIn(gen.stage_of(self.results, case), gen.STAGES, case)

    def test_the_excalidraw_file_is_the_same_bytes_every_time(self):
        # Excalidraw fills in a random seed per element unless one is given, and a picture
        # that differs from itself would make `--check` fail on a clean tree.
        self.assertEqual(
            gen.build_excalidraw(self.results), gen.build_excalidraw(results())
        )

    def test_the_excalidraw_file_is_a_drawing_excalidraw_will_open(self):
        document = json.loads(gen.build_excalidraw(self.results))
        self.assertEqual(document["type"], "excalidraw")
        self.assertTrue(document["elements"])
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

    def test_the_class_file_version_the_probe_saw_matches_the_pin(self):
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(
                    int(data["probe"]["probe.class_file_major"]),
                    pin["jdk_class_file_major"],
                )

    def test_every_case_was_actually_loaded(self):
        # A case with no stage is a case the Java half failed to build, and a table full
        # of blanks that still passes is worse than a probe that fails loudly.
        for name, data in self.results.items():
            for case, facts in data["cases"].items():
                with self.subTest(environment=name, case=case):
                    self.assertIn(facts.get("stage"), gen.STAGES)
                    self.assertTrue(facts.get("error"), "no throwable recorded")

    def test_each_case_was_run_ten_times_without_the_verifier(self):
        for name, data in self.results.items():
            for case, outcomes in data["loaded_unverified"].items():
                with self.subTest(environment=name, case=case):
                    self.assertEqual(sum(outcomes.values()), 10)

    def test_the_old_ways_to_turn_the_verifier_off_are_recorded_as_gone(self):
        # B11 is going to tell a reader to type one of these, and the two that every blog
        # post names have been removed. If a later JDK brings them back this test says so.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertIn("rejected", data["verifier_off"]["-Xverify:none"])
                self.assertIn("rejected", data["verifier_off"]["-noverify"])

    def test_no_address_or_thread_id_reached_a_committed_file(self):
        # Crash reports are full of numbers that change every run. One that got into a
        # results file would make the file differ from itself on the next measurement.
        for name, data in self.results.items():
            for case, outcomes in data["loaded_unverified"].items():
                for outcome in outcomes:
                    with self.subTest(environment=name, case=case):
                        self.assertNotIn("0x", outcome, outcome)
                        self.assertNotIn("pid=", outcome, outcome)


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
                    f"{path} is stale, run tools/gen_malformed_table.py",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
