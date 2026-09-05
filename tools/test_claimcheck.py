#!/usr/bin/env python3
"""Tests for the claim ledger checker.

The two failures worth most of this file are the two that are silent. A marker in the
prose with no claim behind it looks like a cited sentence and is not one. A claim in the
ledger whose marker never appears in the prose leaves the lesson asserting something
unmarked, which is exactly what rule 7 exists to prevent, and a reader cannot see either
one by reading. So each of them gets a test that builds the failure and checks that
claimcheck names it.

The rest are the cheap mistakes that a ledger accumulates: a missing field, a renumbered
claim, a claim marked observable that measured nothing, a `JVMS` marker over a source
line. That last one is a category error rather than a typo, so it gets its own test in
both directions.

Nothing here reads the network or the pinned tree. Each test writes a lesson and a ledger
into a temporary directory and points `claimcheck.LESSONS` at it. The last class runs the
checker over the real `lessons/`, which is what CI runs.

  python tools/test_claimcheck.py
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import claimcheck  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

CITE = "src/hotspot/share/oops/markWord.hpp:124@jdk-27+35"
SPEC = "JVMS 2.7@SE25"

# One claim of each kind, so a test can take this and break one thing.
CLAIMS = [
    {
        "id": "T01-C1",
        "claim": "The specification does not mandate an object layout.",
        "marker": "JVMS",
        "citation": SPEC,
        "observable": False,
    },
    {
        "id": "T01-C2",
        "claim": "The bottom two bits of the mark word are the lock state.",
        "marker": "HOTSPOT",
        "citation": CITE,
        "observable": True,
        "cell": "measure_1",
        "measured": "every mark word read here ends in 01",
    },
]

# The cell markers are `# %%` and the markers are `{[...]}`, so neither percent
# formatting nor `str.format` can build this. It is a literal with one substitution.
LESSON = """\
# %% [markdown] id=hook
# The specification says nothing about layout {[JVMS §2.7@SE25]}.
#
# The lock state is the bottom two bits {[HOTSPOT @CITE@]}.

