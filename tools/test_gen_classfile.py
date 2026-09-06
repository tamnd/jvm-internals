#!/usr/bin/env python3
"""Tests for the class file structure section.

Same split as the opcode tests, because the generator has the same shape: whether it
reads each of its three sources correctly, whether it stops when two of them disagree,
and whether the committed results still say what the committed page says.

Everything here is offline. The parsers run against small fixtures written out of the
real specification page and the real header, because a test that needs docs.oracle.com
to be up is a test that reports Oracle's weather.

  python tools/test_gen_classfile.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_classfile as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# One long table split the way Oracle splits them, and one short one, so the fixture
# covers the bug that lost eleven rows of table 4.7-C: a `(cont.)` part carries the same
# number as the part above it and has to extend that table rather than replace it.
PAGE = """
<b>Table&nbsp;4.4-A.&nbsp;Constant pool tags</b>
<tbody>
<tr><td>CONSTANT_Utf8</td><td>1</td><td>&#167;4.4.7</td></tr>
<tr><td>CONSTANT_Class</td><td>7</td><td>&#167;4.4.1</td></tr>
</tbody>
<b>Table&nbsp;4.4-A (cont.).&nbsp;Constant pool tags</b>
<tbody>
<tr><td>CONSTANT_Module</td><td>19</td><td>&#167;4.4.11</td></tr>
</tbody>
<b>Table&nbsp;4.7-C.&nbsp;Attribute locations</b>
<tbody>
<tr><td>Module , ModulePackages , ModuleMainClass</td><td>ClassFile</td></tr>
<tr><td>Signature</td><td>ClassFile , field_info , method_info</td></tr>
</tbody>
"""

# The guard in `tables` wants a chapter's worth of tables before it believes the page, so
# the fixture carries eight more that say nothing.
FILLER = "".join(
    f"<b>Table&nbsp;4.9-{letter}.&nbsp;filler</b>\n<tbody>\n"
    f"<tr><td>nothing</td><td>0</td></tr>\n</tbody>\n"
    for letter in "ABCDEFGH")

CHAPTER = PAGE + FILLER

STRUCT = """
<pre class="screen">
ClassFile {
    u4             magic;
    u2             constant_pool_count;
    cp_info        constant_pool[constant_pool_count-1];
    u2             interfaces[interfaces_count];
}
</pre>
"""

# Six lines out of classfile_constants.h.template, in the two shapes it uses.
HEADER = """
enum {
    JVM_CONSTANT_Utf8 = 1,
    JVM_CONSTANT_Unicode,               /* unused */
    JVM_CONSTANT_Integer = 3,
    JVM_CONSTANT_Class = 7
};

