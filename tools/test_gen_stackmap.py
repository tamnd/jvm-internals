#!/usr/bin/env python3
"""Tests for the stack map table section.

The generator joins four sources and its whole value is that it stops when they stop
agreeing, so the tests are in three parts: whether each source is read correctly, whether
one source moving stops the run, and whether the committed page still says what the
committed results say.

Everything here is offline. The parsers run against fixtures copied out of the real
specification section, the real header, `stackMapTable.hpp` and `verificationType.hpp`,
because a test that needs docs.oracle.com and raw.githubusercontent.com to be up is a
test that reports somebody else's weather.

  python tools/test_gen_stackmap.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_stackmap as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Chapter 4 in miniature: the section this reads, the section after it that says where to
# stop, and one section before it holding a structure with a name the parser would take
# for a frame kind if it read the whole chapter instead of one section.
SPEC = """
<a name="jvms-4.7.3"></a><h3>4.7.3. The Code Attribute</h3>
<pre class="screen">
full_frame {
    u1 not_the_one_you_want;
}
</pre>
<a name="jvms-4.7.4"></a><h3>4.7.4. The StackMapTable Attribute</h3>
<pre class="screen">
StackMapTable_attribute {
    u2              attribute_name_index;
    u4              attribute_length;
    u2              number_of_entries;
    stack_map_frame entries[number_of_entries];
}
</pre>
<p>A stack map frame is at a bytecode offset found by adding
<code class="literal">offset_delta</code> + 1 to the bytecode offset of the previous
frame.</p>
<pre class="screen">
union verification_type_info {
    Top_variable_info;
    Integer_variable_info;
    Object_variable_info;
    Uninitialized_variable_info;
}
</pre>
<pre class="screen">
Top_variable_info {
    u1 tag = ITEM_Top; /* 0 */
}
</pre>
<pre class="screen">
Integer_variable_info {
    u1 tag = ITEM_Integer; /* 1 */
}
</pre>
<pre class="screen">
Object_variable_info {
    u1 tag = ITEM_Object; /* 7 */
    u2 cpool_index;
}
</pre>
<pre class="screen">
Uninitialized_variable_info {
    u1 tag = ITEM_Uninitialized; /* 8 */
    u2 offset;
}
</pre>
<pre class="screen">
union stack_map_frame {
    same_frame;
    chop_frame;
    append_frame;
    full_frame;
}
</pre>
<p>Tags in the range [128-246] are reserved for future use.</p>
<pre class="screen">
same_frame {
    u1 frame_type = SAME; /* 0-63 */
}
</pre>
<pre class="screen">
chop_frame {
    u1 frame_type = CHOP; /* 248-250 */
    u2 offset_delta;
}
</pre>
<p>The value of k is given by the formula 251 - frame_type.</p>
<pre class="screen">
append_frame {
    u1 frame_type = APPEND; /* 252-254 */
    u2 offset_delta;
    verification_type_info locals[frame_type - 251];
}
</pre>
<pre class="screen">
full_frame {
    u1 frame_type = FULL_FRAME; /* 255 */
    u2 offset_delta;
    u2 number_of_locals;
    verification_type_info locals[number_of_locals];
}
</pre>
<b>Table&nbsp;4.7-B.&nbsp;Attributes by class file version</b>
<tbody>
<tr><td>Signature</td><td>49.0</td><td>5.0</td><td>&#167;4.7.9</td></tr>
<tr><td>StackMapTable</td><td>50.0</td><td>6</td><td>&#167;4.7.4</td></tr>
</tbody>
<a name="jvms-4.7.5"></a><h3>4.7.5. The Exceptions Attribute</h3>
<pre class="screen">
same_frame {
    u1 frame_type = NOT_THIS_ONE_EITHER; /* 9 */
}
</pre>
"""

# The item numbers as classfile_constants.h.template writes them, with the comment line
# above them that names the thing, and a second enum so the block finder has to pick.
HEADER = """
enum {
    JVM_CONSTANT_Utf8 = 1,
    JVM_CONSTANT_Integer = 3
};

/* StackMapTable type item numbers */

