#!/usr/bin/env python3
"""Tests for the blueprint compiler.

A compiler for a document format earns its place by refusing documents, so most of these
build one blueprint that is wrong in a single specific way and check that `bpc` says so.
The one that matters most is the pair about section 4: a clause under "what the
specification mandates" that cites a line of HotSpot source is the defect that would make
this project worse than a blog post, because it reads as "Java guarantees this" and is a
statement about one implementation at one tag.

The rest cover the two things a reader relies on. Coverage, meaning that no clause can
exist without an observation and a conformance item, so a specified behaviour that nobody
can watch and no harness tests is caught while it is being written rather than in M11.
And drift, meaning that the committed page and the committed conformance file have to be
what the sources build, because a stale generated page is the failure the generator was
bought to prevent.

The fixture writes its blueprint into a temporary directory and points the module
constants at it. `bpc.PIN` is bound at import and is deliberately left alone, so the
fixture carries the repository's real tag and edition and the two tests about a mismatched
pin are testing the check rather than the patching.

The last class is the one that runs on a normal day. It checks every committed blueprint
against every committed page, which is what CI runs.

  python tools/test_bpc.py
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

import bpc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))
TAG = PIN["jdk_tag"]
EDITION = PIN["jvms_edition"]

# A real line, cited the way a real clause cites it, so that a test of the marker rules is
# not also a test of a path nobody could resolve.
LINE = f"src/hotspot/share/classfile/classFileParser.cpp:5506@{TAG}"

META = f"""\
---
id: BP-TEST
title: A blueprint made in a temporary directory
pin: {TAG}
edition: {EDITION}
status: draft
lessons: []
reviews:
  expert: null
---
"""

SECTION4 = f"""\
# 4. Guarantees

## 4a. What the specification mandates

- **4a.1** The first four bytes of a class file are the magic number {{[JVMS 4.1@{EDITION}]}}.

## 4b. What HotSpot chose

- **4b.1** The magic number is read before anything else {{[HOTSPOT {LINE}]}}.

## 4c. Ordering obligations

- **4c.1** The magic number is read before the version {{[JVMS 4.1@{EDITION}]}}.
"""

SECTION5 = """\
# 5. Observation

- **5.1** 4a.1, 4b.1, 4c.1: read the first four bytes of a class file with `xxd`.
"""

SECTION6 = """\
# 6. Edge cases

## 6.1 Malformed input

A file of fewer than four bytes has no magic number in it to read.

## 6.2 Resource exhaustion

Nothing here allocates, so there is no limit available to exceed.

## 6.3 The concurrent case

Reading four bytes of a buffer another thread is writing gives four bytes of something.
"""

SECTION8 = """\
# 8. Conformance

## 8.1 What conformance means here

An implementation conforms if it refuses every input whose first four bytes are wrong.

## 8.2 Checklist

