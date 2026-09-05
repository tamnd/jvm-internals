#!/usr/bin/env python3
"""Tests for the citation resolver.

A checker that passes when it should fail is worse than no checker, because it turns a
promise in `CONTRIBUTING.md` into a promise the repository appears to keep. So most of
these build a citation that is wrong in a specific way and check that refcheck says so:
a line whose content changed under a stable line number, a line past the end of the file,
a tag the pin does not accept, one line cited at two tags, an entry in the ledger nobody
cites any more.

The rest hold the reading half honest. The scanner has to find a citation in prose, in a
lesson, in a claims file and in generated JSON, because a citation nobody reads is a
citation nobody checks, and the four places citations live in this repository are four
different file formats. The network is never touched: every test either points
`JVX_JDK_SRC` at a tree made in a temporary directory or drives the functions directly.

The last one is the one that matters on a normal day. It resolves every citation in the
repository against the committed ledger, which is what CI runs.

  python tools/test_refcheck.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import refcheck  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

TAG = "jdk-27+35"
OTHER = "jdk-27-ga"
FILE = "src/hotspot/share/oops/markWord.hpp"

# Enough of a header to cite into. The lines either side of the cited one are here
# because the hash covers them, and a test that could not tell a changed neighbour from a
# changed line would not be testing the thing the hash is for.
HEADER = """\
// line one
// line two
  static const int lock_bits = 2;
// line four
// line five
  static const int age_bits = 4;