# %% id=measure_1 env=E0
jvx.mark(new Object());
""".replace("@CITE@", CITE)


@contextlib.contextmanager
def lesson(claims=None, source: str = LESSON, name: str = "T01"):
    """A lessons directory with one lesson in it, with claimcheck pointed at it."""
    was = claimcheck.LESSONS
    with tempfile.TemporaryDirectory() as where:
        path = pathlib.Path(where) / name
        path.mkdir()
        (path / "claims.json").write_text(
            json.dumps(CLAIMS if claims is None else claims), encoding="utf-8")
        (path / "lesson.py").write_text(source, encoding="utf-8")
        claimcheck.LESSONS = pathlib.Path(where)
        try:
            yield path
        finally:
            claimcheck.LESSONS = was


def problems(claims=None, source: str = LESSON) -> list[str]:
    with lesson(claims, source) as path:
        return claimcheck.check(path)


def without(field: str, position: int = 1) -> list:
    copy = json.loads(json.dumps(CLAIMS))
    copy[position].pop(field, None)
    return copy


def edited(position: int, **fields) -> list:
    copy = json.loads(json.dumps(CLAIMS))
    copy[position].update(fields)
    return copy


class TestTheLedgerAlone(unittest.TestCase):
    def test_a_ledger_that_matches_its_lesson_has_nothing_to_say(self):
        self.assertEqual(problems(), [])

    def test_a_claim_with_no_measured_field_is_caught(self):
        """The one failure the project exists to avoid: sounds measured, was not."""
        found = problems(without("measured"))
        self.assertEqual(len(found), 1, found)
        self.assertIn("measured", found[0])

    def test_a_claim_that_is_observable_and_names_no_cell_is_caught(self):
        found = problems(without("cell"))
        self.assertTrue(any("names no cell" in one for one in found), found)

    def test_a_claim_that_is_not_observable_and_names_a_cell_is_caught(self):
        found = problems(edited(0, cell="measure_1"))
        self.assertTrue(any("One of the two is wrong" in one for one in found), found)

    def test_a_missing_required_field_is_caught(self):
        found = problems(without("claim"))
        self.assertTrue(any("has no claim" in one for one in found), found)

    def test_a_claim_numbered_out_of_order_is_caught(self):
        """A gap in the numbering is how a deleted claim hides."""
        found = problems(edited(1, id="T01-C7"))
        self.assertTrue(any("should be T01-C2" in one for one in found), found)

    def test_a_third_marker_is_refused(self):
        found = problems(edited(1, marker="JEP"))
        self.assertTrue(any("no third option" in one for one in found), found)

    def test_a_jvms_marker_over_a_source_line_is_a_category_error(self):
        found = problems(edited(0, marker="JVMS", citation=CITE))
        self.assertTrue(any("category error" in one for one in found), found)

    def test_a_hotspot_marker_over_a_specification_section_is_a_category_error(self):
        found = problems(edited(1, marker="HOTSPOT", citation=SPEC))
        self.assertTrue(any("category error" in one for one in found), found)

    def test_three_claims_a_reader_must_trust_is_over_the_cap(self):
        copy = json.loads(json.dumps(CLAIMS))
        for number in (3, 4, 5):
            copy.append({"id": f"T01-C{number}", "claim": "trust me",
                         "marker": "JVMS", "citation": SPEC, "observable": False})
        found = problems(copy)
        self.assertTrue(any("take on trust" in one for one in found), found)

    def test_the_cap_counts_claims_and_not_lessons(self):
        """Two unobservable claims is the cap, not one under it."""
        copy = json.loads(json.dumps(CLAIMS))
        copy.append({"id": "T01-C3", "claim": "trust me", "marker": "JVMS",
                     "citation": SPEC, "observable": False})
        self.assertEqual(problems(copy), [])


class TestTheLedgerAgainstTheLesson(unittest.TestCase):
    def test_a_claim_whose_marker_is_in_no_sentence_is_caught(self):
        """The ledger knows the sentence needs a citation and the sentence does not."""
        source = LESSON.replace(" {[HOTSPOT " + CITE + "]}", "")
        found = problems(source=source)
        self.assertTrue(any("no sentence in the lesson carries" in one
                            for one in found), found)

    def test_a_marker_with_no_claim_behind_it_is_caught(self):
        source = LESSON.replace(
            "jvx.mark", "# And the age is four bits {[HOTSPOT "
            "src/hotspot/share/oops/markWord.hpp:126@jdk-27+35]}\njvx.mark")
        found = problems(source=source)
        self.assertTrue(any("the ledger has no claim for it" in one
                            for one in found), found)

    def test_a_claim_naming_a_cell_the_lesson_does_not_have_is_caught(self):
        found = problems(edited(1, cell="measure_9"))
        self.assertTrue(any("which is not in the lesson" in one for one in found), found)

    def test_a_section_sign_does_not_make_two_citations_differ(self):
        """`JVMS §2.7@SE25` in prose and `JVMS 2.7@SE25` in the ledger are one citation."""
        self.assertEqual(claimcheck.key("JVMS", "§2.7@SE25"),
                         claimcheck.key("JVMS", "2.7@SE25"))

    def test_a_marker_inside_a_sentence_is_found(self):
        """Markers do not only appear at the end of a line."""
        found = claimcheck.markers(
            "# a {[HOTSPOT " + CITE + "]} b {[JVMS §2.7@SE25]} c")
        self.assertEqual(sorted(found), ["JVMS 2.7@SE25",
                                         "src/hotspot/share/oops/markWord.hpp:124"
                                         "@jdk-27+35"])

    def test_the_line_a_marker_is_on_is_reported(self):
        found = claimcheck.markers("one\ntwo {[JVMS §2.7@SE25]}\nthree")
        self.assertEqual(found["JVMS 2.7@SE25"], [2])

    def test_a_lesson_with_a_ledger_and_no_lesson_file_is_caught(self):
        with lesson() as path:
            (path / "lesson.py").unlink()
            found = claimcheck.check(path)
        self.assertTrue(any("no lesson.py" in one for one in found), found)

    def test_an_empty_ledger_is_caught(self):
        found = problems([])
        self.assertTrue(any("not a list of claims" in one for one in found), found)


class TestTheRealLessons(unittest.TestCase):
    """What CI runs. Every claim in the repository, against the lesson it belongs to."""

    def test_every_lesson_agrees_with_its_ledger(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = claimcheck.main([])
        self.assertEqual(code, 0, err.getvalue())

    def test_every_lesson_in_the_repository_has_a_ledger(self):
        """A lesson without one is a lesson nothing checks."""
        for path in sorted(ROOT.glob("lessons/*/lesson.py")):
            with self.subTest(lesson=path.parent.name):
                self.assertTrue((path.parent / "claims.json").is_file())

    def test_the_report_names_every_claim(self):
        for path in claimcheck.lessons(None):
            claims = json.loads((path / "claims.json").read_text(encoding="utf-8"))
            printed = claimcheck.report(path)
            for claim in claims:
                with self.subTest(claim=claim["id"]):
                    self.assertIn(claim["id"], printed)

    def test_asking_for_a_lesson_that_does_not_exist_says_so(self):
        with self.assertRaises(SystemExit):
            claimcheck.lessons("Z99")


if __name__ == "__main__":
    unittest.main(verbosity=2)
