#!/usr/bin/env python3
"""Tests for the JOL comparison page.

The page exists to answer one question, and the answer is a number: how many classes did
JOL and the VM agree about. So the tests that matter are the ones that make a generator
that is quietly wrong say so. A disagreement that never reaches the page is the failure
mode here, because the page would then read as a clean bill of health, and eleven lessons
would be built on it.

The rest hold the summary line honest against the table beneath it, since those are two
separate pieces of code that can disagree with each other about the same file.

The tests over the real result file are what CI runs.

  python tools/test_gen_fieldlayout.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_fieldlayout as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def one_class(agrees: bool = True, differences=None) -> dict:
    return {
        "agrees": agrees,
        "differences": differences or [],
        "jol_answered": True,
        "jol": {
            "parseClass": {"fields": {"a": 8}, "header_size": 8, "instance_size": 16},
            "parseInstance": {"fields": {"a": 8}, "header_size": 8, "instance_size": 16},
        },
        "vm": {"fields": {"a": 8}, "instance_size": 16},
        "vm_printed": True,
    }


def result(**overrides) -> dict:
    base = {
        "probe": "fieldlayout",
        "issue": 5,
        "agrees_everywhere": True,
        "classes_asked": ["A"],
        "jol": {"version": "0.17"},
        "environment": {
            "java_build": "OpenJDK 64-Bit Server VM (fastdebug build 27-internal)",
            "built_from": {"commit": "815ff4dc327fe17f2433c7d115a5a503af10f3c4"},
        },
        "configurations": {
            "default": {
                "flags_reported": {
                    "UseCompactObjectHeaders": "true", "UseCompressedOops": "true"},
                "runs": {
                    "attach_self": {
                        "classes": {"A": one_class()},
                        "jol": {"details": "# VM mode: 64 bits # Lilliput VM detected"},
                    },
                    "no_attach": {
                        "classes": {"A": one_class()},
                        "jol": {"details": "# VM mode: 64 bits # Lilliput VM detected"},
                    },
                },
            },
        },
    }
    base.update(overrides)
    return base


class TestTheSummary(unittest.TestCase):
    def test_full_agreement_says_so(self):
        text = gen.page({"linux-x64": result()})
        self.assertIn("agreed on every field of every class.", text)

    def test_a_disagreement_reaches_the_summary(self):
        """The failure that would matter: a difference that never leaves the JSON."""
        bad = result(agrees_everywhere=False)
        bad["configurations"]["default"]["runs"]["attach_self"]["classes"]["A"] = \
            one_class(agrees=False, differences=["parseClass has no field flags"])
        text = gen.page({"linux-x64": bad})
        self.assertIn("except `A`", text)
        self.assertIn("parseClass has no field flags", text)

    def test_a_disagreement_in_only_one_run_still_reaches_the_page(self):
        """Agreeing with attach and not without it is a difference, not a wash."""
        bad = result(agrees_everywhere=False)
        bad["configurations"]["default"]["runs"]["no_attach"]["classes"]["A"] = \
            one_class(agrees=False, differences=["offset differs"])
        text = gen.page({"linux-x64": bad})
        self.assertIn("except `A`", text)

    def test_the_counts_in_the_table_match_the_classes(self):
        bad = result(agrees_everywhere=False)
        for name in ("A", "B"):
            bad["configurations"]["default"]["runs"]["attach_self"]["classes"][name] = \
                one_class(agrees=name == "A")
        text = gen.page({"linux-x64": bad})
        self.assertIn("| 1 of 2 |", text)

    def test_the_platform_count_is_not_pluralised_wrongly(self):
        self.assertIn("1 platform:", gen.page({"linux-x64": result()}))
        self.assertIn("2 platforms:",
                      gen.page({"linux-x64": result(), "osx-arm64": result()}))


class TestOrdering(unittest.TestCase):
    def test_known_configurations_come_in_reading_order(self):
        found = result()
        for name in ("heap_above_32g", "no_compact_headers"):
            found["configurations"][name] = \
                copy.deepcopy(found["configurations"]["default"])
        self.assertEqual(gen.configurations(found),
                         ["default", "no_compact_headers", "heap_above_32g"])

    def test_a_configuration_the_page_has_never_heard_of_is_still_printed(self):
        """A new flag combination must not vanish because nobody updated a list."""
        found = result()
        found["configurations"]["something_new"] = \
            copy.deepcopy(found["configurations"]["default"])
        self.assertIn("something_new", gen.configurations(found))
        self.assertIn("something_new", gen.page({"linux-x64": found}))


class TestTheRealResults(unittest.TestCase):
    """What CI runs, over the file the probe actually wrote."""

    def setUp(self):
        self.results = gen.load()
        self.page = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")

    def test_the_committed_page_matches_the_measurements(self):
        self.assertEqual(self.page, gen.page(self.results))

    def test_the_measurement_was_made_on_a_build_of_the_pinned_commit(self):
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        for platform, one in self.results.items():
            with self.subTest(platform=platform):
                self.assertEqual(one["environment"]["built_from"]["commit"],
                                 pin["jdk_tag_commit"])

    def test_the_vm_printed_a_layout_for_every_class(self):
        """Without this the comparison is JOL against nothing, which always agrees."""
        for platform, one in self.results.items():
            for config in one["configurations"].values():
                for name, run in config["runs"].items():
                    for cls, found in run["classes"].items():
                        with self.subTest(platform=platform, run=name, cls=cls):
                            self.assertTrue(found["vm_printed"])

    def test_jol_reports_the_compact_header_as_eight_and_the_legacy_one_as_twelve(self):
        """The claim eleven lessons rest on."""
        for one in self.results.values():
            for name, config in one["configurations"].items():
                compact = config["flags_reported"]["UseCompactObjectHeaders"] == "true"
                for run in config["runs"].values():
                    sizes, _ = gen.header_sizes(run)
                    with self.subTest(configuration=name):
                        self.assertEqual(sizes, {8} if compact else {12})

    def test_the_only_disagreement_is_a_field_java_does_not_declare(self):
        """`flags` is injected by HotSpot, so reflection cannot see it. Not an offset."""
        for one in self.results.values():
            self.assertEqual(sorted(gen.disagreements(one)), ["java.lang.String"])
            for why in gen.disagreements(one)["java.lang.String"]:
                self.assertIn("flags", why)

    def test_every_offset_jol_does_report_is_the_offset_the_vm_printed(self):
        for platform, one in self.results.items():
            for config in one["configurations"].values():
                for run in config["runs"].values():
                    for cls, found in run["classes"].items():
                        vm = found["vm"]["fields"]
                        for field, offset in found["jol"]["parseClass"]["fields"].items():
                            with self.subTest(platform=platform, cls=cls, field=field):
                                self.assertEqual(offset, vm.get(field))


if __name__ == "__main__":
    unittest.main(verbosity=2)
