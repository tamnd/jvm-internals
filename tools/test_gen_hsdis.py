#!/usr/bin/env python3
"""Tests for the hsdis page.

This page decides whether the JIT lessons can show instructions at all, and half of it is
a licence table somebody may act on. So the things worth testing are that every backend
and every linked library appears, that a backend that failed to build cannot be printed
as a working one, that a licence is never invented for a package whose copyright file
declares none, and that a build against unpinned source stops the page.

  python tools/test_gen_hsdis.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_hsdis as page  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def results() -> dict[str, dict]:
    files = sorted((ROOT / page.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestThePage(unittest.TestCase):
    def setUp(self):
        self.results = results()
        self.text = page.build(self.results)

    def test_every_environment_has_a_row(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_every_measured_backend_is_printed(self):
        for name, data in self.results.items():
            for backend in data.get("backends", {}):
                with self.subTest(environment=name, backend=backend):
                    self.assertIn(f"### {backend} on {name}", self.text)

    def test_a_backend_the_order_does_not_know_about_is_kept(self):
        # A backend added to the probe and not to ORDER would be measured and never
        # printed, which is the quietest way to lose a result.
        made_up = copy.deepcopy(self.results)
        name = next(iter(made_up))
        borrowed = made_up[name]["backends"][page.ORDER[0]]
        made_up[name]["backends"]["fictional"] = copy.deepcopy(borrowed)
        self.assertIn("`fictional`", page.build(made_up))

    def test_both_flags_are_asked_with_no_backend(self):
        for flag, blurb in page.FLAGS:
            self.assertIn(blurb, self.text, flag)

    def test_a_backend_that_did_not_build_is_not_printed_as_working(self):
        made_up = copy.deepcopy(self.results)
        name = next(iter(made_up))
        broken = made_up[name]["backends"][page.ORDER[0]]
        broken["build"] = {"ok": False, "seconds": 4.0, "error": "no"}
        broken.pop("artifact", None)
        broken.pop("with_backend", None)
        text = page.build(made_up)
        row = next(line for line in text.splitlines()
                   if line.startswith(f"| `{name}` | `{page.ORDER[0]}`"))
        self.assertIn("failed", row)
        self.assertIn("not reached", row)
        self.assertNotIn("yes", row)

    def test_every_linked_library_has_a_row(self):
        for data in self.results.values():
            for backend, found in data.get("backends", {}).items():
                made = found.get("artifact")
                if not made:
                    continue
                for library in made["links"]:
                    with self.subTest(backend=backend, library=library):
                        self.assertIn(f"| `{library}` |", self.text)

    def test_a_library_no_package_owns_is_said_rather_than_dropped(self):
        made_up = copy.deepcopy(self.results)
        for data in made_up.values():
            for found in data.get("backends", {}).values():
                made = found.get("artifact")
                if made:
                    made["links"].append("libnobody.so.1")
        text = page.build(made_up)
        self.assertIn("| `libnobody.so.1` | unknown | unknown |", text)

    def test_a_licence_is_never_invented(self):
        # The one thing this table must not do is fill in what everybody knows a package
        # to be. A copyright file that declares nothing parseable prints as that.
        made_up = copy.deepcopy(self.results)
        for data in made_up.values():
            for held in data.get("packages", {}).values():
                if held.get("installed"):
                    held["copyright"] = {"present": True, "names": [], "gpl_versions": [],
                                         "machine_readable": False}
        text = page.build(made_up)
        self.assertIn("no licence named in a parseable form", text)
        self.assertNotIn("GPL-2+", text)

    def test_a_free_form_gpl_notice_is_read_as_the_version_it_offers(self):
        held = {"installed": True, "version": "1",
                "copyright": {"present": True, "names": [], "gpl_versions": ["3"],
                              "machine_readable": False}}
        self.assertEqual(
            page.licence(held), "free form file, offering GPL version 3 or later")

    def test_a_package_with_no_copyright_file_is_said(self):
        held = {"installed": True, "version": "1", "copyright": {"present": False}}
        self.assertEqual(page.licence(held), "no copyright file")

    def test_source_that_is_not_the_pinned_commit_stops_the_page(self):
        made_up = copy.deepcopy(self.results)
        name = next(iter(made_up))
        made_up[name]["source"]["commit_matches_pin"] = False
        with self.assertRaises(SystemExit) as stopped:
            page.build(made_up)
        self.assertIn("pin.json", str(stopped.exception))

    def test_the_tables_are_not_ragged(self):
        widths: dict[str, set[int]] = {}
        heading = ""
        for line in self.text.splitlines():
            if line.startswith("#"):
                heading = line
            if line.startswith("|"):
                widths.setdefault(heading, set()).add(line.count("|") - line.count("\\|"))
        for where, found in widths.items():
            with self.subTest(section=where):
                self.assertEqual(len(found), 1, f"ragged table under {where}")

    def test_booleans_are_words_and_not_python(self):
        self.assertNotIn("| True |", self.text)
        self.assertNotIn("| False |", self.text)

    def test_no_machine_is_identified(self):
        # The results carry paths and sample output straight from the tools, which is
        # where a username or a home directory would arrive from.
        for word in ("/home/", "/Users/", "hostname"):
            self.assertNotIn(word, self.text, f"{word} in a public page")

    def test_the_distribution_warning_is_quoted_rather_than_summarised(self):
        for data in self.results.values():
            note = data.get("hsdis", {}).get("distribution_note")
            if note:
                self.assertIn(note, self.text)
                break
        else:
            self.fail("no results file recorded the README's distribution note")


class TestCommitted(unittest.TestCase):
    def test_the_committed_page_matches_the_results(self):
        path = ROOT / page.PAGE
        self.assertTrue(path.is_file(), f"{path} is missing")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            page.build(results()),
            f"{path} is stale, run tools/gen_hsdis.py",
        )

    def test_the_report_that_explains_it_is_there(self):
        self.assertTrue((ROOT / "docs" / "probes" / "hsdis.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
