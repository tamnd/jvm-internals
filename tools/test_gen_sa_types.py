#!/usr/bin/env python3
"""Tests for the type database page.

This page is the one a lesson author will read instead of attaching to a VM, and half of
it is a permission matrix that decides whether `bpc` can work on a reader's machine at
all. So the things worth testing are that every route and every environment appears, that
a route that was refused cannot be printed as working, that a disagreement between two
machines stops the page rather than being averaged into it, and that nothing here needs
the machines the results came from.

  python tools/test_gen_sa_types.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_sa_types as page  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict[str, dict]:
    files = sorted((ROOT / page.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestThePage(unittest.TestCase):
    def setUp(self):
        self.results = results()
        self.text = page.build(self.results)

    def test_every_environment_has_a_column(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_every_route_has_a_row(self):
        for route, blurb in page.ROUTES:
            self.assertIn(blurb, self.text, route)

    def test_a_route_that_was_refused_is_not_printed_as_working(self):
        # The row and the reason are written from the same results file by two different
        # pieces of code, so this is the one that catches them drifting apart.
        for name, data in self.results.items():
            for route, _ in page.ROUTES:
                if data["routes"].get(route, {}).get("worked") is False:
                    with self.subTest(environment=name, route=route):
                        self.assertIn(f"- `{route}` on `{name}`:", self.text)

    def test_every_measured_route_is_a_route_the_page_knows_about(self):
        # A route added to the probe and not to ROUTES would be measured and never
        # printed, which is the quietest way to lose a result.
        known = {route for route, _ in page.ROUTES}
        for name, data in self.results.items():
            self.assertEqual(set(data["routes"]) - known, set(), name)

    def test_the_types_are_printed_once_and_in_reading_order(self):
        found = [n for n in page.ORDER if f"### {n}\n" in self.text]
        self.assertEqual(found, page.ORDER)
        self.assertEqual(self.text.count("### oopDesc\n"), 1)

    def test_every_field_of_every_type_has_a_row(self):
        types, _ = page.agreed(self.results)
        for name, type_ in types.items():
            if type_ is None:
                continue
            for field in type_["fields"]:
                with self.subTest(type=name, field=field["name"]):
                    self.assertIn(f"| `{field['name']}` | `{field['type']}` |", self.text)

    def test_an_offset_is_never_invented_for_a_static_field(self):
        # A static field has an address rather than an offset. The probe records that as
        # null and this has to print it as a word, because a 0 in an offset column is a
        # number somebody will subtract something from.
        made_up = copy.deepcopy(self.results)
        name = next(n for n, d in made_up.items() if d.get("types"))
        for other in made_up.values():
            if other.get("types"):
                other["types"]["Klass"]["fields"].append(
                    {"name": "_madeup", "type": "int", "static": True, "offset": None})
        self.assertIn("| static | `_madeup` | `int` |", page.build(made_up))
        self.assertTrue(made_up[name])

    def test_two_machines_that_disagree_stop_the_page(self):
        made_up = copy.deepcopy(self.results)
        reading = [n for n, d in made_up.items() if d.get("types")]
        self.assertGreater(len(reading), 1, "this test needs two environments that read")
        made_up[reading[1]]["types"]["oopDesc"]["size"] = 24
        with self.assertRaises(SystemExit) as stopped:
            page.build(made_up)
        self.assertIn("oopDesc", str(stopped.exception))
        self.assertIn("16", str(stopped.exception))

    def test_a_type_this_build_does_not_have_is_said_rather_than_dropped(self):
        made_up = copy.deepcopy(self.results)
        for data in made_up.values():
            if data.get("types"):
                data["types"]["markWord"] = None
        text = page.build(made_up)
        self.assertIn("### markWord", text)
        self.assertIn("Not in the type database of this build.", text)

    def test_a_pipe_in_a_core_pattern_does_not_become_a_column(self):
        # One of the measured machines pipes cores to apport, so its core_pattern starts
        # with a pipe. Unescaped, it turns that row into a table with the wrong shape.
        widths = set()
        for line in self.text.splitlines():
            if line.startswith("| `linux") or line.startswith("| name |"):
                widths.add(line.count("|") - line.count("\\|"))
        self.assertEqual(len(widths), 1, "the environment table has ragged rows")

    def test_booleans_are_words_and_not_python(self):
        self.assertNotIn("| True |", self.text)
        self.assertNotIn("| False |", self.text)

    def test_a_route_nobody_asked_is_not_reported_as_a_refusal(self):
        self.assertEqual(page.yes_no(None), "not asked")

    def test_no_machine_is_identified(self):
        # The results carry error messages straight from the tools, which is where a
        # username or a home directory would arrive from.
        for word in ("/home/", "/Users/", "@", "hostname"):
            self.assertNotIn(word, self.text, f"{word} in a public page")


class TestCommitted(unittest.TestCase):
    def test_the_committed_page_matches_the_results(self):
        path = ROOT / page.PAGE
        self.assertTrue(path.is_file(), f"{path} is missing")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            page.build(results()),
            f"{path} is stale, run tools/gen_sa_types.py",
        )

    def test_the_report_that_explains_it_is_there(self):
        self.assertTrue((ROOT / "docs" / "probes" / "sa-types.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