- **8.2.1** 4a.1, 4b.1, 4c.1: refuses an input whose first four bytes are not the magic number.
"""

SECTIONS: dict[int, tuple[str, str]] = {
    1: ("1-scope.md", "# 1. Scope\n\nThe first four bytes of a class file, and nothing else.\n"),
    2: ("2-data-structures.md", "# 2. Data structures\n\nFour bytes, big endian, at offset zero.\n"),
    3: ("3-operations.md", "# 3. Operations\n\nRead four bytes and compare them.\n"),
    4: ("4-guarantees.md", SECTION4),
    5: ("5-observation.md", SECTION5),
    6: ("6-edge-cases.md", SECTION6),
    7: ("7-configuration.md",
        "# 7. Configuration\n\n<!-- empty: nothing about the magic number can be "
        "configured, on this implementation or on any other -->\n"),
    8: ("8-conformance.md", SECTION8),
    9: ("9-provenance.md", "# 9. Provenance\n\nRead out of the specification and one file.\n"),
}


@contextlib.contextmanager
def sandbox(**edits: str | None):
    """A repository holding one valid blueprint, with `bpc` pointed at it.

    An entry in `edits` named after a section file replaces it, which is how a test says
    what it is testing, and `None` deletes it. A name that is not a section file is added
    as one more file, which is how the tests about misnamed and duplicated sections work.
    The `meta` key replaces `blueprint.md`.
    """
    with tempfile.TemporaryDirectory() as where:
        root = pathlib.Path(where)
        directory = root / "blueprints" / "BP-TEST"
        directory.mkdir(parents=True)
        (directory / "blueprint.md").write_text(edits.pop("meta", META) or META,
                                                encoding="utf-8")
        files: dict[str, str | None] = dict(SECTIONS.values())
        files.update(edits)
        for name, body in files.items():
            if body is not None:
                (directory / name).write_text(body, encoding="utf-8")
        (root / "docs" / "generated").mkdir(parents=True)

        was = (bpc.ROOT, bpc.SOURCE, bpc.BUILT, bpc.CONFORMANCE)
        bpc.ROOT = root
        bpc.SOURCE = root / "blueprints"
        bpc.BUILT = root / "docs" / "blueprints"
        bpc.CONFORMANCE = root / "docs" / "generated" / "conformance.json"
        try:
            yield root
        finally:
            bpc.ROOT, bpc.SOURCE, bpc.BUILT, bpc.CONFORMANCE = was


def build(write: bool = True) -> tuple[int, str]:
    """A build or a check, as the status it would exit with and everything it printed."""
    said = io.StringIO()
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        status = bpc.cmd_build(write=write)
    return status, said.getvalue()


def problems() -> list[str]:
    return bpc.check(bpc.load(bpc.SOURCE / "BP-TEST"))


def refused() -> contextlib.AbstractContextManager:
    return unittest.TestCase().assertRaises(bpc.BlueprintError)


class TestAWholeBlueprint(unittest.TestCase):
    def test_a_complete_blueprint_loads(self):
        with sandbox():
            bp = bpc.load(bpc.SOURCE / "BP-TEST")
            self.assertEqual(bp.id, "BP-TEST")
            self.assertEqual(sorted(bp.clauses), ["4a.1", "4b.1", "4c.1"])
            self.assertEqual(sorted(bp.observations), ["5.1"])
            self.assertEqual(sorted(bp.conformance), ["8.2.1"])
            self.assertTrue(bp.sections[7].empty.startswith("nothing about the magic"))

    def test_a_complete_blueprint_checks_clean(self):
        with sandbox():
            self.assertEqual(problems(), [])

    def test_build_writes_the_page_and_the_checklist(self):
        with sandbox() as root:
            status, said = build()
            page = (root / "docs" / "blueprints" / "BP-TEST.md").read_text(encoding="utf-8")
            payload = json.loads(
                (root / "docs" / "generated" / "conformance.json").read_text(encoding="utf-8")
            )
        self.assertEqual(status, 0, said)
        self.assertIn("# BP-TEST. A blueprint made in a temporary directory", page)
        self.assertIn("## 4. Guarantees", page)
        self.assertIn("### 4a. What the specification mandates", page)
        entry = payload["blueprints"][0]
        self.assertEqual(entry["checklist"][0]["clauses"], ["4a.1", "4b.1", "4c.1"])
        self.assertEqual(entry["clauses"][0]["kinds"], ["JVMS"])
        self.assertEqual(entry["clauses"][0]["observed_by"], ["5.1"])
        self.assertEqual(entry["clauses"][0]["tested_by"], ["8.2.1"])

    def test_check_passes_on_what_build_wrote(self):
        with sandbox():
            build()
            status, said = build(write=False)
        self.assertEqual(status, 0, said)

    def test_an_edited_page_is_drift(self):
        with sandbox() as root:
            build()
            page = root / "docs" / "blueprints" / "BP-TEST.md"
            page.write_text(page.read_text(encoding="utf-8").replace("four bytes", "five"),
                            encoding="utf-8")
            status, said = build(write=False)
        self.assertEqual(status, 1)
        self.assertIn("run 'python tools/bpc.py build'", said)

    def test_an_edited_checklist_is_drift(self):
        with sandbox() as root:
            build()
            where = root / "docs" / "generated" / "conformance.json"
            payload = json.loads(where.read_text(encoding="utf-8"))
            payload["blueprints"][0]["status"] = "final"
            where.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            status, said = build(write=False)
        self.assertEqual(status, 1)
        self.assertIn("conformance.json", said)


class TestSectionFour(unittest.TestCase):
    """The rule the compiler exists for."""

    def test_a_mandate_clause_may_not_cite_hotspot(self):
        broken = SECTION4.replace(f"number {{[JVMS 4.1@{EDITION}]}}",
                                  f"number {{[HOTSPOT {LINE}]}}")
        with sandbox(**{"4-guarantees.md": broken}):
            said = " ".join(problems())
        self.assertIn("is in 4a", said)
        self.assertIn("it is a 4b clause", said)

    def test_a_choice_clause_may_not_cite_the_specification(self):
        broken = SECTION4.replace(f"anything else {{[HOTSPOT {LINE}]}}",
                                  f"anything else {{[JVMS 4.1@{EDITION}]}}")
        with sandbox(**{"4-guarantees.md": broken}):
            said = " ".join(problems())
        self.assertIn("is in 4b", said)
        self.assertIn("two clauses", said)

    def test_an_ordering_clause_may_cite_either(self):
        both = SECTION4.replace(
            f"the version {{[JVMS 4.1@{EDITION}]}}",
            f"the version {{[JVMS 4.1@{EDITION}]}} and is read once {{[HOTSPOT {LINE}]}}",
        )
        with sandbox(**{"4-guarantees.md": both}):
            self.assertEqual(problems(), [])

    def test_a_clause_with_no_marker_is_refused(self):
        broken = SECTION4.replace(f" {{[JVMS 4.1@{EDITION}]}}.", ".", 1)
        with sandbox(**{"4-guarantees.md": broken}):
            said = " ".join(problems())
        self.assertIn("carries no marker", said)

    def test_a_clause_under_no_subheading_is_refused(self):
        broken = SECTION4.replace("## 4a. What the specification mandates",
                                  "## 4. What the specification mandates")
        with sandbox(**{"4-guarantees.md": broken}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("only those three", str(caught.exception))

    def test_clauses_out_of_order_are_refused(self):
        broken = SECTION4.replace(
            "## 4b. What HotSpot chose",
            f"- **4a.3** A third clause {{[JVMS 4.1@{EDITION}]}}.\n"
            f"- **4a.2** A second one, written after it {{[JVMS 4.1@{EDITION}]}}.\n\n"
            "## 4b. What HotSpot chose",
        )
        with sandbox(**{"4-guarantees.md": broken}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("out of order", str(caught.exception))

    def test_a_repeated_clause_number_is_refused(self):
        broken = SECTION4.replace(
            "## 4b. What HotSpot chose",
            f"- **4a.1** The same number a second time {{[JVMS 4.1@{EDITION}]}}.\n\n"
            "## 4b. What HotSpot chose",
        )
        with sandbox(**{"4-guarantees.md": broken}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("appears twice", str(caught.exception))

    def test_a_subsection_with_no_clauses_is_refused(self):
        broken = SECTION4.replace(
            f"- **4c.1** The magic number is read before the version {{[JVMS 4.1@{EDITION}]}}.",
            "Nothing ordered here yet.",
        )
        with sandbox(**{"4-guarantees.md": broken}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("has no clauses", str(caught.exception))


class TestCoverage(unittest.TestCase):
    def test_a_clause_nobody_observes_is_refused(self):
        with sandbox(**{"5-observation.md": SECTION5.replace(", 4b.1", "")}):
            said = " ".join(problems())
        self.assertIn("no section 5 item observes clause 4b.1", said)

    def test_a_clause_no_checklist_item_covers_is_refused(self):
        with sandbox(**{"8-conformance.md": SECTION8.replace(", 4c.1", "")}):
            said = " ".join(problems())
        self.assertIn("no section 8.2 item covers clause 4c.1", said)

    def test_an_item_naming_a_clause_that_does_not_exist_is_refused(self):
        with sandbox(**{"5-observation.md": SECTION5.replace("4c.1:", "4c.1, 4b.9:")}):
            said = " ".join(problems())
        self.assertIn("there is no clause 4b.9", said)

    def test_an_observation_with_no_tool_is_refused(self):
        with sandbox(**{"5-observation.md": SECTION5.replace(" with `xxd`", "")}):
            said = " ".join(problems())
        self.assertIn("names no tool", said)

    def test_unobservable_needs_a_reason_worth_reading(self):
        thin = SECTION5.replace("read the first four bytes of a class file with `xxd`",
                                "unobservable. No tool.")
        with sandbox(**{"5-observation.md": thin}):
            said = " ".join(problems())
        self.assertIn("is unobservable and its reason", said)

    def test_unobservable_with_a_real_reason_is_accepted(self):
        whole = SECTION5.replace(
            "read the first four bytes of a class file with `xxd`",
            "unobservable. No program can watch the order of two reads inside one "
            "implementation, because nothing may write between them.",
        )
        with sandbox(**{"5-observation.md": whole}):
            self.assertEqual(problems(), [])

    def test_an_item_that_names_no_clause_is_refused(self):
        broken = SECTION5.replace("- **5.1** 4a.1, 4b.1, 4c.1:", "- **5.1** everything:")
        with sandbox(**{"5-observation.md": broken}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("names no clause", str(caught.exception))


class TestTheShapeOfADirectory(unittest.TestCase):
    def test_a_missing_section_is_refused(self):
        with sandbox(**{"7-configuration.md": None}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("no section 7", str(caught.exception))

    def test_two_files_claiming_one_section_are_refused(self):
        with sandbox(**{"7-flags.md": "# 7. Configuration\n\nA second section 7.\n"}), \
                refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("section 7 is also", str(caught.exception))

    def test_a_misnamed_file_is_refused(self):
        with sandbox(**{"notes.md": "# Notes\n\nSomething somebody left here.\n"}), \
                refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("either misnamed or does not belong here", str(caught.exception))

    def test_a_heading_that_does_not_match_its_number_is_refused(self):
        with sandbox(**{"9-provenance.md": "# 8. Provenance\n\nThe wrong number.\n"}), \
                refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("this is section 9", str(caught.exception))

    def test_a_section_that_may_not_be_empty_is_refused(self):
        reason = ("<!-- empty: this one gives a reason long enough to pass the floor "
                  "and is still not allowed to be empty -->")
        with sandbox(**{"1-scope.md": f"# 1. Scope\n\n{reason}\n"}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("may not be empty", str(caught.exception))

    def test_an_empty_section_with_a_thin_reason_is_refused(self):
        with sandbox(**{"7-configuration.md":
                        "# 7. Configuration\n\n<!-- empty: nothing to say -->\n"}), \
                refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn(f"at least {bpc.REASON_FLOOR} characters", str(caught.exception))

    def test_a_section_with_a_heading_and_nothing_under_it_is_refused(self):
        with sandbox(**{"1-scope.md": "# 1. Scope\n"}), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("a heading and nothing under it", str(caught.exception))

    def test_front_matter_with_prose_under_it_is_refused(self):
        with sandbox(meta=META + "\nA paragraph somebody put in the wrong file.\n"), \
                refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("holds the front matter and nothing else", str(caught.exception))

    def test_an_id_that_is_not_the_directory_is_refused(self):
        with sandbox(meta=META.replace("id: BP-TEST", "id: BP-OTHER")), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("and the directory is BP-TEST", str(caught.exception))

    def test_a_pin_that_is_not_the_repository_pin_is_refused(self):
        with sandbox(meta=META.replace(f"pin: {TAG}", "pin: jdk-21+35")), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("docs/pin.json says", str(caught.exception))


class TestGeneratedSections(unittest.TestCase):
    PAGE = """\
