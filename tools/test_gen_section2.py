#!/usr/bin/env python3
"""Tests for the object header blueprint's section 2.

This section is the M0 exit criterion, and what it claims is a memory layout somebody
will read instead of attaching to a VM. Getting a layout subtly wrong is worse than not
having one, so most of these tests doctor an input into a shape that would produce a
document that looks right, and check that the generator refuses to write it: a mark word
with a hole in it, a field outside the struct that declares it, a subclass whose fields
start inside its superclass, a type the milestone names that the build does not have.

The rest check the things a reader would act on: that every named type is there, that a
type nobody listed is printed rather than dropped, that a superclass is never reported as
a gap, that the configuration the layouts depend on is stated rather than assumed, and
that nothing here identifies the machines any of it was measured on.

  python tools/test_gen_section2.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_section2 as page  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sources() -> dict:
    """The real inputs, read by absolute path so the tests run from any directory."""
    types, read_by = page.gen_sa_types.agreed({
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ROOT / page.TYPES).glob("*.json"))
    })
    return {
        "types": types,
        "read_by": read_by,
        "capability": {
            p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((ROOT / page.CAPABILITY).glob("*.json"))
        },
        "mark": json.loads((ROOT / page.MARKWORD).read_text(encoding="utf-8")),
        "pin": json.loads((ROOT / page.PIN).read_text(encoding="utf-8")),
    }


class TestTheSection(unittest.TestCase):
    def setUp(self):
        self.sources = sources()
        self.text = page.build(**self.sources)

    def test_every_type_the_milestone_names_has_a_subsection(self):
        for name in page.ORDER:
            with self.subTest(type=name):
                self.assertRegex(self.text, rf"### 2\.3\.\d+ {name}\n")

    def test_a_type_nobody_listed_is_printed_rather_than_dropped(self):
        # A type added to the probe and not to ORDER would be measured and never printed,
        # which is the quietest way to lose a measurement.
        made_up = copy.deepcopy(self.sources)
        made_up["types"]["ArrayKlass"] = copy.deepcopy(made_up["types"]["Klass"])
        made_up["types"]["ArrayKlass"]["super"] = "Klass"
        made_up["types"]["ArrayKlass"]["size"] = 500
        for field in made_up["types"]["ArrayKlass"]["fields"]:
            if field["offset"] is not None:
                field["offset"] += 200
        self.assertIn("ArrayKlass", page.build(**made_up))

    def test_the_mark_word_fields_tile_the_word(self):
        mark = self.sources["mark"]
        self.assertEqual(sum(f["bits"] for f in mark["fields"]), mark["word_bits"])

    def test_a_mark_word_with_a_hole_in_it_stops_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["mark"]["fields"][2]["shift"] += 1
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("does not tile", str(stopped.exception))

    def test_mark_word_fields_that_do_not_fill_the_word_stop_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["mark"]["fields"] = made_up["mark"]["fields"][:-1]
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("bit word", str(stopped.exception))

    def test_a_field_outside_its_struct_stops_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["types"]["Klass"]["size"] = 100
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("cannot be true", str(stopped.exception))

    def test_two_fields_at_one_offset_stop_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["types"]["Klass"]["fields"][1]["offset"] = \
            made_up["types"]["Klass"]["fields"][0]["offset"]
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("same offset", str(stopped.exception))

    def test_a_subclass_that_starts_inside_its_superclass_stops_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["types"]["InstanceKlass"]["fields"][0]["offset"] = 8
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("starts inside it", str(stopped.exception))

    def test_a_type_the_milestone_names_and_the_build_lacks_stops_the_section(self):
        made_up = copy.deepcopy(self.sources)
        made_up["types"]["markWord"] = None
        with self.assertRaises(SystemExit) as stopped:
            page.build(**made_up)
        self.assertIn("markWord", str(stopped.exception))

    def test_a_superclass_is_not_reported_as_a_gap(self):
        # InstanceKlass's own fields start at Klass's size. Measuring from 0 would print
        # 200 bytes of missing struct that is not missing at all.
        rows = [line for line in self.text.splitlines()
                if line.startswith("| `InstanceKlass` | 0 |")]
        self.assertEqual(rows, [])

    def test_the_gaps_are_real_distances_between_exported_fields(self):
        klass = self.sources["types"]["Klass"]
        offsets = sorted(f["offset"] for f in page.instance_fields(klass))
        for start, end in page.gaps(klass, None):
            with self.subTest(gap=(start, end)):
                self.assertGreater(end - start, 8)
                self.assertIn(start, offsets)

    def test_a_type_with_no_exported_fields_is_said_rather_than_dropped(self):
        self.assertIn("### 2.3.1 markWord", self.text)
        self.assertIn("not one of its fields is", self.text)

    def test_every_setting_the_layouts_depend_on_is_stated(self):
        for key, meaning in page.SETTINGS:
            with self.subTest(setting=key):
                self.assertIn(f"| `{key}` |", self.text)
                self.assertIn(meaning, self.text)

    def test_environments_that_disagree_are_both_printed(self):
        # The four capability environments do not all report the same architecture, and a
        # section that collapsed them to one would be a section about one machine.
        made_up = copy.deepcopy(self.sources)
        for name, data in made_up["capability"].items():
            data["answers"]["vm.UseCompactObjectHeaders"] = name.startswith("linux")
        text = page.build(**made_up)
        row = next(line for line in text.splitlines()
                   if line.startswith("| `vm.UseCompactObjectHeaders` |"))
        self.assertIn("`yes` on `linux", row)
        self.assertIn("`no` on ", row)

    def test_booleans_are_words_and_not_python(self):
        self.assertNotIn("`True`", self.text)
        self.assertNotIn("`False`", self.text)

    def test_every_mark_word_row_carries_the_line_it_came_from(self):
        tag = self.sources["pin"]["jdk_tag"]
        for field in self.sources["mark"]["fields"]:
            citation = field["defined_at"]["shift"]
            with self.subTest(field=field["name"]):
                self.assertTrue(citation.endswith(f"@{tag}"), citation)
                self.assertIn(citation, self.text)

    def test_the_compact_header_caveat_survives(self):
        # The one sentence that stops a reader believing the oopDesc struct.
        self.assertIn("separate class pointer", self.text)
        self.assertIn("UseCompactObjectHeaders", self.text)

    def test_the_tables_are_not_ragged(self):
        # Grouped by table rather than by heading, because 2.2 has two of them and a
        # bit table and a lock state table have no reason to be the same width.
        widths: set[int] = set()
        heading = ""
        for line in self.text.splitlines() + [""]:
            if line.startswith("#"):
                heading = line
            if line.startswith("|"):
                widths.add(line.count("|") - line.count("\\|"))
                continue
            self.assertLessEqual(len(widths), 1, f"ragged table under {heading}")
            widths = set()

    def test_no_machine_is_identified(self):
        for word in ("/home/", "/Users/", "hostname"):
            self.assertNotIn(word, self.text, f"{word} in a public page")


class TestCommitted(unittest.TestCase):
    def test_the_committed_section_matches_its_measurements(self):
        path = ROOT / page.PAGE
        self.assertTrue(path.is_file(), f"{path} is missing")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            page.build(**sources()),
            f"{path} is stale, run tools/gen_section2.py",
        )

    def test_the_report_that_explains_it_is_there(self):
        self.assertTrue((ROOT / "docs" / "probes" / "section2.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