enum {
    JVM_ITEM_Top                = 0,
    JVM_ITEM_Integer            = 1,
    JVM_ITEM_Object             = 7,
    JVM_ITEM_Uninitialized      = 8
};
"""

# The frame type ranges as stackMapTable.hpp writes them: a range is two entries with
# `_START` and `_END`, a single value is one entry, and both live in the same enum.
FRAME_HPP = """
class StackMapReader : StackObj {
 private:
  enum {
    SAME_FRAME_START                 =   0,
    SAME_FRAME_END                   =  63,
    SAME_LOCALS_1_STACK_ITEM_FRAME_START = 64,
    SAME_LOCALS_1_STACK_ITEM_FRAME_END  = 127,
    RESERVED_START                   = 128,
    RESERVED_END                     = 246,
    SAME_LOCALS_1_STACK_ITEM_EXTENDED = 247,
    CHOP_FRAME_START                 = 248,
    CHOP_FRAME_END                   = 250,
    SAME_FRAME_EXTENDED              = 251,
    APPEND_FRAME_START               = 252,
    APPEND_FRAME_END                 = 254,
    FULL_FRAME                       = 255
  };
};
"""

# The item numbers as the verifier holds them: the nine the format has, a sentinel that is
# not a number at all, and six more written without values so a reader has to count.
TYPE_HPP = """
class VerificationType {
 public:
  enum : uint {
    ITEM_Top = 0,
    ITEM_Integer,
    ITEM_Float,
    ITEM_Double,
    ITEM_Long,
    ITEM_Null,
    ITEM_UninitializedThis,
    ITEM_Object,
    ITEM_Uninitialized,
    ITEM_Bogus = (uint)-1
  };

