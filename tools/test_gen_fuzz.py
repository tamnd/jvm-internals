#!/usr/bin/env python3
"""Tests for the class file fuzzer's page.

Four parts. Whether the pieces that read the specification and the JVM's words behave the
way the page's two comparison columns assume, whether the claims table is internally
consistent, whether the committed measurements are, and whether the committed page still
says what all three of them say.

Everything here is offline. The specification fixture is a cut down chapter with the same
markup the real one uses, including a sentence broken across a `<code>` element, which is
the shape that makes a quotation checkable at all.

  python tools/test_gen_fuzz.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_fuzz as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

CHAPTER = b"""<html><body>
<div class="section"><div class="titlepage"><div><h2 class="title">
<a name="jvms-4.9.2"></a>4.9.2.&nbsp;Structural Constraints</h2></div></div>
<p>At no point during execution can the operand stack grow to a depth greater than
that implied by the <code class="literal">max_stack</code> item.</p>
</div>
<div class="section"><div class="titlepage"><div><h3 class="title">
<a name="jvms-4.10"></a>4.10.&nbsp;Verification of class Files</h3></div></div>
<p>A well-formed class file &amp; its contents.</p>
</div>
</body></html>
"""


class TestReadingTheSpecification(unittest.TestCase):
    def setUp(self):
        self.prose = gen.sections(CHAPTER)

    def test_each_anchor_becomes_a_section(self):
        self.assertEqual(sorted(self.prose), ["4.10", "4.9.2"])

    def test_a_sentence_broken_by_markup_reads_as_one_sentence(self):
        self.assertIn(
            "grow to a depth greater than that implied by the max_stack item",
            self.prose["4.9.2"])

    def test_an_entity_is_the_character_it_stands_for(self):
        self.assertIn("class file & its contents", self.prose["4.10"])

    def test_a_heading_stays_out_of_the_section_before_it(self):
        self.assertNotIn("Verification", self.prose["4.9.2"])


class TestWhetherAMessageNamesAThing(unittest.TestCase):
    """The column that says whether HotSpot used the specification's word.

    The three spellings matter because the two sides write the same identifier
    differently, and the word boundary matters because one message contains a longer
    word that has the shorter one inside it.
    """

    def test_the_specification_spelling_matches(self):
        self.assertTrue(gen.mentions("no max_stack here", "max_stack"))

    def test_the_same_words_with_a_space_match(self):
        self.assertTrue(gen.mentions("Arguments can't fit into max locals", "max_locals"))

    def test_the_same_words_run_together_match(self):
        self.assertTrue(gen.mentions("Invalid superclass index 0", "super_class"))
        self.assertTrue(gen.mentions("Expecting a stackmap frame", "stack map"))

    def test_a_longer_word_containing_the_shorter_one_does_not_match(self):
        self.assertFalse(gen.mentions("StackMapTable format error", "max_stack"))
        self.assertFalse(gen.mentions("StackMapTable format error", "stack"))

    def test_the_match_ignores_case(self):
        self.assertTrue(gen.mentions("Illegal UTF8 string", "utf8"))

    def test_a_word_that_is_absent_is_absent(self):
        self.assertFalse(gen.mentions("Illegal class modifiers: 0x411", "ACC_FINAL"))


class TestTheSmallPieces(unittest.TestCase):
    def test_a_throwable_is_named_without_its_package(self):
        self.assertEqual(gen.thrown("java.lang.VerifyError"), "VerifyError")
        self.assertEqual(gen.thrown(""), "")

    def test_a_disassembly_is_cut_off_the_message(self):
        self.assertEqual(
            gen.short("Bad instruction: dd Exception Details:   Location: x"),
            "Bad instruction: dd")

    def test_a_message_with_no_disassembly_is_left_alone(self):
        self.assertEqual(gen.short("Truncated class file"), "Truncated class file")

    def test_a_pipe_cannot_break_a_table_row(self):
        self.assertEqual(gen.cell("a|b"), "a\\|b")

    def test_counting_puts_the_largest_first_and_breaks_ties_by_name(self):
        flips = [{"stage": "parse"}, {"stage": "parse"}, {"stage": "link"},
                 {"stage": "accepted"}]
        self.assertEqual(gen.counted(flips, "stage"),
                         [("parse", 2), ("accepted", 1), ("link", 1)])

    def test_the_opcode_table_still_parses_into_mnemonics(self):
        known = gen.opcodes()
        self.assertIn("if_icmplt", known)
        self.assertIn("nop", known)
        self.assertNotIn("fast_iaccess_0", known)


class TestTheClaims(unittest.TestCase):
    """`SPEC` is the half of the comparison that a person wrote, so it is checked hardest.

    What cannot be checked here is whether a quotation appears in the section it is
    attributed to, because that needs the specification and these tests are offline. The
    generator does that on every run and stops if it fails, which is the right place for
    it: a claim that has come loose from the document must not be printable.
    """

    def test_every_case_is_ordered_and_every_ordered_case_is_a_case(self):
        self.assertEqual(sorted(gen.SPEC), sorted(gen.ORDER))
        self.assertEqual(len(gen.ORDER), len(set(gen.ORDER)))

    def test_every_word_looked_for_is_a_word_the_quotation_uses(self):
        for case, one in gen.SPEC.items():
            quoted = " ".join(one.states).lower()
            for name in one.names:
                with self.subTest(case=case, name=name):
                    self.assertIn(name.lower(), quoted)

    def test_a_constraint_in_4_9_or_4_10_is_a_verify_error_and_nothing_else_is(self):
        for case, one in gen.SPEC.items():
            with self.subTest(case=case):
                self.assertEqual(one.rule.startswith(("4.9", "4.10")),
                                 one.error == "VerifyError")

    def test_every_error_is_one_of_the_four_the_two_sections_name(self):
        named = {"ClassFormatError", "UnsupportedClassVersionError",
                 "NoClassDefFoundError", "VerifyError"}
        for case, one in gen.SPEC.items():
            with self.subTest(case=case):
                self.assertIn(one.error, named)
                self.assertIn(one.named_at, (gen.FORMAT, gen.VERIFY))

    def test_every_quotation_is_long_enough_to_be_a_quotation(self):
        # A fragment of three words would match half the chapter and check nothing.
        for case, one in gen.SPEC.items():
            for fragment in one.states:
                with self.subTest(case=case):
                    self.assertGreaterEqual(len(fragment.split()), 6)


class TestTheCommittedMeasurements(unittest.TestCase):
    def setUp(self):
        self.results = {}
        for path in sorted((ROOT / gen.RESULTS).glob("*.json")):
            self.results[path.name] = json.loads(path.read_text(encoding="utf-8"))
        self.pin = json.loads((ROOT / "docs/pin.json").read_text(encoding="utf-8"))

    def test_two_platforms_were_measured(self):
        self.assertEqual(sorted(self.results),
                         ["linux-aarch64.json", "osx-arm64.json"])

    def test_every_result_is_the_pinned_build_and_the_pinned_class_file_version(self):
        for name, data in self.results.items():
            with self.subTest(name=name):
                self.assertEqual(data["java_build"], self.pin["jdk_build"])
                self.assertEqual(data["class_file_major"],
                                 self.pin["jdk_class_file_major"])
                self.assertEqual(data["issue"], 15)
                self.assertEqual(data["probe"], "classfile-fuzz")

    def test_the_unbroken_seed_class_ran_everywhere(self):
        for name, data in self.results.items():
            with self.subTest(name=name):
                self.assertEqual(data["seed_run"]["stage"], "accepted")

    def test_the_probe_measured_exactly_the_cases_the_generator_claims(self):
        for name, data in self.results.items():
            with self.subTest(name=name):
                self.assertEqual(sorted(data["cases"]), sorted(gen.SPEC))

    def test_every_case_stopped_somewhere_with_something_to_say(self):
        for name, data in self.results.items():
            for case, one in data["cases"].items():
                with self.subTest(name=name, case=case):
                    self.assertIn(one["stage"], ("parse", "link", "run"))
                    self.assertTrue(one["error"].startswith("java.lang."))
                    self.assertTrue(one["message"])
                    self.assertTrue(one["what"])

    def test_the_two_platforms_say_the_same_thing_about_every_case(self):
        first, second = self.results.values()
        for case in first["cases"]:
            with self.subTest(case=case):
                self.assertEqual(first["cases"][case], second["cases"][case])

    def test_the_two_platforms_say_the_same_thing_about_every_flip(self):
        first, second = self.results.values()
        self.assertEqual(first["random"], second["random"])

    def test_every_flip_landed_in_a_region_the_page_has_a_row_for(self):
        for name, data in self.results.items():
            for flip in data["random"]:
                with self.subTest(name=name, mutant=flip["mutant"]):
                    self.assertIn(flip["region"], gen.REGIONS)
                    self.assertLess(flip["at"], data["seed_bytes"])

    def test_an_accepted_flip_has_no_error_and_a_refused_one_does(self):
        for name, data in self.results.items():
            for flip in data["random"]:
                with self.subTest(name=name, mutant=flip["mutant"]):
                    if flip["stage"] == "accepted":
                        self.assertEqual(flip["error"], "")
                    else:
                        self.assertTrue(flip["error"])

    def test_the_regions_cover_the_file_end_to_end(self):
        for name, data in self.results.items():
            with self.subTest(name=name):
                covered = sum(one["bytes"] for one in data["regions"].values())
                self.assertEqual(covered, data["seed_bytes"])


class TestTheCommittedPage(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")
        self.results = {}
        for path in sorted((ROOT / gen.RESULTS).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["file"] = f"{gen.RESULTS.as_posix()}/{path.name}"
            self.results[data["platform"]] = data
        self.rows = gen.rows(self.results)

    def test_the_page_exists_and_says_who_wrote_it(self):
        self.assertIn("tools/gen_fuzz.py", self.text)
        self.assertIn("Do not edit", self.text)

    def test_every_case_is_on_the_page_with_its_message(self):
        for row in self.rows:
            with self.subTest(case=row["case"]):
                self.assertIn(gen.cell(row["what"]), self.text)
                self.assertIn(gen.cell(row["message"]), self.text)

    def test_every_cited_section_is_linked_to_the_pinned_edition(self):
        for case, one in gen.SPEC.items():
            with self.subTest(case=case):
                self.assertIn(f"#jvms-{one.rule})", self.text)
        self.assertNotIn("/se24/", self.text)

    def test_the_two_disagreements_are_marked_and_counted(self):
        differs = [row for row in self.rows if not row["same_error"]]
        self.assertTrue(differs, "the page's whole point is that some case disagrees")
        self.assertIn(f"{len(differs)} of {len(gen.ORDER)} cases end in an error", self.text)
        for row in differs:
            with self.subTest(case=row["case"]):
                self.assertIn(f"`{row['expected']}` | `{row['error']}` ", self.text)

    def test_max_stack_is_still_the_case_the_gate_asked_for(self):
        row = next(one for one in self.rows if one["case"] == "max_stack_is_too_small")
        self.assertEqual(row["expected"], "VerifyError")
        self.assertEqual(row["error"], "ClassFormatError")
        self.assertFalse(row["names_it"])

    def test_the_count_of_silent_messages_is_the_count_on_the_page(self):
        silent = [row for row in self.rows if not row["names_it"]]
        self.assertIn(f"{len(silent)} of {len(gen.ORDER)} messages use none of them",
                      self.text)

    def test_every_quotation_is_printed_next_to_the_section_it_came_from(self):
        for case, one in gen.SPEC.items():
            with self.subTest(case=case):
                self.assertIn(gen.cell(" ... ".join(one.states)), self.text)

    def test_the_random_table_adds_up_to_the_flips_that_were_run(self):
        flips = next(iter(self.results.values()))["random"]
        regions = gen.by_region(self.results)
        self.assertEqual(sum(one["flips"] for one in regions), len(flips))
        accepted = sum(1 for flip in flips if flip["stage"] == "accepted")
        self.assertEqual(sum(one["accepted"] for one in regions), accepted)
        self.assertIn(f"{accepted} of them load and link", self.text)

    def test_the_page_carries_a_hash_of_every_result_file_it_read(self):
        for data in self.results.values():
            raw = (ROOT / data["file"]).read_bytes()
            with self.subTest(file=data["file"]):
                self.assertIn(hashlib.sha256(raw).hexdigest()[:16], self.text)

    def test_the_page_carries_a_hash_of_both_chapters(self):
        index = json.loads((ROOT / gen.JVMS_INDEX).read_text(encoding="utf-8"))
        for one in index["chapters"]:
            if one["chapter"] in gen.CHAPTERS:
                with self.subTest(chapter=one["chapter"]):
                    self.assertIn(one["sha256"][:16], self.text)

    def test_an_instruction_the_specification_does_not_have_is_called_out(self):
        strange = gen.invented(self.results, gen.opcodes())
        self.assertTrue(strange, "the verifier used to name a rewritten opcode")
        for _, name in strange:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", self.text)

    def test_no_table_row_was_broken_by_a_message_containing_a_pipe(self):
        for line in self.text.splitlines():
            if line.startswith("| ") and not set(line) <= set("|- "):
                with self.subTest(line=line[:40]):
                    self.assertEqual(len(re.findall(r"(?<!\\)\|", line)),
                                     line.count("|") - line.count("\\|"))

    def test_the_report_next_to_the_page_exists_and_links_back_to_it(self):
        report = ROOT / "docs/probes/classfile-fuzz.md"
        self.assertTrue(report.exists())
        self.assertIn("../generated/classfile-fuzz.md",
                      report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
