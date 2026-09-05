#!/usr/bin/env python3
"""Tests for the specification section index.

The index is what a `[JVMS]` marker is checked against, so an index that parsed the wrong
thing would make every specification citation in the repository appear verified while
verifying nothing. The tests that matter here run the parser over the shapes Oracle's
pages actually contain: a heading with markup inside the title, a chapter heading that is
not a section, a chapter with no sections at all, and a page whose template changed.

None of these touch the network. The parsing tests feed the functions fragments of the
markup, and the tests over the real index read the committed file. Fetching Oracle seven
times to run a unit test would make the suite slow and would make it fail on a train.

  python tools/test_gen_jvms_index.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_jvms_index as index  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestParsing(unittest.TestCase):
    def test_a_plain_title_comes_through(self):
        self.assertEqual(index.title("2.2.&nbsp;Data Types"), "2.2. Data Types")

    def test_markup_inside_a_title_is_removed_and_the_words_kept(self):
        """`2.1. The class File Format` has a code element in the middle of its name."""
        raw = '2.1.&nbsp;The <code class="literal">class</code> File Format\n     '
        self.assertEqual(index.title(raw), "2.1. The class File Format")

    def test_a_section_heading_gives_its_number_and_title(self):
        self.assertEqual(
            index.numbered("jvms-2.7", "2.7. Representation of Objects"),
            ("2.7", "Representation of Objects"))

    def test_a_deep_section_heading_gives_its_number_and_title(self):
        self.assertEqual(
            index.numbered("jvms-5.4.3.1", "5.4.3.1. Class and Interface Resolution"),
            ("5.4.3.1", "Class and Interface Resolution"))

    def test_a_chapter_heading_is_not_a_section(self):
        self.assertIsNone(index.numbered("jvms-2", "Chapter 2. The Structure"))

    def test_a_heading_whose_text_does_not_match_its_anchor_is_refused(self):
        """A mismatch means the page shape moved, and a wrong title is worse than none."""
        self.assertIsNone(index.numbered("jvms-2.7", "2.8. Something Else"))

    def test_sections_sort_by_number_and_not_by_string(self):
        pairs = [("2.10", "ten"), ("2.9", "nine"), ("2.1", "one")]
        self.assertEqual([p[0] for p in sorted(pairs, key=index.order)],
                         ["2.1", "2.9", "2.10"])


class TestTheCommittedIndex(unittest.TestCase):
    def setUp(self):
        self.index = index.load()
        self.sections = index.sections(self.index)

    def test_the_edition_is_the_one_the_pin_names(self):
        pin = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
        self.assertEqual(self.index["edition"], pin["jvms_edition"])

    def test_every_chapter_the_specification_has_is_here(self):
        self.assertEqual([one["chapter"] for one in self.index["chapters"]],
                         index.CHAPTERS)

    def test_the_sections_this_repository_cites_are_in_it(self):
        """If these two ever stop resolving, the citations in O01 are unchecked."""
        self.assertEqual(self.sections["2.7"], "Representation of Objects")
        self.assertEqual(self.sections["5.4.3.1"], "Class and Interface Resolution")

    def test_a_title_with_markup_survived_the_fetch(self):
        self.assertEqual(self.sections["2.1"], "The class File Format")

    def test_no_title_carries_leftover_markup(self):
        for number, title in self.sections.items():
            with self.subTest(section=number):
                self.assertNotIn("<", title)
                self.assertNotIn("&", title)
                self.assertTrue(title.strip())

    def test_no_title_repeats_its_own_number(self):
        for number, title in self.sections.items():
            with self.subTest(section=number):
                self.assertFalse(title.startswith(number))

    def test_every_chapter_page_is_hashed(self):
        for one in self.index["chapters"]:
            with self.subTest(chapter=one["chapter"]):
                self.assertEqual(len(one["sha256"]), 64)
                self.assertTrue(one["title"])

    def test_the_index_is_big_enough_to_be_the_whole_specification(self):
        """A parser that broke on six of seven pages would still produce an index."""
        self.assertGreater(len(self.sections), 150)

    def test_the_index_holds_numbers_and_titles_and_not_the_specification_text(self):
        """The text is Oracle's. This repository stores facts about it, not a copy."""
        longest = max(self.sections.values(), key=len)
        self.assertLess(len(longest), 120, longest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
