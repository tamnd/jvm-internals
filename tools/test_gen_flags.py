#!/usr/bin/env python3
"""Tests for BP-FLAGS.

Three parts, the same split the other generator tests use. Whether the header parser
reads a macro list the way a C++ compiler would, whether the committed measurements are
internally consistent, and whether the committed page still says what the measurements
say.

Everything here is offline. The header fixtures are written out of the real globals
headers at the pinned tag, shrunk to the shapes that broke the parser at least once:
a wrapper macro around a declaration, a documentation string split across two adjacent
literals, a range and a constraint written outside the closing parenthesis, and an
`#ifdef` that is the file's own include guard rather than a condition on the flag.

  python tools/test_gen_flags.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_flags as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

HEADER = """
#ifndef SHARE_RUNTIME_GLOBALS_HPP
#define SHARE_RUNTIME_GLOBALS_HPP

#define RUNTIME_FLAGS(develop, product, product_pd, notproduct)                \\
                                                                               \\
  product(int, ObjectAlignmentInBytes, 8,                                      \\
          "Default object alignment in bytes, 8 is minimum")                   \\
          range(8, 256)                                                        \\
          constraint(ObjectAlignmentInBytesConstraintFunc, AtParse)            \\
                                                                               \\
  product(bool, PrintInlining, false, DIAGNOSTIC,                              \\
          "Print inlining optimizations")                                      \\
                                                                               \\
  product_pd(intx, ThreadStackSize,                                            \\
          "Thread Stack Size (in Kbytes)")                                     \\
                                                                               \\
  develop(bool, TraceBytecodes, false,                                         \\
          "Trace bytecode execution")                                          \\
                                                                               \\
  JFR_ONLY(product(bool, FlightRecorder, false,                                \\
          "(Deprecated) Enable Flight Recorder"))                              \\
                                                                               \\
  product(ccstr, OnSpinWaitInst, "none",                                       \\
          "The instruction to use to implement "                               \\
          "java.lang.Thread.onSpinWait()")                                     \\
                                                                               \\
  product(size_t, MaxHeap, 1*G,                                                \\
          "A heap")                                                            \\

#ifdef ASSERT
  product(bool, OnlyInDebug, true, "there")
#else
  product(bool, NotInDebug, true, "not there")
#endif

define_pd_global(intx, ThreadStackSize, 2048);