# BP-TEST section 2. The four bytes

Generated by a generator that does not exist, for a blueprint that only exists in a test.

## The table

| item | what it is |
|---|---|
| `magic` | four bytes |
"""

    def generated(self, page: str = PAGE) -> tuple[dict[str, str], str]:
        return {"2-data-structures.md": "<!-- generated: docs/generated/four.md -->\n"}, page

    def write(self, root: pathlib.Path, page: str) -> None:
        (root / "docs" / "generated" / "four.md").write_text(page, encoding="utf-8")

    def test_a_generated_section_is_included_and_demoted(self):
        edits, page = self.generated()
        with sandbox(**edits) as root:
            self.write(root, page)
            status, said = build()
            built = (root / "docs" / "blueprints" / "BP-TEST.md").read_text(encoding="utf-8")
        self.assertEqual(status, 0, said)
        self.assertIn("## 2. The four bytes", built)
        self.assertIn("### The table", built)
        self.assertIn("Included from [docs/generated/four.md](../generated/four.md)", built)
        self.assertNotIn("# BP-TEST section 2.", built)

    def test_a_generated_section_with_prose_under_it_is_refused(self):
        edits, page = self.generated()
        edits["2-data-structures.md"] += "\nAnd a paragraph of my own.\n"
        with sandbox(**edits) as root:
            self.write(root, page)
            with refused() as caught:
                bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("the directive and nothing else", str(caught.exception))

    def test_a_page_that_says_it_is_another_section_is_refused(self):
        edits, page = self.generated()
        with sandbox(**edits) as root:
            self.write(root, page.replace("section 2.", "section 3."))
            with refused() as caught:
                bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("included as BP-TEST section 2", str(caught.exception))

    def test_a_page_with_no_declared_heading_is_refused(self):
        edits, page = self.generated()
        with sandbox(**edits) as root:
            self.write(root, page.replace("# BP-TEST section 2. The four bytes",
                                          "# The four bytes"))
            with refused() as caught:
                bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("starts with '# BP-TEST section 2.", str(caught.exception))

    def test_a_directive_pointing_at_nothing_is_refused(self):
        edits, _ = self.generated()
        with sandbox(**edits), refused() as caught:
            bpc.load(bpc.SOURCE / "BP-TEST")
        self.assertIn("does not exist", str(caught.exception))

    def test_retyping_a_generated_table_row_is_refused(self):
        edits, page = self.generated()
        edits["3-operations.md"] = (
            "# 3. Operations\n\nRead the header.\n\n"
            "| item | what it is |\n|---|---|\n| `magic` | four bytes, again |\n"
        )
        with sandbox(**edits) as root:
            self.write(root, page)
            said = " ".join(problems())
        self.assertIn("already has a row for", said)

    def test_a_row_the_generated_page_does_not_have_is_fine(self):
        edits, page = self.generated()
        edits["3-operations.md"] = (
            "# 3. Operations\n\nRead the header.\n\n"
            "| item | what it is |\n|---|---|\n| `version` | two more bytes |\n"
        )
        with sandbox(**edits) as root:
            self.write(root, page)
            self.assertEqual(problems(), [])


class TestProse(unittest.TestCase):
    def test_leaning_on_a_lesson_is_refused(self):
        with sandbox(**{"1-scope.md":
                        "# 1. Scope\n\nThe four bytes, as we saw when we read them.\n"}):
            said = " ".join(problems())
        self.assertIn("'as we saw'", said)

    def test_a_missing_edge_case_subheading_is_refused(self):
        thin = SECTION6.replace("## 6.3 The concurrent case", "## 6.3 Other cases")
        with sandbox(**{"6-edge-cases.md": thin}):
            said = " ".join(problems())
        self.assertIn("no subheading about the concurrent case", said)


class TestScaffolding(unittest.TestCase):
    def test_a_new_blueprint_is_a_blueprint_with_nothing_in_it(self):
        with sandbox() as root:
            said = io.StringIO()
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                status = bpc.cmd_new("BP-FRESH")
            made = sorted(p.name for p in (root / "blueprints" / "BP-FRESH").iterdir())
        self.assertEqual(status, 0, said.getvalue())
        self.assertEqual(len(made), bpc.SECTION_COUNT + 1)
        self.assertIn("blueprint.md", made)

    def test_a_scaffold_does_not_pass_its_own_check(self):
        """A skeleton that loaded clean is a skeleton somebody would commit."""
        with sandbox() as root:
            said = io.StringIO()
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                bpc.cmd_new("BP-FRESH")
            with refused():
                bpc.load(root / "blueprints" / "BP-FRESH")

    def test_an_id_that_is_not_a_blueprint_id_is_refused(self):
        with sandbox():
            said = io.StringIO()
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                status = bpc.cmd_new("classfile")
        self.assertEqual(status, 1)


class TestTheRealBlueprints(unittest.TestCase):
    """What CI runs. Every committed blueprint, against every committed page."""

    def test_every_blueprint_checks_clean_and_nothing_has_drifted(self):
        status, said = build(write=False)
        self.assertEqual(status, 0, said)

    def test_every_clause_of_every_blueprint_is_observed_and_tested(self):
        blueprints = bpc.load_all()
        self.assertTrue(blueprints, "no blueprints to check")
        payload = json.loads(bpc.conformance_json(blueprints))
        for entry in payload["blueprints"]:
            for clause in entry["clauses"]:
                where = f"{entry['id']} {clause['id']}"
                self.assertTrue(clause["observed_by"], where)
                self.assertTrue(clause["tested_by"], where)
                self.assertTrue(clause["citations"], where)

    def test_the_committed_checklist_is_the_one_the_sources_build(self):
        fresh = bpc.conformance_json(bpc.load_all())
        self.assertEqual(bpc.CONFORMANCE.read_text(encoding="utf-8"), fresh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
