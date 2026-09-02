#!/usr/bin/env python3
"""Tests for the capability matrix.

The matrix is the file a lesson author will read instead of running the probe, so the
things worth testing are that it says what the results files say, that it cannot quietly
drop a check, and that it does not carry anything into a public repository that should
not be there.

  python tools/test_gen_capability_matrix.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_capability_matrix as matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict[str, dict]:
    files = sorted((ROOT / matrix.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheTable(unittest.TestCase):
    def setUp(self):
        self.results = results()
        self.text = matrix.build(self.results)

    def test_every_check_has_a_row(self):
        # The one that stops a check being added to the probe and lost here. Every key in
        # every results file has to appear, including one that only one machine answered.
        keys = set().union(*[set(d["answers"]) for d in self.results.values()])
        for key in keys:
            self.assertIn(f"| `{key}` |", self.text, key)

    def test_every_environment_has_a_column(self):
        for name in self.results:
            self.assertIn(name, self.text)

    def test_a_check_in_an_unexpected_group_still_appears(self):
        made_up = json.loads(json.dumps(self.results))
        first = next(iter(made_up))
        made_up[first]["answers"]["telemetry.something_new"] = True
        self.assertIn("| `telemetry.something_new` |", matrix.build(made_up))

    def test_the_agreement_count_is_right(self):
        keys = sorted(set().union(*[set(d["answers"]) for d in self.results.values()]))
        same = sum(
            1 for k in keys
            if len({json.dumps(d["answers"].get(k)) for d in self.results.values()}) == 1
        )
        self.assertIn(f"{same} of the checks give the same answer everywhere", self.text)

    def test_booleans_are_words_and_not_python(self):
        self.assertNotIn("| True |", self.text)
        self.assertNotIn("| False |", self.text)

    def test_a_missing_answer_is_not_reported_as_a_no(self):
        # A check one machine never ran is not a capability that machine lacks, and
        # printing it as "no" would be a measurement this probe did not make.
        self.assertEqual(matrix.show(None), "not asked")

    def test_no_machine_is_identified(self):
        for word in ["Users", "/root", "/home/", "MacBook", "vmi", "gopher"]:
            self.assertNotIn(word, self.text)

    def test_no_process_id_survives_into_the_notes(self):
        # Notes come from tool output, and tool output has pids in it. A pid changes
        # every run, so one that reached this file would make it differ from itself.
        for data in self.results.values():
            for key, note in data["notes"].items():
                for word in note.split():
                    self.assertFalse(
                        word.strip(":,.").isdigit() and len(word.strip(":,.")) >= 4,
                        f"{key} still has a pid in it: {note}",
                    )

    def test_it_is_deterministic(self):
        self.assertEqual(self.text, matrix.build(results()))


class TestTheMeasurements(unittest.TestCase):
    """Not tests of the generator, tests of the results it is generated from."""

    def setUp(self):
        self.results = results()

    def test_more_than_one_environment_was_measured(self):
        self.assertGreaterEqual(len(self.results), 2, "a matrix of one column is a list")

    def test_every_environment_ran_the_in_process_half(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertTrue(data["answers"]["inprocess.ok"], name)

    def test_every_environment_is_the_pinned_jdk(self):
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["java_build"], pin["jdk_build"], name)

    def test_every_environment_answered_the_same_questions(self):
        counts = {name: len(d["answers"]) for name, d in self.results.items()}
        self.assertEqual(len(set(counts.values())), 1, counts)

    def test_the_measured_class_file_version_matches_the_pin(self):
        # The probe asks the JVM rather than trusting the pin file, so this is the check
        # that catches the pin file drifting away from the JDK it names.
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(
                    data["answers"]["vm.class_file_major"], pin["jdk_class_file_major"]
                )


class TestCommitted(unittest.TestCase):
    def test_the_committed_matrix_matches_the_results(self):
        path = ROOT / matrix.MATRIX
        self.assertTrue(path.is_file(), f"{path} is missing")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            matrix.build(results()),
            f"{path} is stale, run tools/gen_capability_matrix.py",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