#endif // SHARE_RUNTIME_GLOBALS_HPP
"""


def parsed() -> dict[str, dict]:
    return {entry["name"]: entry
            for entry in gen.declarations("globals.hpp", HEADER)}


def results() -> dict[str, dict]:
    found = {}
    for path in sorted((ROOT / gen.RESULTS).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found[data["platform"]] = data
    return found


class TestTheHeaderParser(unittest.TestCase):
    def setUp(self):
        self.found = parsed()

    def test_a_declaration_carries_its_macro_type_default_and_documentation(self):
        entry = self.found["ObjectAlignmentInBytes"]
        self.assertEqual(entry["macro"], "product")
        self.assertEqual(entry["type"], "int")
        self.assertEqual(entry["default"], "8")
        self.assertEqual(entry["documentation"],
                         "Default object alignment in bytes, 8 is minimum")

    def test_a_range_and_a_constraint_written_after_the_closing_paren_are_found(self):
        # Both are separate macro calls outside the declaration's parentheses, joined to
        # it by nothing but a line continuation.
        entry = self.found["ObjectAlignmentInBytes"]
        self.assertEqual(entry["range"], ("8", "256"))
        self.assertEqual(entry["constraint"],
                         ["ObjectAlignmentInBytesConstraintFunc", "AtParse"])

    def test_a_flag_with_no_range_says_so_rather_than_guessing_one(self):
        self.assertIsNone(self.found["PrintInlining"]["range"])
        self.assertIsNone(self.found["PrintInlining"]["constraint"])

    def test_an_attribute_is_read_as_an_attribute_and_not_as_a_default(self):
        entry = self.found["PrintInlining"]
        self.assertEqual(entry["default"], "false")
        self.assertEqual(entry["attributes"], ["DIAGNOSTIC"])

    def test_a_pd_flag_has_no_default_in_the_shared_header(self):
        # `product_pd` takes no default argument at all, so reading the third argument as
        # one would make the documentation string the default of every pd flag.
        entry = self.found["ThreadStackSize"]
        self.assertIsNone(entry["default"])
        self.assertEqual(entry["documentation"], "Thread Stack Size (in Kbytes)")

    def test_a_declaration_wrapped_in_another_macro_is_still_a_declaration(self):
        # The three Flight Recorder flags are inside `JFR_ONLY(...)`, so they are not at
        # the start of a line and an anchored pattern misses all three.
        self.assertIn("FlightRecorder", self.found)
        self.assertEqual(self.found["FlightRecorder"]["type"], "bool")

    def test_two_adjacent_string_literals_are_one_documentation_string(self):
        self.assertEqual(
            self.found["OnSpinWaitInst"]["documentation"],
            "The instruction to use to implement java.lang.Thread.onSpinWait()")

    def test_the_macro_names_in_the_define_parameter_list_are_not_declarations(self):
        # `RUNTIME_FLAGS(develop, product, ...)` contains every macro name this looks
        # for, followed by punctuation rather than by a type and an identifier.
        self.assertNotIn("RUNTIME_FLAGS", self.found)
        self.assertEqual(len([n for n in self.found if n.islower()]), 0)

    def test_the_files_own_include_guard_is_not_a_condition_on_its_flags(self):
        # Every flag in the file is inside `#ifndef SHARE_RUNTIME_GLOBALS_HPP`, and
        # reporting that as the reason a flag is missing explained seventy five
        # Shenandoah flags with the wrong sentence.
        self.assertEqual(self.found["ObjectAlignmentInBytes"]["guards"], ())

    def test_a_real_condition_is_recorded_with_its_else_branch_named(self):
        self.assertEqual(self.found["OnlyInDebug"]["guards"], ("#ifdef ASSERT",))
        self.assertEqual(self.found["NotInDebug"]["guards"], ("#else of #ifdef ASSERT",))

    def test_a_pd_default_is_read_from_the_platform_header(self):
        supplied = gen.pd_globals("globals_bsd_aarch64.hpp", HEADER)
        self.assertEqual([(e["name"], e["default"]) for e in supplied],
                         [("ThreadStackSize", "2048")])

    def test_every_declaration_says_which_line_of_which_file_it_is_on(self):
        for name, entry in self.found.items():
            with self.subTest(flag=name):
                self.assertEqual(entry["file"], "globals.hpp")
                self.assertGreater(entry["line"], 0)
                self.assertIn(f"{name},", HEADER.splitlines()[entry["line"] - 1])


class TestReadingADefault(unittest.TestCase):
    def test_the_three_kinds_of_value_that_are_not_numbers(self):
        self.assertEqual(gen.literal("true"), "true")
        self.assertEqual(gen.literal('"none"'), "none")
        self.assertEqual(gen.literal("nullptr"), "")

    def test_the_size_suffixes_the_headers_write_defaults_in(self):
        self.assertEqual(gen.literal("1*G"), str(1024 ** 3))
        self.assertEqual(gen.literal("64*K"), "65536")

    def test_an_integer_division_is_an_integer(self):
        # `max_jint / 4` is 536870911 to a C++ compiler and 536870911.75 to Python, and
        # six declared bounds disagreed with the VM until this was true.
        self.assertEqual(gen.literal("max_jint / 4"), "536870911")

    def test_a_decimal_default_keeps_its_fraction(self):
        self.assertEqual(gen.literal("1.5"), "1.5")

    def test_an_expression_that_depends_on_the_build_is_not_guessed_at(self):
        for expr in ("trueInDebug", "NOT_LP64(4*M) LP64_ONLY(512*M)",
                     "os::vm_page_size()", "DEBUG_ONLY(1) NOT_DEBUG(0)"):
            with self.subTest(expr=expr):
                self.assertIsNone(gen.literal(expr))


class TestWhyAFlagIsMissing(unittest.TestCase):
    def test_a_develop_flag_is_explained_by_being_a_develop_flag(self):
        entry = parsed()["TraceBytecodes"]
        self.assertIn("develop", gen.missing_reason(entry, set()))

    def test_a_guarded_flag_is_explained_by_its_guard(self):
        entry = parsed()["NotInDebug"]
        self.assertEqual(gen.missing_reason(entry, set()),
                         "declared in the `#else` of `#ifdef ASSERT`")

    def test_a_flag_from_a_component_this_build_left_out_is_explained_by_its_file(self):
        entry = parsed()["MaxHeap"]
        self.assertIsNone(gen.missing_reason(entry, set()))
        self.assertIn("does not contain", gen.missing_reason(entry, {"globals.hpp"}))


class TestTheCommittedResults(unittest.TestCase):
    """Whether the measurements on disk are consistent with themselves."""

    def setUp(self):
        self.results = results()

    def test_there_are_results_from_more_than_one_environment(self):
        self.assertGreater(len(self.results), 1)

    def test_every_result_says_what_it_is_and_when(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["probe"], "vmflags")
                self.assertEqual(data["issue"], 15)
                self.assertTrue(data["measured"])
                self.assertGreater(data["processors"], 0)

    def test_every_result_is_the_build_the_pin_names(self):
        pinned = gen.load_pin(ROOT)
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["java_build"], pinned["jdk_build"])

    def test_a_flag_has_a_type_a_value_an_origin_and_a_kind(self):
        for name, data in self.results.items():
            for flag, entry in data["flags"].items():
                with self.subTest(environment=name, flag=flag):
                    self.assertIn(entry["type"], data["counts"]["by_type"])
                    self.assertIn(entry["origin"],
                                  ("default", "ergonomic", "command line"))
                    self.assertIsInstance(entry["kind"], list)

    def test_every_kind_word_is_one_the_page_has_a_sentence_for(self):
        # A kind word the VM prints and this does not know about would be a new gate or
        # a new compiler, printed into a table as a bare word with no explanation.
        for name, data in self.results.items():
            for flag, entry in data["flags"].items():
                for word in entry["kind"]:
                    with self.subTest(environment=name, flag=flag, word=word):
                        self.assertIn(word, gen.KINDS)

    def test_the_counts_are_the_flags_counted(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                counted = data["counts"]
                self.assertEqual(counted["reported"], len(data["flags"]))
                self.assertEqual(sum(counted["by_type"].values()), len(data["flags"]))
                self.assertEqual(sum(counted["by_origin"].values()), len(data["flags"]))
                self.assertEqual(counted["with_a_range"], len(data["ranges"]))

    def test_the_only_flag_set_on_the_command_line_is_the_one_doing_the_asking(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                asked = [flag for flag, entry in data["flags"].items()
                         if entry["origin"] == "command line"]
                self.assertEqual(asked, ["PrintFlagsFinal"])

    def test_a_flag_that_would_not_sit_still_has_no_value_recorded(self):
        # Three runs of the identical command, and any flag that answered differently
        # keeps its name and loses its value, because recording the first sample of a
        # randomised address would make every rerun of this probe a diff.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertTrue(data["unstable_between_runs"])
                for flag in data["unstable_between_runs"]:
                    entry = data["flags"][flag]
                    self.assertIsNone(entry["value"])
                    self.assertTrue(entry["unstable"])

    def test_a_range_belongs_to_a_flag_and_has_both_ends(self):
        for name, data in self.results.items():
            for flag, bounds in data["ranges"].items():
                with self.subTest(environment=name, flag=flag):
                    self.assertIn(flag, data["flags"])
                    self.assertTrue(bounds["min"])
                    self.assertTrue(bounds["max"])

    def test_unlocking_changes_what_may_be_set_and_not_what_is_listed(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["counts"]["reported_when_unlocked"],
                                 data["counts"]["reported"])

    def test_every_gate_that_should_refuse_refused_and_said_why(self):
        for name, data in self.results.items():
            for gate in data["gates"]:
                with self.subTest(environment=name, gate=gate["name"]):
                    if "unlocked" in gate["name"]:
                        self.assertEqual(gate["exit"], 0)
                        continue
                    self.assertNotEqual(gate["exit"], 0)
                    self.assertTrue(gate["message"])
                    self.assertTrue(gate["options"])

    def test_no_two_refusals_are_worded_the_same(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                said = [gate["message"] for gate in data["gates"] if gate["exit"]]
                self.assertEqual(len(said), len(set(said)))

    def test_a_scenario_moved_flags_that_the_baseline_also_reported(self):
        for name, data in self.results.items():
            for scenario in data["scenarios"]:
                for flag in scenario["changed"]:
                    with self.subTest(environment=name, scenario=scenario["name"],
                                      flag=flag):
                        self.assertIn(flag, data["flags"])
                        self.assertNotIn(flag, data["unstable_between_runs"])

    def test_the_two_environments_agree_about_what_the_jdk_is(self):
        # Type and kind are properties of the build. A disagreement means the two result
        # files are not the same JDK, whatever their `java_build` says.
        names = sorted(self.results)
        first = self.results[names[0]]["flags"]
        for other in names[1:]:
            for flag, entry in self.results[other]["flags"].items():
                if flag not in first:
                    continue
                with self.subTest(flag=flag, environment=other):
                    self.assertEqual(entry["type"], first[flag]["type"])
                    self.assertEqual(sorted(entry["kind"]),
                                     sorted(first[flag]["kind"]))


class TestTheCommittedPage(unittest.TestCase):
    """Whether the page on disk still says what the results files say.

    The generator's own `--check` reads 22 headers off the network. This is the half
    that can be checked offline, and it is the half that goes stale when somebody reruns
    the probe and forgets the generator.
    """

    def setUp(self):
        self.results = results()
        self.text = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")

    def test_the_page_is_there_and_says_it_is_generated(self):
        self.assertIn("tools/gen_flags.py", self.text)
        self.assertIn("Do not edit it, edit a source", self.text)

    def test_every_environment_measured_is_named_on_the_page(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_the_page_reports_the_number_of_flags_each_environment_reported(self):
        for name, data in self.results.items():
            row = [line for line in self.text.splitlines()
                   if line.startswith(f"| `{name}` | ")]
            self.assertTrue(row, name)
            self.assertIn(gen.commas(data["counts"]["reported"]), " ".join(row))

    def test_every_refusal_measured_is_printed_with_what_the_vm_said(self):
        for data in self.results.values():
            for gate in data["gates"]:
                if not gate["exit"]:
                    continue
                with self.subTest(gate=gate["name"]):
                    self.assertIn(gate["message"], self.text)

    def test_every_option_that_was_tried_is_named_on_the_page(self):
        for data in self.results.values():
            for scenario in data["scenarios"]:
                with self.subTest(scenario=scenario["name"]):
                    self.assertIn(f"`{' '.join(scenario['options'])}`", self.text)

    def test_the_flag_that_will_not_sit_still_is_named_rather_than_dropped(self):
        for data in self.results.values():
            for flag in data["unstable_between_runs"]:
                self.assertIn(f"`{flag}`", self.text)

    def test_the_page_carries_a_hash_of_every_header_it_read(self):
        for path, _ in gen.HEADERS:
            self.assertIn(f"`{path}`", self.text)
        self.assertEqual(self.text.count("| `src/hotspot/"), len(gen.HEADERS))

    def test_the_report_next_to_the_page_exists_and_links_back_to_it(self):
        report = ROOT / "docs/probes/vmflags.md"
        self.assertTrue(report.exists())
        self.assertIn("../generated/flags.md", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