enum {
    JVM_ACC_PUBLIC        = 0x0001,
    JVM_ACC_STRICT        = 0x0800,
    /* JVM_ACC_NOTREAL    = 0x0002, */
};
"""


class TestTheSpecificationPage(unittest.TestCase):
    def test_markup_inside_a_sentence_does_not_leave_a_space_before_the_semicolon(self):
        # Oracle marks up a keyword as its own element, so the naive strip gives
        # `Declared public ;` and a whole flag table reads like a bad scan.
        self.assertEqual(
            gen.plain("Declared <code>public</code> ; may be accessed ."),
            "Declared public; may be accessed.")

    def test_a_cited_section_keeps_its_parentheses_closed_up(self):
        self.assertEqual(gen.plain("same nest ( &#167;5.4.4 )"), "same nest (§5.4.4)")

    def test_a_continuation_table_extends_the_table_above_it(self):
        found = gen.tables(CHAPTER)
        self.assertEqual([row[0] for row in found["4.4-A"]][:3],
                         ["CONSTANT_Utf8", "CONSTANT_Class", "CONSTANT_Module"])

    def test_a_page_with_almost_no_tables_stops_the_run(self):
        # The guard that catches Oracle republishing the chapter in a new shape, rather
        # than the generator writing a page with three rows on it.
        with self.assertRaises(SystemExit):
            gen.tables(PAGE)

    def test_a_table_with_no_body_after_it_is_skipped_rather_than_guessed_at(self):
        found = gen.tables(CHAPTER + "<b>Table&nbsp;4.9-Z.&nbsp;orphan</b>")
        self.assertNotIn("4.9-Z", found)

    def test_a_structure_block_becomes_items_in_order(self):
        found = gen.structures(STRUCT)
        self.assertEqual([item["name"] for item in found["ClassFile"]],
                         ["magic", "constant_pool_count", "constant_pool", "interfaces"])
        self.assertEqual(found["ClassFile"][0]["type"], "u4")

    def test_an_array_item_carries_the_expression_that_sizes_it(self):
        found = gen.structures(STRUCT)["ClassFile"]
        self.assertEqual(found[2]["count"], "constant_pool_count-1")
        self.assertEqual(found[0]["count"], "")

    def test_one_row_can_name_three_attributes_and_three_places(self):
        # Both cells hold comma separated lists, which is the specification saving a page
        # and a parser having to split twice.
        places = gen.allowed_places(gen.tables(CHAPTER)["4.7-C"])
        self.assertEqual(places["ModuleMainClass"], {"ClassFile"})
        self.assertEqual(places["Signature"],
                         {"ClassFile", "field_info", "method_info"})

    def test_a_flag_table_row_is_read_as_a_mask_in_hexadecimal(self):
        rows = gen.flag_names([["ACC_PUBLIC", "0x0001", "Declared public."],
                               ["ACC_MODULE", "0x8000", "Is a module."],
                               ["", "", ""]])
        self.assertEqual(rows, {1: ("ACC_PUBLIC", "Declared public."),
                                0x8000: ("ACC_MODULE", "Is a module.")})


class TestTheHeader(unittest.TestCase):
    def setUp(self):
        self.header = gen.constants(HEADER)

    def test_decimal_and_hexadecimal_both_parse(self):
        self.assertEqual(self.header["JVM_CONSTANT_Utf8"], 1)
        self.assertEqual(self.header["JVM_ACC_STRICT"], 0x0800)

    def test_a_constant_with_no_value_is_not_invented(self):
        # `JVM_CONSTANT_Unicode` has no `=`, and guessing that it is 2 would be reading
        # C rules into a file this generator only greps.
        self.assertNotIn("JVM_CONSTANT_Unicode", self.header)

    def test_a_commented_out_constant_is_not_a_constant(self):
        self.assertNotIn("JVM_ACC_NOTREAL", self.header)

    def test_a_header_that_lost_its_shape_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.constants("enum { SOMETHING_ELSE = 1 };\n")


class TestTheIndexCheck(unittest.TestCase):
    def setUp(self):
        self.saved = gen.JVMS_INDEX

    def tearDown(self):
        gen.JVMS_INDEX = self.saved

    def write(self, tmp: pathlib.Path, sha: str) -> None:
        gen.JVMS_INDEX = tmp
        tmp.write_text(json.dumps(
            {"chapters": [{"chapter": gen.SPEC_CHAPTER, "sha256": sha}]}), "utf-8")

    def test_a_chapter_that_moved_stops_the_run(self):
        with tempfile.TemporaryDirectory() as where:
            self.write(pathlib.Path(where) / "index.json", "a" * 64)
            with self.assertRaises(SystemExit) as stop:
                gen.check_the_index("b" * 64)
            self.assertIn("gen_jvms_index.py", str(stop.exception))

    def test_a_chapter_that_did_not_move_is_reported_in_the_provenance(self):
        with tempfile.TemporaryDirectory() as where:
            self.write(pathlib.Path(where) / "index.json", "c" * 64)
            self.assertIn("jvms-index.json", gen.check_the_index("c" * 64))


class TestJoiningTheEnvironments(unittest.TestCase):
    def data(self) -> dict[str, dict]:
        return {
            "one": {"platform": "linux-aarch64", "api": {"latest_major": 71},
                    "tags": {"1": 10, "7": 2}, "names": {"a": ["x", "y"]},
                    "nested": {"CLASS": {"Code": 3}}},
            "two": {"platform": "darwin-arm64", "api": {"latest_major": 71},
                    "tags": {"1": 5}, "names": {"a": ["y", "z"]},
                    "nested": {"CLASS": {"Code": 4}, "METHOD": {"Code": 1}}},
        }

    def test_a_property_of_the_jdk_has_to_be_the_same_everywhere(self):
        self.assertEqual(gen.one_answer(self.data(), "api"), {"latest_major": 71})

    def test_two_jdks_under_one_pin_stop_the_run(self):
        data = self.data()
        data["two"]["api"]["latest_major"] = 70
        with self.assertRaises(SystemExit) as stop:
            gen.one_answer(data, "api")
        self.assertIn("Two JDKs, one pin", str(stop.exception))

    def test_counts_are_summed_and_not_averaged(self):
        # Each environment scanned its own copy of java.base and the copies differ, so a
        # sum is a count of things seen and a mean is a count of nothing.
        self.assertEqual(gen.summed(self.data(), "tags"), {"1": 15, "7": 2})

    def test_counts_two_levels_deep_are_summed_per_location(self):
        self.assertEqual(gen.summed(self.data(), "nested"),
                         {"CLASS": {"Code": 7}, "METHOD": {"Code": 1}})

    def test_example_names_are_merged_rather_than_made_to_agree(self):
        # Ordered by platform rather than by results file name, so the page does not
        # reorder itself when somebody adds a third environment called `aaa`.
        self.assertEqual(gen.pooled(self.data(), "names"), {"a": ["y", "z", "x"]})

    def test_example_names_stop_at_five(self):
        data = self.data()
        data["one"]["names"]["a"] = list("abcdef")
        self.assertEqual(len(gen.pooled(data, "names")["a"]), 5)


class TestHowNumbersAreWritten(unittest.TestCase):
    def test_a_tag_that_never_occurred_gets_no_share_at_all(self):
        # "less than a hundredth of a per cent" and "not once" are different answers and
        # the second one is the one worth reading.
        self.assertEqual(gen.share(0, 1000), "")
        self.assertEqual(gen.share(1, 1_000_000), "<0.01%")
        self.assertEqual(gen.share(50, 200), "25.00%")

    def test_small_counts_are_words_and_large_ones_are_numerals(self):
        self.assertEqual(gen.word(1), "one")
        self.assertEqual(gen.word(12), "twelve")
        self.assertEqual(gen.word(18), "18")

    def test_a_flag_that_never_moved_is_one_span(self):
        history = {"A": ["METHOD"], "B": ["METHOD"], "C": ["METHOD"]}
        releases = {"A": 45, "B": 46, "C": 47}
        self.assertEqual(gen.spans(history, releases), "`METHOD` at 45 to 47")

    def test_a_flag_that_was_retired_says_where_it_stopped(self):
        history = {"A": [], "B": ["METHOD"], "C": ["METHOD"], "D": []}
        releases = {"A": 45, "B": 46, "C": 60, "D": 61}
        self.assertEqual(gen.spans(history, releases),
                         "nowhere at 45; `METHOD` at 46 to 60; nowhere at 61")


def world() -> tuple[dict, dict, dict, dict, dict, dict, dict, dict]:
    """A small consistent set of sources, for the tests that break one thing at a time."""
    spec = {
        "4.4-A": [["CONSTANT_Utf8", "1", "§4.4.7"], ["CONSTANT_Class", "7", "§4.4.1"]],
        "4.7-A": [["Code", "§4.7.3", "45.3", "1.0.2"],
                  ["Signature", "§4.7.9", "49.0", "5.0"]],
        "4.7-C": [["Code", "method_info"],
                  ["Signature", "ClassFile , field_info , method_info"]],
        "4.1-B": [["ACC_PUBLIC", "0x0001", "Declared public."]],
        "4.5-A": [["ACC_PUBLIC", "0x0001", "Declared public."]],
        "4.6-A": [["ACC_PUBLIC", "0x0001", "Declared public."],
                  ["ACC_STRICT", "0x0800", "Declared strictfp."]],
        "4.7.6-A": [["ACC_PUBLIC", "0x0001", "Marked public in source."]],
    }
    shapes = {"ClassFile": [{"name": name, "type": "u2", "count": ""}
                            for name in gen.ITEM]}
    header = {"JVM_CONSTANT_Utf8": 1, "JVM_CONSTANT_Class": 7,
              "JVM_ACC_PUBLIC": 0x0001, "JVM_ACC_STRICT": 0x0800}
    # ACC_STRICT is the flag that makes this fixture worth having: table 4.6-A still
    # lists it for methods, and this JDK says it applies nowhere at the current version.
    flags = {
        "PUBLIC": {"mask": 0x0001, "source_modifier": True,
                   "locations": ["CLASS", "FIELD", "INNER_CLASS", "METHOD"]},
        "STRICT": {"mask": 0x0800, "source_modifier": True, "locations": []},
    }
    tags = {"1": 100, "7": 20}
    attribute_counts = {"METHOD": {"Code": 10}, "CLASS": {"Signature": 3}}
    pinned = {"jdk_class_file_major": 71, "jvms_class_file_major": 69}
    data = {"osx-arm64": {
        "platform": "darwin-arm64",
        "api": {"latest_major": 71, "latest_minor": 0},
        "releases": {"RELEASE_1": 45, "RELEASE_17": 61, "RELEASE_27": 71},
        "flag_locations": {
            "PUBLIC": {"RELEASE_1": ["CLASS", "FIELD", "METHOD"],
                       "RELEASE_17": ["CLASS", "FIELD", "INNER_CLASS", "METHOD"],
                       "RELEASE_27": ["CLASS", "FIELD", "INNER_CLASS", "METHOD"]},
            "STRICT": {"RELEASE_1": [], "RELEASE_17": ["METHOD"], "RELEASE_27": []},
        },
    }}
    return spec, shapes, header, flags, tags, attribute_counts, pinned, data


class TestWhatStopsTheGenerator(unittest.TestCase):
    """One test per source having moved. Each is a real way these three can drift."""

    def setUp(self):
        (self.spec, self.shapes, self.header, self.flags, self.tags,
         self.attribute_counts, self.pinned, self.data) = world()

    def check(self):
        return gen.verify(self.spec, self.shapes, self.header, self.flags, self.tags,
                          self.attribute_counts, self.pinned, self.data)

    def test_the_fixture_agrees_with_itself(self):
        notes = self.check()
        self.assertTrue(any("class file version 71" in note for note in notes))

    def test_a_flag_this_jdk_retired_does_not_stop_the_run(self):
        # The check has to ask whether any class file version allows the flag, not
        # whether this one does, or it would be asking the specification to forget its
        # own history.
        self.assertEqual(self.flags["STRICT"]["locations"], [])
        self.check()

    def test_the_classfile_structure_gaining_an_item(self):
        self.shapes["ClassFile"].append({"name": "surprise", "type": "u2", "count": ""})
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_specification_defining_a_tag_the_header_lacks(self):
        self.spec["4.4-A"].append(["CONSTANT_Something", "21", "§4.4.13"])
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_disagreeing_about_what_number_a_tag_is(self):
        self.header["JVM_CONSTANT_Class"] = 8
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_tag_that_occurred_and_the_specification_does_not_list(self):
        self.tags["21"] = 5
        with self.assertRaises(SystemExit):
            self.check()

    def test_an_attribute_the_specification_defines_and_places_nowhere(self):
        self.spec["4.7-C"] = [row for row in self.spec["4.7-C"] if row[0] != "Code"]
        with self.assertRaises(SystemExit):
            self.check()

    def test_an_attribute_counted_where_the_specification_forbids_it(self):
        self.attribute_counts["CODE"] = {"Signature": 2}
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_specification_naming_a_flag_the_header_lacks(self):
        self.spec["4.6-A"].append(["ACC_MYSTERY", "0x0200", "Declared mysterious."])
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_disagreeing_about_what_bit_a_flag_is(self):
        self.header["JVM_ACC_PUBLIC"] = 0x0002
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_flag_location_no_class_file_version_allows(self):
        for where in self.data["osx-arm64"]["flag_locations"]["PUBLIC"].values():
            if "INNER_CLASS" in where:
                where.remove("INNER_CLASS")
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_flag_this_jdk_allows_where_the_specification_lists_no_bit(self):
        self.flags["SEALED"] = {"mask": 0x0100, "source_modifier": False,
                                "locations": ["CLASS"]}
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_pin_disagreeing_with_the_class_file_version_this_jdk_writes(self):
        self.pinned["jdk_class_file_major"] = 70
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_specification_edition_ahead_of_the_jdk(self):
        # Impossible unless the pin is wrong, which is the point of noticing it.
        self.pinned["jvms_class_file_major"] = 72
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_specification_edition_level_with_the_jdk_needs_no_note(self):
        self.pinned["jvms_class_file_major"] = 71
        self.assertEqual(self.check(), [])


def results() -> dict[str, dict]:
    files = sorted((ROOT / gen.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheMeasurements(unittest.TestCase):
    def setUp(self):
        self.results = results()

    def test_there_is_more_than_one_environment(self):
        self.assertGreaterEqual(len(self.results), 2)

    def test_every_result_says_which_probe_and_issue_it_came_from(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["probe"], "classfile-census")
                self.assertEqual(data["issue"], 15)
                self.assertTrue(data["java_build"])
                self.assertTrue(data["measured"])

    def test_the_module_scanned_is_the_one_every_image_has(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["module"], "java.base")

    def test_nothing_in_the_runtime_image_failed_to_parse(self):
        # A class file inside the JDK that the JDK's own class file API cannot read
        # would be the most interesting line in the output, so it is not allowed to
        # pass unnoticed.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["unreadable"], 0)
                self.assertEqual(data["scan"]["unreadable_files"], [])

    def test_the_tag_counts_add_up_to_the_pool_entries_counted(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sum(data["tags"].values()),
                                 data["scan"]["pool_entries"])

    def test_every_class_scanned_reported_a_version(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sum(data["versions"].values()),
                                 data["scan"]["classes"])

    def test_the_pool_of_every_class_fits_in_the_largest_one_reported(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertGreaterEqual(data["scan"]["largest_pool"],
                                        data["scan"]["pool_entries"]
                                        // max(data["scan"]["classes"], 1))
                self.assertTrue(data["scan"]["largest_pool_class"])

    def test_every_place_an_attribute_was_counted_is_a_place_the_page_prints(self):
        for name, data in self.results.items():
            for location in data["attribute_counts"]:
                with self.subTest(environment=name, location=location):
                    self.assertIn(location, gen.PLACES)

    def test_every_flag_carries_its_whole_history(self):
        # The per version locations are the one thing here no document has, so a flag
        # that reported its current locations and not its history would be a silent gap.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sorted(data["flags"]), sorted(data["flag_locations"]))
                for flag, history in data["flag_locations"].items():
                    self.assertEqual(sorted(history), sorted(data["releases"]), flag)

    def test_a_flag_is_current_only_where_the_latest_version_puts_it(self):
        for name, data in self.results.items():
            latest = max(data["releases"], key=lambda r: data["releases"][r])
            for flag, entry in data["flags"].items():
                with self.subTest(environment=name, flag=flag):
                    self.assertEqual(sorted(entry["locations"]),
                                     sorted(data["flag_locations"][flag][latest]))

    def test_every_attribute_stability_has_a_sentence_to_print(self):
        # The page explains each stability rather than printing the enum constant, so a
        # JDK that adds one has to stop the generator rather than leak `CP_REFS` into a
        # sentence. This is the offline half of that check.
        for name, data in self.results.items():
            for attribute, entry in data["attributes"].items():
                with self.subTest(environment=name, attribute=attribute):
                    self.assertIn(entry["stability"], gen.STABILITY)

    def test_a_stray_bit_is_recorded_with_somewhere_to_go_and_look(self):
        for name, data in self.results.items():
            for key, entry in data["stray_flags"].items():
                with self.subTest(environment=name, bit=key):
                    self.assertIn(key.rsplit(".", 1)[0], gen.PLACES)
                    self.assertGreater(entry["count"], 0)
                    self.assertTrue(entry["examples"])

    def test_this_jdk_writes_the_class_file_version_the_pin_claims(self):
        pinned = gen.pin()
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["api"]["latest_major"],
                                 pinned["jdk_class_file_major"])


class TestTheCommittedPage(unittest.TestCase):
    """Whether the page on disk still says what the results files say.

    The generator's own `--check` compares against all three sources and needs the
    network for two of them. This is the half that can be checked offline, and it is the
    half that would go stale if somebody reran the probe and forgot the generator.
    """

    def setUp(self):
        self.results = results()
        self.text = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")

    def test_the_page_is_there_and_says_it_is_generated(self):
        self.assertIn("tools/gen_classfile.py", self.text)
        self.assertIn("Do not edit it, edit a source", self.text)

    def test_every_environment_measured_is_named_on_the_page(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_the_totals_on_the_page_are_the_totals_in_the_results(self):
        for field, unit in (("classes", "class files"), ("fields", "fields"),
                            ("methods", "methods"),
                            ("pool_entries", "constant pool entries")):
            total = sum(data["scan"][field] for data in self.results.values())
            self.assertIn(f"{total:,} {unit}", self.text)

    def test_the_largest_pool_is_reported_with_the_class_it_belongs_to(self):
        largest = max(data["scan"]["largest_pool"] for data in self.results.values())
        owner = [data["scan"]["largest_pool_class"] for data in self.results.values()
                 if data["scan"]["largest_pool"] == largest][0]
        self.assertIn(f"{largest:,} slots", self.text)
        self.assertIn(f"`{owner}`", self.text)

    def test_every_tag_that_never_occurred_is_shown_as_never(self):
        totals: dict[str, int] = {}
        for data in self.results.values():
            for tag, count in data["tags"].items():
                totals[tag] = totals.get(tag, 0) + count
        # A tag the specification lists and the scan never saw has an empty share cell
        # rather than a rounded one, and it is named in the sentence under the table.
        self.assertIn("never occur", self.text)
        self.assertNotIn("| 0 | <0.01%", self.text)
        self.assertTrue(totals)

    def test_every_stray_bit_measured_is_named_on_the_page(self):
        for data in self.results.values():
            for key, entry in data["stray_flags"].items():
                location, mask = key.rsplit(".", 1)
                self.assertIn(f"`{gen.PLACES[location]}` | `0x{int(mask):04x}`",
                              self.text)
                for name in entry["examples"]:
                    self.assertIn(f"`{name}`", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