// line seven
"""


@contextlib.contextmanager
def tree(text: str = HEADER):
    """A checkout with one file in it, with JVX_JDK_SRC pointed at it."""
    with tempfile.TemporaryDirectory() as where:
        path = pathlib.Path(where) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        was = os.environ.get("JVX_JDK_SRC")
        os.environ["JVX_JDK_SRC"] = where
        try:
            yield pathlib.Path(where)
        finally:
            if was is None:
                del os.environ["JVX_JDK_SRC"]
            else:
                os.environ["JVX_JDK_SRC"] = was


def one(line: int = 3, tag: str = TAG, where: str = "docs/a.md:1") -> dict:
    return {f"{FILE}:{line}": {"cited_at": [where], "tags": [tag],
                               "cited_in": [where.rsplit(":", 1)[0]]}}


class TestResolving(unittest.TestCase):
    def test_a_citation_resolves_to_the_line_it_names(self):
        with tree():
            found = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        self.assertTrue(found["resolved"])
        self.assertEqual(found["line"], "  static const int lock_bits = 2;")

    def test_a_line_past_the_end_of_the_file_is_refused(self):
        with tree():
            found = refcheck.resolve(f"{FILE}:400", TAG, offline=True)
        self.assertFalse(found["resolved"])
        self.assertIn("has 7 lines", found["why"])

    def test_a_file_that_is_not_in_the_tree_is_refused(self):
        with tree():
            found = refcheck.resolve("src/hotspot/share/oops/nope.hpp:3", TAG,
                                     offline=True)
        self.assertFalse(found["resolved"])
        self.assertIn("could not read", found["why"])

    def test_the_hash_covers_the_lines_around_the_cited_one(self):
        """The whole point. A neighbour changing has to change the hash."""
        with tree():
            before = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        moved = HEADER.replace("// line five", "// line five, edited")
        with tree(moved):
            after = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        self.assertEqual(before["line"], after["line"])
        self.assertNotEqual(before["context_sha256"], after["context_sha256"])

    def test_an_edit_six_lines_away_does_not_change_the_hash(self):
        """The other half. A hash that changes on every edit is a hash nobody trusts."""
        with tree():
            before = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        with tree(HEADER.replace("// line seven", "// line seven, edited")):
            after = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        self.assertEqual(before["context_sha256"], after["context_sha256"])

    def test_trailing_whitespace_is_not_a_content_change(self):
        with tree():
            before = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        with tree(HEADER.replace("lock_bits = 2;", "lock_bits = 2;   ")):
            after = refcheck.resolve(f"{FILE}:3", TAG, offline=True)
        self.assertEqual(before["context_sha256"], after["context_sha256"])


class TestBuilding(unittest.TestCase):
    def test_a_tag_the_pin_does_not_accept_is_refused(self):
        with tree():
            entries, unresolved = refcheck.build(
                one(tag="jdk-26+9"), [TAG, OTHER], offline=True)
        self.assertEqual(entries, {})
        self.assertIn("does not accept", unresolved[0])

    def test_one_line_cited_at_two_tags_is_refused(self):
        source = one()
        source[f"{FILE}:3"]["tags"].append(OTHER)
        with tree():
            entries, unresolved = refcheck.build(source, [TAG, OTHER], offline=True)
        self.assertEqual(entries, {})
        self.assertIn("more than one tag", unresolved[0])

    def test_what_could_not_be_resolved_names_where_it_was_cited(self):
        with tree():
            _, unresolved = refcheck.build(
                one(line=400, where="docs/probes/thing.md:12"), [TAG], offline=True)
        self.assertIn("docs/probes/thing.md:12", unresolved[0])


class TestComparing(unittest.TestCase):
    def setUp(self):
        self.source = one()
        with tree():
            self.entries, _ = refcheck.build(self.source, [TAG], offline=True)

    def test_a_ledger_that_matches_the_tree_is_quiet(self):
        self.assertEqual(refcheck.compare(self.entries, self.entries, self.source), [])

    def test_a_line_whose_content_changed_is_caught(self):
        with tree(HEADER.replace("lock_bits = 2;", "lock_bits = 3;")):
            now, _ = refcheck.build(self.source, [TAG], offline=True)
        problems = refcheck.compare(self.entries, now, self.source)
        self.assertEqual(len(problems), 1)
        self.assertIn("its content changed", problems[0])
        self.assertIn("lock_bits = 2;", problems[0])
        self.assertIn("lock_bits = 3;", problems[0])

    def test_a_citation_that_is_not_in_the_ledger_is_caught(self):
        problems = refcheck.compare({}, self.entries, self.source)
        self.assertEqual(len(problems), 1)
        self.assertIn("not in the ledger", problems[0])

    def test_a_ledger_entry_nobody_cites_is_caught(self):
        stale = dict(self.entries)
        stale["src/hotspot/share/oops/gone.hpp:1"] = {
            "tag": TAG, "line": "x", "context_sha256": "y", "cited_in": []}
        problems = refcheck.compare(stale, self.entries, self.source)
        self.assertEqual(len(problems), 1)
        self.assertIn("cited nowhere", problems[0])

    def test_a_citation_that_moved_to_another_file_is_caught(self):
        moved = one(where="docs/b.md:9")
        with tree():
            now, _ = refcheck.build(moved, [TAG], offline=True)
        problems = refcheck.compare(self.entries, now, moved)
        self.assertEqual(len(problems), 1)
        self.assertIn("different files", problems[0])

    def test_a_citation_that_moved_down_its_own_file_is_not_a_change(self):
        """Adding a paragraph above a citation must not dirty the ledger."""
        moved = one(where="docs/a.md:400")
        with tree():
            now, _ = refcheck.build(moved, [TAG], offline=True)
        self.assertEqual(refcheck.compare(self.entries, now, moved), [])


class TestScanning(unittest.TestCase):
    """Citations live in four file formats here, and all four have to be read."""

    def scan(self, name: str, body: str) -> tuple[dict, list]:
        with tempfile.TemporaryDirectory() as where:
            path = pathlib.Path(where) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            was = refcheck.ROOT
            refcheck.ROOT = pathlib.Path(where)
            try:
                return refcheck.cited([path])
            finally:
                refcheck.ROOT = was

    def test_a_citation_in_prose_is_found(self):
        source, _ = self.scan("docs/a.md", f"The klass field is 22 bits {{[HOTSPOT {FILE}:152@{TAG}]}}.\n")
        self.assertEqual(list(source), [f"{FILE}:152"])

    def test_a_citation_in_a_claims_file_is_found(self):
        body = json.dumps({"claims": [{"citation": f"{FILE}:152@{TAG}"}]}, indent=2)
        source, _ = self.scan("lessons/O01/claims.json", body)
        self.assertEqual(list(source), [f"{FILE}:152"])

    def test_a_citation_in_generated_json_is_found(self):
        body = json.dumps({"defined_at": f"{FILE}:150@{TAG}"}, indent=2)
        source, _ = self.scan("docs/generated/markword.json", body)
        self.assertEqual(list(source), [f"{FILE}:150"])

    def test_a_citation_in_a_lesson_is_found(self):
        source, _ = self.scan("lessons/O01/lesson.py", f"# The shift {FILE}:150@{TAG}\n")
        self.assertEqual(list(source), [f"{FILE}:150"])

    def test_one_line_cited_from_four_places_is_one_entry_with_four_places(self):
        body = "\n".join(f"line {n} cites {FILE}:152@{TAG}" for n in range(4)) + "\n"
        source, _ = self.scan("docs/a.md", body)
        self.assertEqual(len(source), 1)
        self.assertEqual(len(source[f"{FILE}:152"]["cited_at"]), 4)

    def test_a_line_marked_ok_is_left_alone(self):
        source, _ = self.scan(
            "docs/a.md", f"Write it as path/to/file.cpp:123@{TAG} {refcheck.ALLOW}\n")
        self.assertEqual(source, {})

    def test_a_specification_citation_is_read_apart_into_its_three_pieces(self):
        source, spec = self.scan("docs/a.md", "Objects are represented {[JVMS §2.7@SE25]}.\n")
        self.assertEqual(source, {})
        self.assertEqual(len(spec), 1)
        self.assertEqual(spec[0]["spec"], "JVMS")
        self.assertEqual(spec[0]["section"], "2.7")
        self.assertEqual(spec[0]["edition"], "SE25")

    def test_a_specification_citation_without_the_section_sign_is_read_the_same(self):
        _, spec = self.scan("lessons/O01/claims.json", '{"citation": "JVMS 2.7@SE25"}\n')
        self.assertEqual(spec[0]["section"], "2.7")

    def test_a_deep_section_keeps_all_of_its_number(self):
        _, spec = self.scan("docs/a.md", "resolution {[JVMS §5.4.3.1@SE25]}.\n")
        self.assertEqual(spec[0]["section"], "5.4.3.1")

    def test_a_citation_ending_a_sentence_keeps_its_tag(self):
        """A trailing period is punctuation and a trailing backtick is markup."""
        for ending in (".", ")", "`", "]}.", ","):
            with self.subTest(ending=ending):
                source, _ = self.scan("docs/a.md", f"see {FILE}:152@{TAG}{ending}\n")
                self.assertEqual(source[f"{FILE}:152"]["tags"], [TAG])


class TestSpecifications(unittest.TestCase):
    """The half that decides whether a `[JVMS]` marker means anything."""

    PIN = {"jvms_edition": "SE25"}

    def cite(self, text: str, section: str, edition: str = "SE25",
             spec: str = "JVMS") -> list[dict]:
        return [{"spec": spec, "section": section, "edition": edition,
                 "where": "docs/a.md:3", "text": text}]

    def test_a_section_that_exists_resolves_to_its_title(self):
        resolved, problems = refcheck.specifications(
            self.cite("JVMS §2.7@SE25", "2.7"), self.PIN)
        self.assertEqual(problems, [])
        self.assertEqual(resolved[0]["title"], "Representation of Objects")

    def test_a_section_that_does_not_exist_is_caught(self):
        resolved, problems = refcheck.specifications(
            self.cite("JVMS §2.99@SE25", "2.99"), self.PIN)
        self.assertEqual(resolved, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("not in JVMS SE25", problems[0])

    def test_a_chapter_cited_as_a_section_is_caught(self):
        """Rule 7 says a JVMS marker names the section and not the chapter."""
        _, problems = refcheck.specifications(self.cite("JVMS §5@SE25", "5"), self.PIN)
        self.assertEqual(len(problems), 1)

    def test_an_edition_the_pin_does_not_name_is_caught(self):
        _, problems = refcheck.specifications(
            self.cite("JVMS §2.7@SE26", "2.7", edition="SE26"), self.PIN)
        self.assertEqual(len(problems), 1)
        self.assertIn("docs/pin.json says SE25", problems[0])

    def test_a_language_specification_citation_is_reported_as_unchecked(self):
        """There is no JLS index. Saying so beats resolving it against the wrong book."""
        resolved, problems = refcheck.specifications(
            self.cite("JLS §12.4@SE25", "12.4", spec="JLS"), self.PIN)
        self.assertEqual(problems, [])
        self.assertIsNone(resolved[0]["title"])
        self.assertIn("no index exists for JLS", resolved[0]["why"])


class TestTheRepository(unittest.TestCase):
    """What CI runs, and the reason the ledger is committed."""

    def setUp(self):
        self.source, self.spec = refcheck.cited(refcheck.sources())
        self.ledger = refcheck.ledger()

    def test_every_citation_in_the_repository_is_in_the_ledger(self):
        missing = sorted(set(self.source) - set(self.ledger["citations"]))
        self.assertEqual(missing, [], "run tools/refcheck.py --update")

    def test_the_ledger_cites_nothing_that_is_not_cited(self):
        stale = sorted(set(self.ledger["citations"]) - set(self.source))
        self.assertEqual(stale, [], "run tools/refcheck.py --update")

    def test_the_ledger_records_a_real_line_for_every_citation(self):
        """A ledger full of blank lines would pass every other test in this file."""
        for key, entry in self.ledger["citations"].items():
            with self.subTest(citation=key):
                self.assertTrue(entry["line"].strip(), "the cited line is blank")
                self.assertEqual(len(entry["context_sha256"]), 64)

    def test_every_tag_in_the_ledger_is_one_the_pin_accepts(self):
        pin = refcheck.load_pin()
        accepted = {pin[k] for k in ("jdk_tag", "jdk_ga_tag") if pin.get(k)}
        for key, entry in self.ledger["citations"].items():
            with self.subTest(citation=key):
                self.assertIn(entry["tag"], accepted)

    def test_the_repository_still_has_citations_to_check(self):
        """Guards against a scanner that quietly stops finding anything."""
        self.assertGreater(len(self.source), 10)

    def test_every_specification_citation_resolves_to_a_titled_section(self):
        resolved, problems = refcheck.specifications(self.spec, refcheck.load_pin())
        self.assertEqual(problems, [])
        for one in resolved:
            with self.subTest(citation=one["text"]):
                self.assertIsNotNone(one["title"], one["why"])

    def test_the_report_prints_the_title_beside_every_specification_citation(self):
        """The line a reviewer reads to notice that a claim and a section do not match."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            refcheck.main(["--report", "--offline"])
        self.assertIn("Representation of Objects", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