 private:
  enum {
    ITEM_Boolean = 9, ITEM_Byte, ITEM_Short, ITEM_Char,
    ITEM_Long_2nd, ITEM_Double_2nd
  };
};
"""


class TestTheSpecificationSection(unittest.TestCase):
    def setUp(self):
        self.only = gen.section_of(SPEC)
        self.boxes = gen.blocks(self.only)

    def test_the_section_is_cut_out_before_anything_is_parsed(self):
        # Chapter 4 prints more than a hundred structures and two of them here are called
        # `full_frame` and `same_frame`. Cutting first is what stops a frame kind being
        # read out of the section about a different attribute.
        self.assertNotIn("not_the_one_you_want", self.only)
        self.assertNotIn("NOT_THIS_ONE_EITHER", self.only)

    def test_a_chapter_that_lost_the_section_anchor_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.section_of(SPEC.replace('name="jvms-4.7.4"', 'name="jvms-4.7.40"'))
        self.assertIn("4.7.4", str(stop.exception))

    def test_a_section_that_no_longer_ends_where_it_did_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.section_of(SPEC.replace('name="jvms-4.7.5"', 'name="jvms-4.7.50"'))
        self.assertIn("4.7.5", str(stop.exception))

    def test_a_union_and_a_structure_are_both_read_as_blocks(self):
        self.assertIn("verification_type_info", self.boxes)
        self.assertIn("StackMapTable_attribute", self.boxes)

    def test_the_tags_come_off_the_comment_beside_each_item(self):
        types = gen.spec_types(self.boxes)
        self.assertEqual(sorted(types), [0, 1, 7, 8])
        self.assertEqual(types[7]["structure"], "Object_variable_info")
        self.assertEqual(types[7]["item"], "ITEM_Object")

    def test_a_type_that_carries_an_operand_says_what_it_carries(self):
        types = gen.spec_types(self.boxes)
        self.assertEqual(types[7]["after"], ["u2 cpool_index"])
        self.assertEqual(types[0]["after"], [])

    def test_a_union_member_with_no_structure_printed_stops_the_run(self):
        boxes = dict(self.boxes)
        del boxes["Object_variable_info"]
        with self.assertRaises(SystemExit) as stop:
            gen.spec_types(boxes)
        self.assertIn("Object_variable_info", str(stop.exception))

    def test_two_types_sharing_one_tag_stops_the_run(self):
        boxes = dict(self.boxes)
        boxes["Integer_variable_info"] = ["u1 tag = ITEM_Integer; /* 7 */"]
        with self.assertRaises(SystemExit):
            gen.spec_types(boxes)

    def test_a_range_in_a_comment_reads_as_a_range_and_a_single_value_as_a_point(self):
        frames = gen.spec_frames(self.boxes)
        self.assertEqual((frames["SAME"]["low"], frames["SAME"]["high"]), (0, 63))
        self.assertEqual((frames["FULL_FRAME"]["low"], frames["FULL_FRAME"]["high"]),
                         (255, 255))

    def test_an_array_field_keeps_the_expression_that_sizes_it(self):
        # `locals[frame_type - 251]` is where the append count is defined, and the 251 in
        # it is checked against HotSpot's `SAME_FRAME_EXTENDED` rather than trusted.
        frames = gen.spec_frames(self.boxes)
        self.assertIn("verification_type_info locals[frame_type - 251]",
                      frames["APPEND"]["after"])

    def test_a_frame_structure_without_a_frame_type_item_stops_the_run(self):
        boxes = dict(self.boxes)
        boxes["chop_frame"] = ["u2 offset_delta;"]
        with self.assertRaises(SystemExit) as stop:
            gen.spec_frames(boxes)
        self.assertIn("chop_frame", str(stop.exception))

    def test_the_three_rules_stated_in_prose_are_read_as_numbers(self):
        # None of these three is in a table, a structure, or a comment. They are sentences,
        # and every other source states them as constants, which is the join.
        prose = gen.spec_prose(self.only)
        self.assertEqual(prose["reserved"], (128, 246))
        self.assertEqual(prose["chop_base"], 251)
        self.assertEqual(prose["delta_one"], 1)

    def test_a_section_that_stopped_reserving_a_range_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.spec_prose(self.only.replace("Tags in the range", "Tags in the band"))
        self.assertIn("reserved range", str(stop.exception))

    def test_a_section_that_stopped_stating_the_offset_rule_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.spec_prose(self.only.replace("offset_delta</code> + 1",
                                             "offset_delta</code> plus one"))
        self.assertIn("offset formula", str(stop.exception))

    def test_the_version_the_attribute_arrived_at_comes_off_the_chapter_table(self):
        self.assertEqual(gen.spec_version(SPEC), ["50.0", "6"])

    def test_a_chapter_without_that_row_says_nothing_rather_than_guessing(self):
        self.assertEqual(gen.spec_version(SPEC.replace("StackMapTable</td>", "X</td>")),
                         [])


class TestTheEnums(unittest.TestCase):
    def test_the_block_a_marker_sits_inside_is_the_one_that_is_read(self):
        # The marker is inside the enum, so the brace to open at is the one before it.
        # Taking the next brace after it would read whatever block came next.
        body = gen.body_around(HEADER, "JVM_ITEM_Top", gen.HEADER_PATH)
        self.assertIn("JVM_ITEM_Uninitialized", body)
        self.assertNotIn("JVM_CONSTANT_Utf8", body)

    def test_a_file_that_lost_the_marker_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.body_around(HEADER, "JVM_ITEM_Nothing", gen.HEADER_PATH)
        self.assertIn("JVM_ITEM_Nothing", str(stop.exception))

    def test_the_header_item_numbers_parse(self):
        found = gen.enum(gen.body_around(HEADER, "JVM_ITEM_Top", gen.HEADER_PATH),
                         gen.ENUM_ITEM, "JVM_ITEM_Top", gen.HEADER_PATH)
        self.assertEqual(found["JVM_ITEM_Top"], 0)
        self.assertEqual(found["JVM_ITEM_Uninitialized"], 8)
        self.assertNotIn("JVM_CONSTANT_Utf8", found)

    def test_an_enum_that_lost_its_shape_stops_the_run(self):
        # A regex that silently matches nothing is the failure a generator is least likely
        # to notice, so every file it parses names something it must have found.
        with self.assertRaises(SystemExit) as stop:
            gen.enum("enum { SOMETHING_ELSE = 1 };", gen.ENUM_ITEM, "JVM_ITEM_Top",
                     "somewhere")
        self.assertIn("JVM_ITEM_Top", str(stop.exception))

    def test_items_written_without_a_value_are_counted_rather_than_skipped(self):
        # `ITEM_Boolean = 9, ITEM_Byte, ITEM_Short` on one line is the shape a table
        # retyped by hand gets wrong, and the six values it hides are the whole of 2.4.
        found = gen.enum(TYPE_HPP, gen.ENUM_ITEM, "ITEM_Top", gen.TYPE_HPP)
        self.assertEqual(found["ITEM_Integer"], 1)
        self.assertEqual(found["ITEM_Uninitialized"], 8)
        self.assertEqual(found["ITEM_Byte"], 10)
        self.assertEqual(found["ITEM_Double_2nd"], 14)

    def test_a_sentinel_that_is_not_a_number_is_left_out(self):
        # `ITEM_Bogus = (uint)-1` is a marker for an unset type rather than an item
        # number, and printing it in a table of item numbers would be an invention.
        found = gen.enum(TYPE_HPP, gen.ENUM_ITEM, "ITEM_Top", gen.TYPE_HPP)
        self.assertNotIn("ITEM_Bogus", found)

    def test_a_range_loses_its_frame_suffix_and_a_single_value_keeps_its_name(self):
        # HotSpot spells a range `CHOP_FRAME_START` where the specification says `CHOP`,
        # and spells the last row `FULL_FRAME` where the specification says the same. A
        # rule that took `_FRAME` off everything would rename one of them into nothing.
        ranges = gen.frame_ranges(FRAME_HPP)
        self.assertEqual(ranges["CHOP"], (248, 250))
        self.assertEqual(ranges["SAME_LOCALS_1_STACK_ITEM"], (64, 127))
        self.assertEqual(ranges["FULL_FRAME"], (255, 255))
        self.assertEqual(ranges["SAME_FRAME_EXTENDED"], (251, 251))

    def test_the_range_the_specification_leaves_nameless_has_a_name_here(self):
        self.assertEqual(gen.frame_ranges(FRAME_HPP)["RESERVED"], (128, 246))

    def test_a_range_that_lost_its_end_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.frame_ranges(FRAME_HPP.replace("CHOP_FRAME_END", "CHOP_FRAME_LAST"))
        self.assertIn("CHOP_FRAME_END", str(stop.exception))


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
            "one": {"types": {"TOP": {"tag": 0}},
                    "frame_types": {"0": 10, "255": 2},
                    "items_where": {"locals": {"7": 3}}},
            "two": {"types": {"TOP": {"tag": 0}},
                    "frame_types": {"0": 5},
                    "items_where": {"locals": {"7": 1}, "stack": {"8": 4}}},
        }

    def test_what_the_platform_reports_has_to_be_the_same_everywhere(self):
        self.assertEqual(gen.one_answer(self.data(), "types"), {"TOP": {"tag": 0}})

    def test_two_jdks_under_one_pin_stop_the_run(self):
        data = self.data()
        data["two"]["types"]["TOP"]["tag"] = 1
        with self.assertRaises(SystemExit) as stop:
            gen.one_answer(data, "types")
        self.assertIn("Two JDKs, one pin", str(stop.exception))

    def test_counts_are_summed_and_not_averaged(self):
        # Each environment walked its own copy of java.base and the copies differ, so a
        # sum is a count of things seen and a mean is a count of nothing.
        self.assertEqual(gen.summed(self.data(), "frame_types"), {"0": 15, "255": 2})

    def test_counts_two_levels_deep_are_summed_per_side(self):
        self.assertEqual(gen.summed(self.data(), "items_where"),
                         {"locals": {"7": 4}, "stack": {"8": 4}})

    def test_a_type_that_never_occurred_gets_no_share_at_all(self):
        # "less than a hundredth of a per cent" and "not once" are different answers and
        # the second one is the one worth reading.
        self.assertEqual(gen.share(0, 1000), "")
        self.assertEqual(gen.share(1, 1_000_000), "<0.01%")
        self.assertEqual(gen.share(50, 200), "25.00%")

    def test_small_counts_are_words_and_large_ones_are_numerals(self):
        self.assertEqual(gen.word(7), "seven")
        self.assertEqual(gen.word(9), "nine")
        self.assertEqual(gen.word(119), "119")


def world() -> dict[str, object]:
    """A small consistent set of four sources, for the tests that break one at a time.

    Four verification types rather than nine, because every check in `verify` is about a
    relationship between the sources and none of them is about how many rows there are.
    All seven frame kinds are here, because two of those checks are about the ranges
    covering every value a first byte can hold.
    """
    types = {
        0: {"structure": "Top_variable_info", "item": "ITEM_Top", "after": []},
        1: {"structure": "Integer_variable_info", "item": "ITEM_Integer", "after": []},
        7: {"structure": "Object_variable_info", "item": "ITEM_Object",
            "after": ["u2 cpool_index"]},
        8: {"structure": "Uninitialized_variable_info", "item": "ITEM_Uninitialized",
            "after": ["u2 offset"]},
    }
    frames = {
        "SAME": {"structure": "same_frame", "low": 0, "high": 63, "after": []},
        "SAME_LOCALS_1_STACK_ITEM": {
            "structure": "same_locals_1_stack_item_frame", "low": 64, "high": 127,
            "after": ["verification_type_info stack[1]"]},
        "SAME_LOCALS_1_STACK_ITEM_EXTENDED": {
            "structure": "same_locals_1_stack_item_frame_extended", "low": 247,
            "high": 247, "after": ["u2 offset_delta"]},
        "CHOP": {"structure": "chop_frame", "low": 248, "high": 250,
                 "after": ["u2 offset_delta"]},
        "SAME_FRAME_EXTENDED": {"structure": "same_frame_extended", "low": 251,
                                "high": 251, "after": ["u2 offset_delta"]},
        "APPEND": {"structure": "append_frame", "low": 252, "high": 254,
                   "after": ["u2 offset_delta",
                             "verification_type_info locals[frame_type - 251]"]},
        "FULL_FRAME": {"structure": "full_frame", "low": 255, "high": 255,
                       "after": ["u2 offset_delta"]},
    }
    header = {"JVM_ITEM_Top": 0, "JVM_ITEM_Integer": 1, "JVM_ITEM_Object": 7,
              "JVM_ITEM_Uninitialized": 8}
    verifier = {"ITEM_Top": 0, "ITEM_Integer": 1, "ITEM_Object": 7,
                "ITEM_Uninitialized": 8, "ITEM_Boolean": 9, "ITEM_Long_2nd": 13}
    ranges = {"SAME": (0, 63), "SAME_LOCALS_1_STACK_ITEM": (64, 127),
              "RESERVED": (128, 246), "SAME_LOCALS_1_STACK_ITEM_EXTENDED": (247, 247),
              "CHOP": (248, 250), "SAME_FRAME_EXTENDED": (251, 251),
              "APPEND": (252, 254), "FULL_FRAME": (255, 255)}
    prose = {"reserved": (128, 246), "chop_base": 251, "delta_one": 1}
    api = {"TOP": 0, "INTEGER": 1, "OBJECT": 7, "UNINITIALIZED": 8}
    machine = {
        "scan": {"module": "java.base", "frames": 40, "attribute_bytes": 120,
                 "raw_attribute_bytes": 120, "arithmetic_checked": 30,
                 "arithmetic_wrong": 0},
        "built": {"verifies": 1, "bytes": 316},
    }
    data = {"osx-arm64": machine,
            "linux-aarch64": json.loads(json.dumps(machine))}
    items = {"0": 4, "1": 20, "7": 60, "8": 2}
    seen = {"0": 20, "64": 8, "247": 1, "248": 3, "251": 2, "252": 4, "255": 2}
    return {"types": types, "frames": frames, "header": header, "verifier": verifier,
            "ranges": ranges, "prose": prose, "api": api, "data": data, "items": items,
            "seen": seen}


class TestWhatStopsTheGenerator(unittest.TestCase):
    """One test per source having moved. Each is a real way these four can drift."""

    def setUp(self):
        self.world = world()

    def check(self) -> list[str]:
        return gen.verify(**self.world)

    def test_the_fixture_agrees_with_itself(self):
        self.assertEqual(self.check(), [])

    def test_the_specification_and_the_header_disagreeing_about_an_item_number(self):
        self.world["header"]["JVM_ITEM_Object"] = 6
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("ITEM_Object", str(stop.exception))

    def test_the_specification_and_the_verifier_disagreeing_about_an_item_number(self):
        self.world["verifier"]["ITEM_Object"] = 6
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("verificationType.hpp", str(stop.exception))

    def test_a_verification_type_nobody_wrote_a_description_for(self):
        self.world["types"][5] = {"structure": "Null_variable_info", "item": "ITEM_Null",
                                  "after": []}
        self.world["header"]["JVM_ITEM_Null"] = 5
        self.world["verifier"]["ITEM_Null"] = 5
        self.world["api"]["NULL"] = 5
        self.check()
        del gen.MEANS[5]
        try:
            with self.assertRaises(SystemExit) as stop:
                self.check()
            self.assertIn("no description", str(stop.exception))
        finally:
            gen.MEANS[5] = "the `null` reference"

    def test_the_platform_knowing_a_type_the_specification_does_not_print(self):
        self.world["api"]["MYSTERY"] = 9
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("pinned JDK", str(stop.exception))

    def test_the_specification_and_hotspot_disagreeing_about_a_frame_range(self):
        self.world["ranges"]["CHOP"] = (248, 251)
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("stackMapTable.hpp", str(stop.exception))

    def test_the_specification_and_hotspot_disagreeing_about_the_reserved_range(self):
        # The one range the specification states only in prose, which is exactly the kind
        # of thing that drifts without anybody noticing.
        self.world["ranges"]["RESERVED"] = (128, 245)
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("reserves", str(stop.exception))

    def test_two_frame_kinds_claiming_the_same_first_byte(self):
        self.world["frames"]["CHOP"]["high"] = 251
        self.world["ranges"]["CHOP"] = (248, 251)
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("251", str(stop.exception))

    def test_a_first_byte_no_frame_kind_and_no_reserved_range_accounts_for(self):
        # A hole here is a byte a parser would meet in a file and find nothing on the page
        # about, which is the one thing a table of frame kinds exists to prevent.
        self.world["frames"]["SAME"]["high"] = 62
        self.world["ranges"]["SAME"] = (0, 62)
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("[63]", str(stop.exception))

    def test_the_chop_formula_drifting_from_the_constant_it_counts_against(self):
        self.world["prose"]["chop_base"] = 252
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("SAME_FRAME_EXTENDED", str(stop.exception))

    def test_the_append_formula_drifting_from_the_same_constant(self):
        self.world["frames"]["APPEND"]["after"][1] = (
            "verification_type_info locals[frame_type - 250]")
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("frame_type - 250", str(stop.exception))

    def test_a_type_that_occurred_and_the_specification_does_not_print(self):
        self.world["items"]["9"] = 3
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("tag 9", str(stop.exception))

    def test_a_frame_in_the_reserved_range_having_been_counted(self):
        # If this fires, either a class file in the runtime image is illegal or the probe
        # is reading the wrong byte, and both are worth stopping for.
        self.world["seen"]["200"] = 1
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("200", str(stop.exception))

    def test_the_two_readers_disagreeing_about_how_many_bytes_there_are(self):
        # Both byte counts on the page come from one width model, so a wrong rule cancels
        # out of the comparison. The second reader takes `attribute_length` out of the
        # file, which is a number nothing else computed.
        self.world["data"]["osx-arm64"]["scan"]["raw_attribute_bytes"] = 121
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("attribute_length", str(stop.exception))

    def test_the_offset_arithmetic_having_failed_anywhere(self):
        self.world["data"]["osx-arm64"]["scan"]["arithmetic_wrong"] = 1
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("4.7.4", str(stop.exception))

    def test_a_built_class_the_jvm_would_not_verify(self):
        # The two rare types are demonstrated rather than reasoned about, so a run where
        # the demonstration failed writes nothing.
        self.world["data"]["osx-arm64"]["built"]["verifies"] = 0
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("demonstrated", str(stop.exception))

    def test_one_environment_is_a_note_rather_than_a_stop(self):
        # A page built from a single machine is still worth having, and a reader has to
        # be told that the counts on it are one machine's answer.
        del self.world["data"]["linux-aarch64"]
        notes = self.check()
        self.assertTrue(any("one machine" in note for note in notes))


def results(where: pathlib.Path) -> dict[str, dict]:
    files = sorted(where.glob("*.json"))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


class TestTheMeasurements(unittest.TestCase):
    def setUp(self):
        self.results = results(ROOT / gen.RESULTS)

    def test_there_is_more_than_one_environment(self):
        self.assertGreaterEqual(len(self.results), 2)

    def test_every_result_says_which_probe_and_issue_it_came_from(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["probe"], "stackmap")
                self.assertEqual(data["issue"], 15)
                self.assertTrue(data["java_build"])
                self.assertTrue(data["measured"])

    def test_the_module_scanned_is_the_one_every_image_has(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["module"], "java.base")

    def test_the_platform_describes_all_nine_verification_types_the_same_way(self):
        answers = {name: data["types"] for name, data in self.results.items()}
        for name, types in answers.items():
            with self.subTest(environment=name):
                self.assertEqual(len(types), 9)
                self.assertEqual(len({entry["tag"] for entry in types.values()}), 9)
        self.assertEqual(len({json.dumps(t, sort_keys=True)
                              for t in answers.values()}), 1)

    def test_only_the_two_types_that_carry_an_operand_say_they_carry_one(self):
        for name, data in self.results.items():
            carrying = sorted(entry["tag"] for entry in data["types"].values()
                              if entry["carries"])
            with self.subTest(environment=name):
                self.assertEqual(carrying, [7, 8])

    def test_the_item_counts_split_across_the_two_sides_without_loss(self):
        # Every verification type is either in a locals array or on an operand stack, so
        # the by tag total and the by side total are the same number counted two ways.
        for name, data in self.results.items():
            sides: dict[str, int] = {}
            for side in data["items_where"].values():
                for tag, count in side.items():
                    sides[tag] = sides.get(tag, 0) + count
            with self.subTest(environment=name):
                self.assertEqual(sides, data["items"])

    def test_the_frame_kinds_add_up_to_the_frames_counted(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sum(data["kinds"].values()), data["scan"]["frames"])
                self.assertEqual(sum(data["frame_types"].values()),
                                 data["scan"]["frames"])

    def test_the_two_readers_agree_about_the_bytes_and_about_the_frame_types(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["attribute_bytes"],
                                 data["scan"]["raw_attribute_bytes"])

    def test_every_first_byte_that_occurred_is_legal(self):
        for name, data in self.results.items():
            for value in data["frame_types"]:
                with self.subTest(environment=name, first_byte=value):
                    self.assertFalse(128 <= int(value) <= 246)

    def test_every_legal_first_byte_occurred(self):
        # The finding in 2.3 is that there is no legal first byte a parser can avoid by
        # only ever being tested against real class files.
        for name, data in self.results.items():
            seen = {int(value) for value in data["frame_types"]}
            with self.subTest(environment=name):
                self.assertEqual(seen, set(range(256)) - set(range(128, 247)))
                self.assertEqual(data["scan"]["frame_type_values_seen"], len(seen))

    def test_the_two_ranges_of_first_byte_account_for_every_value(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["frame_type_values_seen"]
                                 + data["scan"]["frame_type_values_reserved"], 256)

    def test_the_encoder_that_produced_the_headline_zero_was_checked_first(self):
        # The headline is a zero, and a zero out of an unwatched function is not a
        # measurement. These six frames have an answer that can be worked out on paper.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["encoder_self_check"],
                                 {"same": 5, "same_extended": 251, "same_locals_1": 69,
                                  "chop": 250, "append": 252, "full": 255})

    def test_no_frame_is_written_larger_than_the_format_required(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["frames_larger_than_needed"], 0)
                self.assertEqual(data["larger_than_needed"], {})
                self.assertEqual(data["scan"]["minimal_bytes"],
                                 data["scan"]["attribute_bytes"])

    def test_the_offset_arithmetic_was_checked_and_held(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertGreater(data["scan"]["arithmetic_checked"], 0)
                self.assertEqual(data["scan"]["arithmetic_wrong"], 0)

    def test_the_built_class_forces_all_four_of_the_rare_cases_and_verifies(self):
        for name, data in self.results.items():
            built = data["built"]
            with self.subTest(environment=name):
                self.assertEqual(built["verifies"], 1)
                for case in ("uninitialized_in_locals", "uninitialized_in_stack",
                             "uninitialized_this_in_locals",
                             "uninitialized_this_in_stack"):
                    self.assertGreater(built[case], 0, case)

    def test_the_scanned_module_carries_more_bytecode_than_stack_map(self):
        # A sanity bound on the two numbers section 2.6 divides into each other, so a
        # probe that started counting one of them twice would be caught here.
        for name, data in self.results.items():
            scan = data["scan"]
            with self.subTest(environment=name):
                self.assertLess(scan["attribute_bytes"], scan["code_bytes"])
                self.assertLess(scan["attribute_bytes"], scan["all_full_bytes"])
                self.assertLessEqual(scan["methods_with_frames"],
                                     scan["methods_with_code"])


class TestTheCommittedPage(unittest.TestCase):
    """Whether the page on disk still says what the results files say.

    The generator's own `--check` compares against all four sources and needs the network
    for three of them. This is the half that can be checked offline, and it is the half
    that would go stale if somebody reran the probe and forgot the generator.
    """

    def setUp(self):
        self.results = results(ROOT / gen.RESULTS)
        self.text = (ROOT / gen.OUTPUT).read_text(encoding="utf-8")

    def total(self, field: str) -> int:
        return sum(data["scan"][field] for data in self.results.values())

    def test_the_page_is_there_and_says_it_is_generated(self):
        self.assertIn("tools/gen_stackmap.py", self.text)
        self.assertIn("Do not edit it, edit a source", self.text)

    def test_every_environment_measured_is_named_on_the_page(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_the_totals_on_the_page_are_the_totals_in_the_results(self):
        self.assertIn(f"{self.total('classes'):,} class files", self.text)
        self.assertIn(f"{self.total('frames'):,} frames in "
                      f"{self.total('attribute_bytes'):,} bytes", self.text)

    def test_the_two_readers_are_reported_as_agreeing_on_the_number_they_agree_on(self):
        self.assertEqual(self.total("attribute_bytes"), self.total("raw_attribute_bytes"))
        self.assertIn(f"they agree at {self.total('attribute_bytes'):,} bytes", self.text)

    def test_every_frame_kind_is_a_row_with_the_count_that_was_measured(self):
        seen: dict[int, int] = {}
        for data in self.results.values():
            for value, count in data["frame_types"].items():
                seen[int(value)] = seen.get(int(value), 0) + count
        for span, low, high in (("0-63", 0, 63), ("64-127", 64, 127), ("247", 247, 247),
                                ("248-250", 248, 250), ("251", 251, 251),
                                ("252-254", 252, 254), ("255", 255, 255)):
            count = sum(seen.get(value, 0) for value in range(low, high + 1))
            row = re.search(rf"^\| {re.escape(span)} \| `(?P<name>\w+)` \|.*\| {count:,} \|",
                            self.text, re.M)
            self.assertIsNotNone(row, span)

    def test_every_verification_type_is_a_row_split_by_the_side_it_was_found_on(self):
        for tag in sorted(int(t) for t in self.results and
                          list(self.results.values())[0]["items"]):
            in_locals = sum(data["items_where"].get("locals", {}).get(str(tag), 0)
                            for data in self.results.values())
            on_stack = sum(data["items_where"].get("stack", {}).get(str(tag), 0)
                           for data in self.results.values())
            self.assertRegex(
                self.text,
                rf"(?m)^\| {tag} \| `\w+_variable_info` \|.*\| {in_locals:,} \| "
                rf"{on_stack:,} \|")

    def test_the_page_says_the_encoding_is_smallest_because_it_measured_that(self):
        self.assertEqual(self.total("frames_larger_than_needed"), 0)
        self.assertIn("written in the smallest form that could express it", self.text)

    def test_the_reserved_range_is_counted_rather_than_asserted(self):
        reserved = {data["scan"]["frame_type_values_reserved"]
                    for data in self.results.values()}
        self.assertEqual(len(reserved), 1)
        self.assertIn(f"## 2.3 The {reserved.pop()} first bytes that mean nothing",
                      self.text)

    def test_the_page_carries_a_hash_of_all_four_sources(self):
        self.assertIn("## 2.7 Provenance", self.text)
        for source in (gen.HEADER_PATH, gen.TABLE_HPP, gen.TYPE_HPP):
            self.assertIn(f"`{source}`", self.text)
        digests = re.findall(r"^\| .+ \| `([0-9a-f]{64})` \|$", self.text, re.M)
        self.assertEqual(len(digests), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
