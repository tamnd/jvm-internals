#!/usr/bin/env python3
"""Tests for the bytecode instruction table.

The generator joins three sources that nobody can diff by eye, so the tests are split
the same way the generator is: whether it reads each source correctly, whether it stops
when two sources disagree, and whether the committed results say what the page says.

Everything here is offline. The parsers are exercised against small fixtures written out
of the real files rather than against the network, because a test that needs
raw.githubusercontent.com to be up is a test that reports GitHub's weather.

  python tools/test_gen_opcodes.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_opcodes as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Six lines out of bytecodes.hpp, chosen for the three shapes the file uses: a value
# written out, a bare name meaning one more than the last, and a name defined earlier.
HEADER = """
class Bytecodes: AllStatic {
 public:
  enum Code {
    _illegal              =  -1,
    _nop                  =   0, // 0x00
    _aconst_null          =   1, // 0x01
    _iconst_m1            ,      // 0x02
    number_of_java_codes  ,
    _fast_agetfield       = number_of_java_codes,
    _shouldnotreachhere   ,
    number_of_codes
  };
"""

# Four def rows, copied out of BYTECODES_DO. The trailing backslash is the macro
# continuation the real file has on every row, and the last field is the code a
# rewritten form was rewritten from, which is the row's own name when it is not one.
TABLE = '''
  def(_nop           , "nop"           , "b"  , nullptr, T_VOID   ,  0, false, _nop     ) \\
  def(_iload         , "iload"         , "bi" , "wbii" , T_INT    ,  1, false, _iload   ) \\
  def(_getfield      , "getfield"      , "bJJ", nullptr, T_ILLEGAL,  0, true , _getfield) \\
  def(_fast_agetfield, "fast_agetfield", "bJJ", nullptr, T_OBJECT ,  0, true , _getfield) \\
'''


class TestTheHeader(unittest.TestCase):
    def setUp(self):
        self.codes = gen.enum(HEADER)

    def test_a_written_value_is_taken_as_written(self):
        self.assertEqual(self.codes["_nop"], 0)
        self.assertEqual(self.codes["_illegal"], -1)

    def test_a_bare_name_is_one_more_than_the_last(self):
        # The form most of the file uses, and the one that puts every row of the table
        # against the wrong opcode if it is read wrongly.
        self.assertEqual(self.codes["_iconst_m1"], 2)
        self.assertEqual(self.codes["number_of_java_codes"], 3)

    def test_a_name_defined_earlier_resolves_to_that_number(self):
        self.assertEqual(self.codes["_fast_agetfield"],
                         self.codes["number_of_java_codes"])
        self.assertEqual(self.codes["_shouldnotreachhere"], 4)

    def test_a_trailing_comment_is_not_part_of_the_value(self):
        self.assertEqual(self.codes["_aconst_null"], 1)

    def test_a_value_that_names_nothing_stops_the_run(self):
        broken = HEADER.replace("= number_of_java_codes", "= whatever_this_is")
        with self.assertRaises(SystemExit):
            gen.enum(broken)

    def test_an_enum_that_lost_its_shape_stops_the_run(self):
        # The check that catches the header being reorganised, rather than the parser
        # returning three entries and the generator believing them.
        with self.assertRaises(SystemExit):
            gen.enum("enum Code {\n  _something,\n};\n")


class TestTheTableRows(unittest.TestCase):
    def rows(self, text: str) -> dict[str, dict]:
        found = {}
        for line in text.splitlines():
            match = gen.DEF.match(line)
            if match:
                found[match.group("code")] = match
        return found

    def setUp(self):
        self.rows = self.rows(TABLE)

    def test_every_row_of_the_fixture_parses(self):
        self.assertEqual(sorted(self.rows),
                         ["_fast_agetfield", "_getfield", "_iload", "_nop"])

    def test_a_missing_format_is_read_as_missing_rather_than_as_the_word(self):
        self.assertIsNone(gen.string_or_none("nullptr"))
        self.assertEqual(gen.string_or_none('"wbii"'), "wbii")

    def test_the_wide_format_is_the_fourth_field(self):
        self.assertEqual(gen.string_or_none(self.rows["_iload"].group("wide")), "wbii")
        self.assertIsNone(gen.string_or_none(self.rows["_nop"].group("wide")))

    def test_a_table_that_lost_its_shape_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.defs(TABLE)


class TestLengthAndOperands(unittest.TestCase):
    def test_one_character_is_one_byte(self):
        self.assertEqual(gen.length("b"), 1)
        self.assertEqual(gen.length("bJJ"), 3)

    def test_an_empty_format_is_variable_and_a_missing_one_is_neither(self):
        self.assertIsNone(gen.length(""))
        self.assertIsNone(gen.length(None))
        self.assertEqual(gen.operands(""), "variable")
        self.assertEqual(gen.operands(None), "no short form")

    def test_a_run_of_one_character_is_one_operand(self):
        # The bug this is here for: `boooo` read character by character says goto_w has
        # four branch offsets, when it has one that is four bytes wide.
        self.assertEqual(gen.operands("boooo"), "a 4 byte branch offset")
        self.assertEqual(gen.runs("boooo"), [("b", 1), ("o", 4)])

    def test_two_different_operands_are_two_phrases(self):
        self.assertEqual(gen.operands("bic"),
                         "a 1 byte local variable index, a 1 byte constant")

    def test_an_uppercase_character_says_native_order(self):
        # The single most useful thing in the format strings, and the thing that makes
        # a bytecode dump out of a running VM differ from javap on the same class.
        self.assertIn("native order", gen.operands("bJJ"))
        self.assertNotIn("native order", gen.operands("bkk"))

    def test_padding_bytes_are_not_described_as_an_operand(self):
        self.assertEqual(gen.operands("b__"), "2 bytes nothing reads")
        self.assertEqual(gen.operands("b_"), "1 byte nothing reads")

    def test_an_opcode_with_no_operands_says_so(self):
        self.assertEqual(gen.operands("b"), "none")


class TestTheSpecificationPage(unittest.TestCase):
    def test_a_padded_number_still_matches(self):
        # 201 of 205 opcodes matched before this, and the four that did not were the
        # two-digit ones the page pads so the column lines up.
        row = ('<code class="literal">&nbsp;96&nbsp;(0x60)</code>'
               '&nbsp;&nbsp;&nbsp;&nbsp;iadd')
        found = gen.SPEC_ROW.search(row)
        self.assertIsNotNone(found)
        self.assertEqual(int(found.group("number")), 96)
        self.assertEqual(found.group("name"), "iadd")

    def test_an_unpadded_number_matches_too(self):
        row = '<code class="literal">187&nbsp;(0xbb)</code>&nbsp;new'
        found = gen.SPEC_ROW.search(row)
        self.assertIsNotNone(found)
        self.assertEqual(found.group("name"), "new")

    def test_every_family_member_links_to_its_family(self):
        for family, members in gen.FAMILIES.items():
            for member in members:
                with self.subTest(mnemonic=member):
                    self.assertEqual(gen.anchor_for(member), family)

    def test_an_instruction_the_specification_does_not_fold_links_to_itself(self):
        self.assertEqual(gen.anchor_for("invokedynamic"), "invokedynamic")

    def test_no_mnemonic_belongs_to_two_families(self):
        seen = [member for members in gen.FAMILIES.values() for member in members]
        self.assertEqual(len(seen), len(set(seen)))


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
        import tempfile
        with tempfile.TemporaryDirectory() as where:
            self.write(pathlib.Path(where) / "index.json", "a" * 64)
            with self.assertRaises(SystemExit) as stop:
                gen.check_the_index("b" * 64)
            # The message has to name the fix, because the reader of it is somebody who
            # ran a generator and got told a specification changed.
            self.assertIn("gen_jvms_index.py", str(stop.exception))

    def test_a_chapter_that_did_not_move_is_reported_in_the_provenance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as where:
            self.write(pathlib.Path(where) / "index.json", "c" * 64)
            self.assertIn("jvms-index.json", gen.check_the_index("c" * 64))


def world() -> tuple[dict, dict, dict, dict, set[str]]:
    """A small consistent set of sources, for the tests that break one thing at a time."""
    spec = {0: "nop", 21: "iload", 132: "iinc", 202: "breakpoint",
            254: "impdep1", 255: "impdep2"}
    codes = {"_nop": 0, "_iload": 21, "_iinc": 132, "_breakpoint": 202,
             "number_of_java_codes": 203, "_fast_aload_0": 203, "number_of_codes": 204}

    def row(mnemonic, fmt, wide, java, depth=0, trap=False):
        return {"mnemonic": mnemonic, "format": fmt, "wide_format": wide,
                "result": "T_VOID", "depth": depth, "can_trap": trap,
                "java_code": java, "line": 1}

    rows = {
        "_nop": row("nop", "b", None, "_nop"),
        "_iload": row("iload", "bi", "wbii", "_iload", depth=1),
        "_iinc": row("iinc", "bic", "wbiicc", "_iinc"),
        "_breakpoint": row("breakpoint", "", None, "_breakpoint"),
        "_fast_aload_0": row("fast_aload_0", "b", None, "_nop", depth=1),
    }
    api = {
        0: {"name": "NOP", "size": 1, "kind": "NOP", "wide": False},
        21: {"name": "ILOAD", "size": 2, "kind": "LOAD", "wide": False},
        132: {"name": "IINC", "size": 3, "kind": "INCREMENT", "wide": False},
        (gen.WIDE_PREFIX << 8) | 21: {"name": "ILOAD_W", "size": 4, "kind": "LOAD",
                                      "wide": True},
        (gen.WIDE_PREFIX << 8) | 132: {"name": "IINC_W", "size": 6,
                                       "kind": "INCREMENT", "wide": True},
    }
    anchors = set(gen.FAMILIES) | {"nop", "iload", "iinc"}
    return spec, codes, rows, api, anchors


class TestWhatStopsTheGenerator(unittest.TestCase):
    """One test per source having moved. Each is a real way these three can drift."""

    def setUp(self):
        self.spec, self.codes, self.rows, self.api, self.anchors = world()

    def check(self):
        return gen.verify(self.spec, self.codes, self.rows, self.api, self.anchors)

    def test_the_fixture_agrees_with_itself(self):
        notes = self.check()
        self.assertTrue(any("wide" in note for note in notes))

    def test_an_opcode_the_specification_has_and_hotspot_does_not(self):
        self.spec[42] = "somethingnew"
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_calling_the_same_number_different_names(self):
        self.rows["_iload"]["mnemonic"] = "iload_but_renamed"
        with self.assertRaises(SystemExit):
            self.check()

    def test_hotspot_defining_an_opcode_the_specification_reserves(self):
        self.codes["_impdep1"] = 254
        self.rows["_impdep1"] = dict(self.rows["_nop"], mnemonic="impdep1",
                                     java_code="_impdep1")
        with self.assertRaises(SystemExit):
            self.check()

    def test_hotspot_having_a_java_code_the_specification_does_not_list(self):
        self.codes["_mystery"] = 100
        self.rows["_mystery"] = dict(self.rows["_nop"], mnemonic="mystery",
                                     java_code="_mystery")
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_wide_form_whose_size_disagrees_with_hotspot(self):
        self.api[(gen.WIDE_PREFIX << 8) | 21]["size"] = 5
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_wide_constant_that_decomposes_to_nothing(self):
        self.api[(gen.WIDE_PREFIX << 8) | 99] = {"name": "MADE_UP_W", "size": 4,
                                                 "kind": "LOAD", "wide": True}
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_fixed_length_that_disagrees_with_hotspot(self):
        self.api[21]["size"] = 3
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_disagreeing_about_how_many_wide_forms_there_are(self):
        self.rows["_nop"]["wide_format"] = "wb"
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_class_file_api_gaining_a_constant_for_wide_itself(self):
        # The whole of section 3.3 rests on `wide` having no constant of its own, so
        # the API growing one has to stop the page rather than change a number in it.
        self.api[gen.WIDE_PREFIX] = {"name": "WIDE", "size": 1, "kind": "OTHER",
                                     "wide": False}
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_rewritten_form_that_names_no_original(self):
        self.rows["_fast_aload_0"]["java_code"] = "_not_a_code"
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_format_string_that_does_not_start_with_an_opcode(self):
        self.rows["_iload"]["format"] = "ib"
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_format_character_nothing_here_knows(self):
        self.rows["_iload"]["format"] = "bz"
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_specification_section_that_was_renamed(self):
        self.anchors.discard("if_cond")
        with self.assertRaises(SystemExit):
            self.check()

    def test_an_instruction_with_no_section_to_link_to(self):
        self.anchors.discard("iinc")
        with self.assertRaises(SystemExit):
            self.check()


def results() -> dict[str, dict]:
    files = sorted((ROOT / gen.RESULTS).glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheMeasurements(unittest.TestCase):
    def setUp(self):
        self.results = results()

    def test_there_is_more_than_one_environment(self):
        # One environment measuring one copy of java.base would make the census a fact
        # about a machine. Two make it a fact about the code.
        self.assertGreaterEqual(len(self.results), 2)

    def test_every_result_says_which_probe_and_issue_it_came_from(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["probe"], "opcodes")
                self.assertEqual(data["issue"], 15)
                self.assertTrue(data["java_build"])
                self.assertTrue(data["measured"])

    def test_every_opcode_has_a_count_including_the_zeroes(self):
        # A missing zero and a measured zero look the same in a results file afterwards,
        # which is why the probe prints every opcode rather than only the ones it saw.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sorted(data["opcodes"]), sorted(data["counts"]))

    def test_the_counts_add_up_to_the_scan_total(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sum(data["counts"].values()),
                                 data["scan"]["instructions"])

    def test_nothing_in_the_runtime_image_failed_to_parse(self):
        # A class file inside the JDK that the JDK's own class file API cannot read
        # would be the most interesting line in the output, so it is not allowed to
        # pass unnoticed.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["unreadable"], 0)
                self.assertEqual(data["scan"]["unreadable_files"], [])

    def test_the_module_scanned_is_the_one_every_image_has(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["module"], "java.base")

    def test_every_environment_reports_the_same_enum(self):
        # An enum is a property of the JDK, not of the machine, so this passing is what
        # makes it honest to merge the two into one table.
        merged = gen.api_table(self.results)
        self.assertEqual(len(merged), len(next(iter(self.results.values()))["opcodes"]))

    def test_two_different_jdks_under_one_pin_stop_the_run(self):
        made_up = json.loads(json.dumps(self.results))
        first = sorted(made_up)[0]
        made_up[first]["opcodes"]["NOP"]["size"] = 99
        with self.assertRaises(SystemExit):
            gen.api_table(made_up)

    def test_the_census_is_summed_and_not_averaged(self):
        counts = gen.census(self.results)
        for name in counts:
            self.assertEqual(counts[name],
                             sum(data["counts"][name] for data in self.results.values()))

    def test_the_widest_slot_never_exceeds_the_highest_slot(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                for opcode, slot in data["widest_slot"].items():
                    self.assertLessEqual(slot, data["scan"]["highest_local_slot"],
                                         opcode)

    def test_the_wide_forms_are_the_prefix_folded_into_the_opcode(self):
        for name, data in self.results.items():
            for opcode, entry in data["opcodes"].items():
                if entry["wide"]:
                    with self.subTest(environment=name, opcode=opcode):
                        base = entry["bytecode"] - (gen.WIDE_PREFIX << 8)
                        self.assertTrue(0 <= base <= 255)


class TestTheCommittedPage(unittest.TestCase):
    """Whether the page on disk still says what the results files say.

    The generator's own `--check` compares against all three sources and needs the
    network for two of them. This is the half that can be checked offline, and it is
    the half that would go stale if somebody reran the probe and forgot the generator.
    """

    def setUp(self):
        self.results = results()
        self.text = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")

    def test_the_page_is_there_and_says_it_is_generated(self):
        self.assertIn("tools/gen_opcodes.py", self.text)
        self.assertIn("Do not edit it, edit a source", self.text)

    def test_every_environment_measured_is_named_on_the_page(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_the_total_on_the_page_is_the_total_in_the_results(self):
        total = sum(sum(data["counts"].values()) for data in self.results.values())
        self.assertIn(f"{total:,} instructions", self.text)

    def test_the_most_common_instruction_is_reported_with_its_measured_count(self):
        counts = gen.census(self.results)
        top = max(counts.items(), key=lambda item: item[1])
        self.assertIn(f"| `{top[0].lower()}` | {top[1]:,} |", self.text)

    def test_every_opcode_that_never_occurred_is_listed_as_never(self):
        counts = gen.census(self.results)
        never = [name.lower() for name, count in counts.items() if not count]
        self.assertTrue(never)
        tail = self.text.split("Never once,")[1]
        for name in never:
            self.assertIn(f"`{name}`", tail, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
