#!/usr/bin/env python3
"""Tests for the constant pool section.

The generator joins four sources and its whole value is that it stops when they stop
agreeing, so the tests are in three parts: whether each source is read correctly, whether
one source moving stops the run, and whether the committed page still says what the
committed results say.

Everything here is offline. The parsers run against fixtures copied out of the real
specification page, the real header, `constantTag.hpp` and `constantTag.cpp`, because a
test that needs docs.oracle.com and raw.githubusercontent.com to be up is a test that
reports somebody else's weather.

  python tools/test_gen_constpool.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_constpool as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Three tables in the shape Oracle prints them, including the continuation the class file
# tests already found the hard way: a `(cont.)` part carries the number of the part above
# it and has to extend that table rather than replace it.
PAGE = """
<b>Table&nbsp;4.4-A.&nbsp;Constant pool tags</b>
<tbody>
<tr><td>CONSTANT_Utf8</td><td>1</td><td>&#167;4.4.7</td></tr>
<tr><td>CONSTANT_Integer</td><td>3</td><td>&#167;4.4.4</td></tr>
</tbody>
<b>Table&nbsp;4.4-A (cont.).&nbsp;Constant pool tags</b>
<tbody>
<tr><td>CONSTANT_Class</td><td>7</td><td>&#167;4.4.1</td></tr>
</tbody>
<b>Table&nbsp;4.4-B.&nbsp;Constant pool tags by class file version</b>
<tbody>
<tr><td>CONSTANT_Utf8</td><td>1</td><td>45.3</td><td>1.0.2</td></tr>
<tr><td>CONSTANT_Class</td><td>7</td><td>45.3</td><td>1.0.2</td></tr>
</tbody>
<b>Table&nbsp;4.4-C.&nbsp;Loadable constant pool tags</b>
<tbody>
<tr><td>CONSTANT_Integer</td><td>3</td><td>45.3</td><td>1.0.2</td></tr>
<tr><td>CONSTANT_Class</td><td>7</td><td>49.0</td><td>5.0</td></tr>
</tbody>
"""

# The specification prints two structures in one box, which is the case that makes the
# structure parser more than a regex.
STRUCT = """
<pre class="screen">
CONSTANT_Long_info {
    u1 tag;
    u4 high_bytes;
    u4 low_bytes;
}
CONSTANT_Double_info {
    u1 tag;
    u4 high_bytes;
    u4 low_bytes;
}
</pre>
<pre class="screen">
CONSTANT_Utf8_info {
    u1 tag;
    u2 length;
    u1 bytes[length];
}
</pre>
"""

# Six lines out of classfile_constants.h.template, in the shapes it uses.
HEADER = """
enum {
    JVM_CONSTANT_Utf8 = 1,
    JVM_CONSTANT_Unicode = 2, /* unused */
    JVM_CONSTANT_Integer = 3,
    JVM_CONSTANT_Class = 7,
    JVM_CONSTANT_ExternalMax = 20
};

enum {
    JVM_REF_getField = 1,
    JVM_REF_invokeInterface = 9
};
"""

# The parts of constantTag.hpp this reads, copied rather than paraphrased: the enum of
# implementation tags and the predicates that are asked what they accept.
HPP = """
enum {
  JVM_CONSTANT_Invalid                  = 0,    // For bad value initialization
  JVM_CONSTANT_InternalMin              = 100,  // First implementation tag
  JVM_CONSTANT_UnresolvedClass          = 100,  // Temporary tag until actual use
  JVM_CONSTANT_ClassIndex               = 101,  // Temporary tag while constructing pool
  JVM_CONSTANT_StringIndex              = 102,  // Temporary tag while constructing pool
  JVM_CONSTANT_UnresolvedClassInError   = 103,  // Error tag due to resolution error
  JVM_CONSTANT_MethodHandleInError      = 104,  // Error tag due to resolution error
  JVM_CONSTANT_MethodTypeInError        = 105,  // Error tag due to resolution error
  JVM_CONSTANT_DynamicInError           = 106,  // Error tag due to resolution error
  JVM_CONSTANT_InternalMax              = 106   // Last implementation tag
};

class constantTag {
 private:
  jbyte _tag;
 public:
  bool is_field () const            { return _tag == JVM_CONSTANT_Fieldref; }
  bool is_method() const            { return _tag == JVM_CONSTANT_Methodref; }
  bool is_interface_method() const  { return _tag == JVM_CONSTANT_InterfaceMethodref; }
  bool is_utf8() const              { return _tag == JVM_CONSTANT_Utf8; }

  bool is_unresolved_klass() const {
    return _tag == JVM_CONSTANT_UnresolvedClass || _tag == JVM_CONSTANT_UnresolvedClassInError;
  }

  bool is_unresolved_klass_in_error() const {
    return _tag == JVM_CONSTANT_UnresolvedClassInError;
  }

  bool is_method_handle_in_error() const {
    return _tag == JVM_CONSTANT_MethodHandleInError;
  }
  bool is_method_type_in_error() const {
    return _tag == JVM_CONSTANT_MethodTypeInError;
  }

  bool is_dynamic_constant_in_error() const {
    return _tag == JVM_CONSTANT_DynamicInError;
  }

  bool is_in_error() const {
    return is_unresolved_klass_in_error() ||
           is_method_handle_in_error()    ||
           is_method_type_in_error()      ||
           is_dynamic_constant_in_error();
  }

  bool is_field_or_method() const   { return is_field() || is_method() || is_interface_method(); }
  bool is_symbol() const            { return is_utf8(); }

  bool is_method_type() const       { return _tag == JVM_CONSTANT_MethodType; }
  bool is_method_handle() const     { return _tag == JVM_CONSTANT_MethodHandle; }
  bool is_dynamic_constant() const  { return _tag == JVM_CONSTANT_Dynamic; }

  bool has_bootstrap() const {
    return (_tag == JVM_CONSTANT_Dynamic ||
            _tag == JVM_CONSTANT_DynamicInError ||
            _tag == JVM_CONSTANT_InvokeDynamic);
  }

  bool is_loadable_constant() const {
    return ((_tag >= JVM_CONSTANT_Integer && _tag <= JVM_CONSTANT_String) ||
            is_method_type() || is_method_handle() || is_dynamic_constant() ||
            is_unresolved_klass());
  }

  static jbyte type2tag(BasicType bt) {
    switch (bt) {
      case T_INT:    return JVM_CONSTANT_Integer;
      default:       return JVM_CONSTANT_Invalid;
    }
  }
};
"""

# The tag numbers the header would have supplied, so the predicates above have something
# to resolve against. The range in `is_loadable_constant` is only as wide as this map.
VALUES = {
    "JVM_CONSTANT_Utf8": 1, "JVM_CONSTANT_Integer": 3, "JVM_CONSTANT_Float": 4,
    "JVM_CONSTANT_Long": 5, "JVM_CONSTANT_Double": 6, "JVM_CONSTANT_Class": 7,
    "JVM_CONSTANT_String": 8, "JVM_CONSTANT_Fieldref": 9, "JVM_CONSTANT_Methodref": 10,
    "JVM_CONSTANT_InterfaceMethodref": 11, "JVM_CONSTANT_MethodHandle": 15,
    "JVM_CONSTANT_MethodType": 16, "JVM_CONSTANT_Dynamic": 17,
    "JVM_CONSTANT_InvokeDynamic": 18, "JVM_CONSTANT_UnresolvedClass": 100,
    "JVM_CONSTANT_UnresolvedClassInError": 103,
    "JVM_CONSTANT_MethodHandleInError": 104, "JVM_CONSTANT_MethodTypeInError": 105,
    "JVM_CONSTANT_DynamicInError": 106,
}

# Three switches out of constantTag.cpp, again copied. `basic_type` is the one with a
# case that falls into an assertion rather than a return.
CPP = """
BasicType constantTag::basic_type() const {
  switch (_tag) {
    case JVM_CONSTANT_Integer :
      return T_INT;
    case JVM_CONSTANT_Long :
      return T_LONG;

    case JVM_CONSTANT_Class :
    case JVM_CONSTANT_String :
      return T_OBJECT;

    case JVM_CONSTANT_Dynamic :
    case JVM_CONSTANT_DynamicInError :
      assert(false, "Dynamic constant has no fixed basic type");

    default:
      ShouldNotReachHere();
      return T_ILLEGAL;
  }
}

jbyte constantTag::error_value() const {
  switch (_tag) {
  case JVM_CONSTANT_UnresolvedClass:
    return JVM_CONSTANT_UnresolvedClassInError;
  case JVM_CONSTANT_MethodHandle:
    return JVM_CONSTANT_MethodHandleInError;
  default:
    return _tag;
  }
}

const char* constantTag::internal_name() const {
  switch (_tag) {
    case JVM_CONSTANT_Invalid :
      return "Invalid index";
    case JVM_CONSTANT_UnresolvedClass :
      return "Unresolved Class";
    default:
      ShouldNotReachHere();
      return "Illegal";
  }
}
"""


class TestTheSpecificationPage(unittest.TestCase):
    def test_markup_inside_a_sentence_does_not_leave_a_space_before_the_semicolon(self):
        self.assertEqual(
            gen.plain("Declared <code>public</code> ; may be accessed ."),
            "Declared public; may be accessed.")

    def test_a_continuation_table_extends_the_table_above_it(self):
        found = gen.tables(PAGE)
        self.assertEqual([row[0] for row in found["4.4-A"]],
                         ["CONSTANT_Utf8", "CONSTANT_Integer", "CONSTANT_Class"])

    def test_a_page_without_the_three_tags_tables_stops_the_run(self):
        # The guard that catches Oracle republishing chapter 4 in a new shape, rather than
        # this writing a page with two rows on it.
        with self.assertRaises(SystemExit) as stop:
            gen.tables(PAGE.replace("4.4-C", "4.4-Z"))
        self.assertIn("4.4-C", str(stop.exception))

    def test_a_table_with_no_body_after_it_is_skipped_rather_than_guessed_at(self):
        found = gen.tables(PAGE + "<b>Table&nbsp;4.9-Z.&nbsp;orphan</b>")
        self.assertNotIn("4.9-Z", found)

    def test_one_tag_table_becomes_tag_to_row(self):
        rows = gen.spec_tags(gen.tables(PAGE), "4.4-C")
        self.assertEqual(sorted(rows), [3, 7])
        self.assertEqual(rows[7][2], "49.0")

    def test_a_tag_table_that_parsed_to_nothing_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.spec_tags({"4.4-C": [["something else", "yes"]]}, "4.4-C")

    def test_two_structures_in_one_box_are_cut_at_the_second_tag(self):
        # `CONSTANT_Long_info` and `CONSTANT_Double_info` share a box, and a parser that
        # read to the closing brace would give Long six items and Double none.
        found = gen.structures(STRUCT)
        self.assertEqual([item["name"] for item in found["CONSTANT_Long_info"]],
                         ["tag", "high_bytes", "low_bytes"])

    def test_an_array_item_carries_the_expression_that_sizes_it(self):
        items = gen.structures(STRUCT)["CONSTANT_Utf8_info"]
        self.assertEqual(items[2], {"type": "u1", "name": "bytes", "count": "length"})
        self.assertEqual(items[1]["count"], "")

    def test_a_kind_with_no_structure_of_its_own_borrows_the_one_it_shares(self):
        shapes = {"CONSTANT_Fieldref_info": [{"type": "u1", "name": "tag", "count": ""},
                                             {"type": "u2", "name": "class_index",
                                              "count": ""}]}
        self.assertEqual(gen.entry_shape(shapes, "CONSTANT_Methodref"),
                         [{"type": "u2", "name": "class_index", "count": ""}])

    def test_a_kind_with_no_structure_anywhere_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.entry_shape({}, "CONSTANT_Methodref")


class TestTheHeader(unittest.TestCase):
    def setUp(self):
        self.header = gen.constants(HEADER, gen.HEADER_PATH, "JVM_CONSTANT_Utf8")

    def test_the_tag_numbers_and_the_reference_kinds_both_parse(self):
        self.assertEqual(self.header["JVM_CONSTANT_Utf8"][0], 1)
        self.assertEqual(self.header["JVM_REF_invokeInterface"][0], 9)

    def test_a_constant_keeps_whatever_the_line_said_about_it(self):
        # `unused` is the whole reason tag 2 is a hole, so the comment is data.
        self.assertEqual(self.header["JVM_CONSTANT_Unicode"], (2, "unused"))
        self.assertEqual(self.header["JVM_CONSTANT_Utf8"][1], "")

    def test_a_file_that_lost_its_shape_stops_the_run(self):
        # A regex that silently matches nothing is the failure this generator is least
        # likely to notice, so every file it parses names something it must have found.
        with self.assertRaises(SystemExit) as stop:
            gen.constants("enum { SOMETHING_ELSE = 1 };\n", "somewhere",
                          "JVM_CONSTANT_Utf8")
        self.assertIn("JVM_CONSTANT_Utf8", str(stop.exception))

    def test_the_hotspot_enum_parses_with_its_trailing_comments(self):
        found = gen.constants(HPP, gen.TAG_HPP, "JVM_CONSTANT_UnresolvedClass")
        self.assertEqual(found["JVM_CONSTANT_Invalid"],
                         (0, "For bad value initialization"))
        self.assertEqual(found["JVM_CONSTANT_DynamicInError"][0], 106)
        self.assertEqual(found["JVM_CONSTANT_InternalMax"][0], 106)


class TestReadingABooleanExpression(unittest.TestCase):
    """The four helpers under `is_loadable_constant`, which is the hardest line here."""

    def test_a_brace_matcher_gets_the_whole_body_and_not_the_first_close(self):
        self.assertEqual(gen.block("f() { a { b } c }", 0), " a { b } c ")

    def test_a_brace_that_never_closes_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.block("f() { a", 0)

    def test_top_level_or_is_split_and_nested_or_is_not(self):
        self.assertEqual(gen.split_or("a || (b || c) || d"), ["a", "(b || c)", "d"])

    def test_one_pair_of_brackets_around_everything_is_told_from_two_pairs(self):
        self.assertTrue(gen.bracketed("(a || b)"))
        self.assertFalse(gen.bracketed("(a) || (b)"))
        self.assertFalse(gen.bracketed("a || b"))

    def test_a_range_covers_every_tag_between_its_ends(self):
        self.assertEqual(
            gen.evaluate("_tag >= JVM_CONSTANT_Integer && _tag <= JVM_CONSTANT_String",
                         {}, VALUES, ()),
            {3, 4, 5, 6, 7, 8})

    def test_an_expression_this_parser_does_not_know_answers_nothing(self):
        # Nothing at all rather than a wrong answer, and the caller decides whether the
        # predicate it wanted was one of the ones that came back.
        self.assertIsNone(gen.evaluate("_tag != JVM_CONSTANT_Utf8", {}, VALUES, ()))


class TestTheTagPredicates(unittest.TestCase):
    def setUp(self):
        self.checks = gen.predicates(HPP, VALUES)

    def test_a_predicate_that_calls_four_others_is_followed_through_all_of_them(self):
        self.assertEqual(self.checks["is_in_error"], {103, 104, 105, 106})

    def test_the_loadable_predicate_mixes_a_range_and_four_calls(self):
        self.assertEqual(self.checks["is_loadable_constant"],
                         {3, 4, 5, 6, 7, 8, 15, 16, 17, 100, 103})

    def test_the_loadable_predicate_accepts_tags_no_class_file_may_contain(self):
        # The reason section 2.3 subtracts the implementation tags before comparing this
        # against table 4.4-C: an unresolved class is loadable and is not a tag.
        self.assertTrue({100, 103} <= self.checks["is_loadable_constant"])

    def test_a_predicate_written_as_three_calls_on_one_line_still_parses(self):
        self.assertEqual(self.checks["is_field_or_method"], {9, 10, 11})

    def test_the_bootstrap_predicate_carries_its_own_error_tag(self):
        self.assertEqual(self.checks["has_bootstrap"], {17, 18, 106})

    def test_a_predicate_this_parser_cannot_read_is_left_out_rather_than_guessed(self):
        self.assertNotIn("type2tag", self.checks)

    def test_a_header_that_lost_one_of_the_four_wanted_predicates_stops_the_run(self):
        with self.assertRaises(SystemExit) as stop:
            gen.predicates(HPP.replace("has_bootstrap", "had_bootstrap"), VALUES)
        self.assertIn("has_bootstrap", str(stop.exception))


class TestTheSwitches(unittest.TestCase):
    def test_cases_pile_up_until_a_return(self):
        found = gen.switch(CPP, "constantTag::basic_type", gen.RETURNS_TYPE)
        self.assertEqual(found["JVM_CONSTANT_Class"], "T_OBJECT")
        self.assertEqual(found["JVM_CONSTANT_String"], "T_OBJECT")
        self.assertEqual(found["JVM_CONSTANT_Long"], "T_LONG")

    def test_a_case_that_reaches_an_assertion_has_no_answer_at_all(self):
        # `CONSTANT_Dynamic` has whatever type its bootstrap method produced, so a basic
        # type for it would be an invention, and the page says so instead of printing one.
        found = gen.switch(CPP, "constantTag::basic_type", gen.RETURNS_TYPE)
        self.assertNotIn("JVM_CONSTANT_Dynamic", found)
        self.assertNotIn("JVM_CONSTANT_DynamicInError", found)

    def test_the_error_pairing_reads_as_the_kind_that_failed_to_its_error_tag(self):
        found = gen.switch(CPP, "constantTag::error_value", gen.RETURNS_TAG)
        self.assertEqual(found["JVM_CONSTANT_UnresolvedClass"],
                         "JVM_CONSTANT_UnresolvedClassInError")
        self.assertNotIn("default", found)

    def test_the_names_hotspot_prints_are_read_as_written(self):
        found = gen.switch(CPP, "constantTag::internal_name", gen.RETURNS_STRING)
        self.assertEqual(found["JVM_CONSTANT_UnresolvedClass"], "Unresolved Class")
        self.assertNotIn("JVM_CONSTANT_Illegal", found)

    def test_a_switch_that_moved_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.switch(CPP, "constantTag::no_such_thing", gen.RETURNS_TYPE)


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
            "one": {"kinds": {"ClassEntry": {"tag": 7}},
                    "tags": {"1": 10, "7": 2},
                    "handles": {"6": {"MethodRefEntry": 3}}},
            "two": {"kinds": {"ClassEntry": {"tag": 7}},
                    "tags": {"1": 5},
                    "handles": {"6": {"MethodRefEntry": 1,
                                      "InterfaceMethodRefEntry": 2}}},
        }

    def test_what_the_platform_builds_has_to_be_the_same_everywhere(self):
        self.assertEqual(gen.one_answer(self.data(), "kinds"),
                         {"ClassEntry": {"tag": 7}})

    def test_two_jdks_under_one_pin_stop_the_run(self):
        data = self.data()
        data["two"]["kinds"]["ClassEntry"]["tag"] = 8
        with self.assertRaises(SystemExit) as stop:
            gen.one_answer(data, "kinds")
        self.assertIn("Two JDKs, one pin", str(stop.exception))

    def test_counts_are_summed_and_not_averaged(self):
        # Each environment walked its own copy of java.base and the copies differ, so a
        # sum is a count of things seen and a mean is a count of nothing.
        self.assertEqual(gen.summed(self.data(), "tags"), {"1": 15, "7": 2})

    def test_counts_two_levels_deep_are_summed_per_target(self):
        self.assertEqual(gen.summed(self.data(), "handles"),
                         {"6": {"MethodRefEntry": 4, "InterfaceMethodRefEntry": 2}})

    def test_a_field_that_is_neither_a_count_nor_a_map_is_left_alone(self):
        # `duplicates` holds `entries_seen`, which is a list of examples rather than a
        # number, and adding two lists of examples together would be a claim.
        data = {"one": {"duplicates": {"entries": 3, "entries_seen": [{"class": "a"}]}}}
        self.assertEqual(gen.summed(data, "duplicates"), {"entries": 3})


class TestHowNumbersAreWritten(unittest.TestCase):
    def test_a_tag_that_never_occurred_gets_no_share_at_all(self):
        # "less than a hundredth of a per cent" and "not once" are different answers and
        # the second one is the one worth reading.
        self.assertEqual(gen.share(0, 1000), "")
        self.assertEqual(gen.share(1, 1_000_000), "<0.01%")
        self.assertEqual(gen.share(50, 200), "25.00%")

    def test_small_counts_are_words_and_large_ones_are_numerals(self):
        self.assertEqual(gen.word(1), "one")
        self.assertEqual(gen.word(17), "seventeen")
        self.assertEqual(gen.word(18), "18")
        self.assertEqual(gen.word(2139699), "2,139,699")

    def test_a_list_reads_as_a_sentence_rather_than_a_join(self):
        self.assertEqual(gen.listed(["a"]), "`a`")
        self.assertEqual(gen.listed(["a", "b"]), "`a` and `b`")
        self.assertEqual(gen.listed(["a", "b", "c"]), "`a`, `b` and `c`")


def world() -> dict[str, object]:
    """A small consistent set of four sources, for the tests that break one at a time.

    Nine tags rather than seventeen, because every check in `verify` is about a
    relationship between the sources and none of them is about how many rows there are.
    """
    spec = {
        "4.4-A": [["CONSTANT_Utf8", "1", "§4.4.7"], ["CONSTANT_Class", "7", "§4.4.1"]],
        "4.4-B": [["CONSTANT_Utf8", "1", "45.3", "1.0.2"],
                  ["CONSTANT_Integer", "3", "45.3", "1.0.2"],
                  ["CONSTANT_Long", "5", "45.3", "1.0.2"],
                  ["CONSTANT_Class", "7", "45.3", "1.0.2"],
                  ["CONSTANT_Fieldref", "9", "45.3", "1.0.2"],
                  ["CONSTANT_Methodref", "10", "45.3", "1.0.2"],
                  ["CONSTANT_MethodHandle", "15", "51.0", "7"],
                  ["CONSTANT_Dynamic", "17", "55.0", "11"],
                  ["CONSTANT_InvokeDynamic", "18", "51.0", "7"]],
        "4.4-C": [["CONSTANT_Integer", "3", "45.3", "1.0.2"],
                  ["CONSTANT_Long", "5", "45.3", "1.0.2"],
                  ["CONSTANT_Class", "7", "49.0", "5.0"],
                  ["CONSTANT_MethodHandle", "15", "51.0", "7"],
                  ["CONSTANT_Dynamic", "17", "55.0", "11"]],
    }
    shapes = {name + "_info": [{"type": "u1", "name": "tag", "count": ""},
                               {"type": "u2", "name": "index", "count": ""}]
              for name in ("CONSTANT_Utf8", "CONSTANT_Integer", "CONSTANT_Long",
                           "CONSTANT_Class", "CONSTANT_Fieldref",
                           "CONSTANT_MethodHandle", "CONSTANT_Dynamic")}
    header = {"JVM_CONSTANT_Utf8": (1, ""), "JVM_CONSTANT_Unicode": (2, "unused"),
              "JVM_CONSTANT_Integer": (3, ""), "JVM_CONSTANT_Long": (5, ""),
              "JVM_CONSTANT_Class": (7, ""), "JVM_CONSTANT_Fieldref": (9, ""),
              "JVM_CONSTANT_Methodref": (10, ""), "JVM_CONSTANT_MethodHandle": (15, ""),
              "JVM_CONSTANT_Dynamic": (17, ""), "JVM_CONSTANT_InvokeDynamic": (18, ""),
              "JVM_CONSTANT_ExternalMax": (20, ""), "JVM_REF_getField": (1, "")}
    internal = {0: ("JVM_CONSTANT_Invalid", "For bad value initialization"),
                100: ("JVM_CONSTANT_UnresolvedClass", "Temporary tag until actual use"),
                103: ("JVM_CONSTANT_UnresolvedClassInError", "Error tag")}
    checks = {"is_loadable_constant": {3, 5, 7, 15, 17, 100, 103},
              "is_field_or_method": {9, 10},
              "has_bootstrap": {17, 18},
              "is_in_error": {103}}
    kinds = {
        "Utf8Entry": {"tag": 1, "slots": 1, "loadable": False, "member_ref": False,
                      "dynamic": False},
        "IntegerEntry": {"tag": 3, "slots": 1, "loadable": True, "member_ref": False,
                         "dynamic": False},
        "LongEntry": {"tag": 5, "slots": 2, "loadable": True, "member_ref": False,
                      "dynamic": False},
        "ClassEntry": {"tag": 7, "slots": 1, "loadable": True, "member_ref": False,
                       "dynamic": False},
        "FieldRefEntry": {"tag": 9, "slots": 1, "loadable": False, "member_ref": True,
                          "dynamic": False},
        "MethodRefEntry": {"tag": 10, "slots": 1, "loadable": False, "member_ref": True,
                           "dynamic": False},
        "MethodHandleEntry": {"tag": 15, "slots": 1, "loadable": True,
                              "member_ref": False, "dynamic": False},
        "ConstantDynamicEntry": {"tag": 17, "slots": 1, "loadable": True,
                                 "member_ref": False, "dynamic": True},
        "InvokeDynamicEntry": {"tag": 18, "slots": 1, "loadable": False,
                               "member_ref": False, "dynamic": True},
    }
    tags = {"1": 60, "3": 5, "5": 2, "7": 20, "9": 4, "10": 8, "15": 1, "18": 2}
    data = {"osx-arm64": {
        "platform": "darwin-arm64",
        "scan": {"module": "java.base", "classes": 10, "entries": 102,
                 "second_slots": 2, "pool_count_sum": 114, "largest_pool": 40,
                 "largest_pool_class": "java/lang/Object", "utf8_unaccounted": []},
        "tags": dict(tags),
        "duplicates": {"classes": 1, "entries": 2, "by_tag": {"10": 2},
                       "entries_seen": [{"class": "java/lang/Object",
                                         "entry": "10|java/util/List.toString:()V"}]},
    }}
    census = {"osx-arm64": {"tags": dict(tags)}}
    roles = {"class_name": 20, "attribute_name": 30, "method_name": 10}
    handles = {"6": {"MethodRefEntry": 3, "InterfaceMethodRefEntry": 1}}
    return {"spec": spec, "shapes": shapes, "header": header, "internal": internal,
            "checks": checks, "kinds": kinds, "data": data, "census": census,
            "tags": tags, "roles": roles, "handles": handles}


class TestWhatStopsTheGenerator(unittest.TestCase):
    """One test per source having moved. Each is a real way these four can drift."""

    def setUp(self):
        self.world = world()

    def check(self) -> list[str]:
        return gen.verify(**self.world)

    def test_the_fixture_agrees_with_itself(self):
        self.assertEqual(self.check(), [])

    def test_the_specification_defining_a_tag_the_header_lacks(self):
        self.world["spec"]["4.4-B"].append(["CONSTANT_Something", "19", "45.3", "1.0.2"])
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_disagreeing_about_what_number_a_tag_is(self):
        self.world["header"]["JVM_CONSTANT_Class"] = (8, "")
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_header_tag_the_specification_does_not_list_and_nobody_explains(self):
        # Tag 2 is allowed through because the header says `unused` next to it. A new one
        # with no comment is the JDK shipping a tag ahead of the specification, and that
        # is a thing to read about rather than a thing to print.
        self.world["header"]["JVM_CONSTANT_Mystery"] = (19, "")
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_header_tag_the_specification_does_not_list_and_the_header_does(self):
        self.world["header"]["JVM_CONSTANT_Mystery"] = (19, "unused")
        self.check()

    def test_the_platform_building_a_kind_the_specification_does_not_have(self):
        self.world["kinds"]["MysteryEntry"] = {"tag": 19, "slots": 1, "loadable": False,
                                               "member_ref": False, "dynamic": False}
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_wide_entry_that_stopped_taking_two_slots(self):
        self.world["kinds"]["LongEntry"]["slots"] = 1
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_narrow_entry_that_started_taking_two(self):
        self.world["kinds"]["ClassEntry"]["slots"] = 2
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_platform_and_the_specification_disagreeing_about_loadability(self):
        self.world["kinds"]["Utf8Entry"]["loadable"] = True
        with self.assertRaises(SystemExit):
            self.check()

    def test_hotspot_and_the_specification_disagreeing_about_loadability(self):
        self.world["checks"]["is_loadable_constant"].add(1)
        with self.assertRaises(SystemExit):
            self.check()

    def test_an_implementation_tag_in_the_loadable_predicate_is_not_a_disagreement(self):
        # `is_loadable_constant` accepts an unresolved class, which is a state rather than
        # a tag any class file may contain, so it is subtracted before the comparison.
        self.assertTrue({100, 103} <= self.world["checks"]["is_loadable_constant"])
        self.check()

    def test_the_two_disagreeing_about_which_kinds_are_member_references(self):
        self.world["checks"]["is_field_or_method"] = {9, 10, 11}
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_two_disagreeing_about_which_kinds_have_a_bootstrap_method(self):
        self.world["kinds"]["InvokeDynamicEntry"]["dynamic"] = False
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_kind_the_chapter_prints_no_structure_for(self):
        del self.world["shapes"]["CONSTANT_Fieldref_info"]
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_tag_that_occurred_and_the_specification_does_not_list(self):
        self.world["tags"]["19"] = 5
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_pool_count_arithmetic_not_adding_up(self):
        # The identity this section exists to explain, checked rather than asserted: the
        # entries, plus the second half of every wide entry, plus the missing slot zero of
        # every class, is the sum of every constant_pool_count.
        self.world["data"]["osx-arm64"]["scan"]["second_slots"] = 3
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("constant_pool_count", str(stop.exception))

    def test_two_probes_that_walked_the_same_module_and_counted_differently(self):
        self.world["census"]["osx-arm64"]["tags"]["7"] = 21
        with self.assertRaises(SystemExit) as stop:
            self.check()
        self.assertIn("classfile-census", str(stop.exception))

    def test_no_environment_ran_both_probes_is_a_note_rather_than_a_stop(self):
        self.world["census"] = {"linux-x64": {"tags": {}}}
        notes = self.check()
        self.assertTrue(any("checked against the specification" in note
                            for note in notes))

    def test_a_string_role_nobody_wrote_a_description_for(self):
        # A string nobody can name is the whole question section 2.5 exists to answer.
        self.world["roles"]["something_new"] = 4
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_method_handle_reference_kind_the_specification_does_not_define(self):
        self.world["handles"]["10"] = {"MethodRefEntry": 1}
        with self.assertRaises(SystemExit):
            self.check()

    def test_a_method_handle_pointing_at_something_the_specification_forbids(self):
        self.world["handles"]["5"] = {"InterfaceMethodRefEntry": 1}
        with self.assertRaises(SystemExit):
            self.check()

    def test_the_interface_target_of_reference_kind_six_is_allowed(self):
        # The row a reader gets wrong first: class file version 52 let a static interface
        # method be the target of a handle, and both targets are legal for kind 6.
        self.assertEqual(self.world["handles"]["6"],
                         {"MethodRefEntry": 3, "InterfaceMethodRefEntry": 1})
        self.check()


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
                self.assertEqual(data["probe"], "constpool")
                self.assertEqual(data["issue"], 15)
                self.assertTrue(data["java_build"])
                self.assertTrue(data["measured"])

    def test_the_module_scanned_is_the_one_every_image_has(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["scan"]["module"], "java.base")

    def test_every_kind_the_platform_builds_is_described_the_same_way_everywhere(self):
        # The half of the results that is a property of the JDK rather than of the image,
        # so two environments under one pin have to agree about it exactly.
        answers = {name: data["kinds"] for name, data in self.results.items()}
        self.assertEqual(len(set(json.dumps(k, sort_keys=True)
                                 for k in answers.values())), 1)

    def test_the_platform_builds_one_entry_of_every_kind(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(len(data["kinds"]), 17)
                self.assertEqual(len({entry["tag"]
                                      for entry in data["kinds"].values()}), 17)

    def test_only_the_wide_kinds_take_two_slots(self):
        for name, data in self.results.items():
            wide = sorted(entry["tag"] for entry in data["kinds"].values()
                          if entry["slots"] == 2)
            with self.subTest(environment=name):
                self.assertEqual(wide, [5, 6])

    def test_the_tag_counts_add_up_to_the_entries_counted(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(sum(data["tags"].values()), data["scan"]["entries"])

    def test_the_pool_count_identity_holds_in_every_environment(self):
        for name, data in self.results.items():
            scan = data["scan"]
            with self.subTest(environment=name):
                self.assertEqual(
                    scan["entries"] + scan["second_slots"] + scan["classes"],
                    scan["pool_count_sum"])

    def test_the_second_slots_are_the_wide_entries(self):
        # Every `CONSTANT_Long` and every `CONSTANT_Double` costs exactly one extra slot,
        # so this is the same number counted two ways.
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertEqual(data["tags"].get("5", 0) + data["tags"].get("6", 0),
                                 data["scan"]["second_slots"])

    def test_no_pool_is_larger_than_the_field_that_counts_it(self):
        for name, data in self.results.items():
            with self.subTest(environment=name):
                self.assertLessEqual(data["scan"]["largest_pool"], 65535)
                self.assertTrue(data["scan"]["largest_pool_class"])

    def test_every_string_role_reported_has_a_description_on_the_page(self):
        for name, data in self.results.items():
            for role in data["utf8_roles"]:
                with self.subTest(environment=name, role=role):
                    self.assertIn(role, gen.ROLE)

    def test_every_method_handle_points_where_the_specification_allows(self):
        for name, data in self.results.items():
            for kind, points in data["method_handles"].items():
                with self.subTest(environment=name, kind=kind):
                    self.assertIn(int(kind), gen.REFERENCE)
                    self.assertTrue(set(points) <= gen.REFERENCE[int(kind)][0])

    def test_the_duplicates_are_counted_and_shown(self):
        for name, data in self.results.items():
            found = data["duplicates"]
            with self.subTest(environment=name):
                self.assertEqual(sum(found["by_tag"].values()), found["entries"])
                self.assertEqual(len(found["entries_seen"]), found["entries"])
                for entry in found["entries_seen"]:
                    self.assertIn("|", entry["entry"])
                    self.assertTrue(entry["class"])

    def test_the_two_probes_that_count_tags_agree(self):
        census = results(ROOT / gen.CENSUS)
        shared = sorted(set(self.results) & set(census))
        self.assertTrue(shared)
        for name in shared:
            with self.subTest(environment=name):
                self.assertEqual(self.results[name]["tags"], census[name]["tags"])


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
        self.assertIn("tools/gen_constpool.py", self.text)
        self.assertIn("Do not edit it, edit a source", self.text)

    def test_every_environment_measured_is_named_on_the_page(self):
        for name in self.results:
            self.assertIn(f"`{name}`", self.text)

    def test_the_totals_on_the_page_are_the_totals_in_the_results(self):
        self.assertIn(f"{self.total('classes'):,} class files", self.text)
        self.assertIn(f"{self.total('entries'):,} constant pool entries", self.text)

    def test_the_arithmetic_the_page_shows_is_the_arithmetic_it_measured(self):
        left = self.total("entries") + self.total("second_slots") + self.total("classes")
        self.assertIn(f"is {left:,}", self.text)
        self.assertIn(f"`constant_pool_count` field is {self.total('pool_count_sum'):,}",
                      self.text)

    def test_the_largest_pool_is_reported_with_the_class_it_belongs_to(self):
        largest = max(data["scan"]["largest_pool"] for data in self.results.values())
        owner = sorted(data["scan"]["largest_pool_class"]
                       for data in self.results.values()
                       if data["scan"]["largest_pool"] == largest)[0]
        self.assertIn(f"{largest:,} slots", self.text)
        self.assertIn(f"`{owner}`", self.text)

    def test_every_string_role_measured_has_its_sentence_on_the_page(self):
        roles: dict[str, int] = {}
        for data in self.results.values():
            for role, count in data["utf8_roles"].items():
                roles[role] = roles.get(role, 0) + count
        for role, count in roles.items():
            self.assertIn(f"| {gen.ROLE[role]} | {count:,} |", self.text)

    def test_every_repeated_tag_is_a_row_with_the_number_of_repeats_measured(self):
        repeats: dict[str, int] = {}
        examples: set[str] = set()
        for data in self.results.values():
            for tag, count in data["duplicates"]["by_tag"].items():
                repeats[tag] = repeats.get(tag, 0) + count
            for entry in data["duplicates"]["entries_seen"]:
                examples.add(f"`{entry['entry'].split('|', 1)[1]}` in "
                             f"`{entry['class']}`")
        self.assertTrue(repeats)
        for tag, count in repeats.items():
            row = re.search(rf"^\| {tag} \| `\w+` \| {count:,} \| (?P<example>.+) \|$",
                            self.text, re.M)
            self.assertIsNotNone(row, tag)
            # The example the page shows has to be one of the repeats that were found,
            # rather than a name that survived a rewrite of the probe.
            self.assertIn(row.group("example"), examples)

    def test_a_kind_that_never_occurred_is_said_to_never_occur(self):
        counted: dict[str, int] = {}
        for data in self.results.values():
            for tag, count in data["tags"].items():
                counted[tag] = counted.get(tag, 0) + count
        missing = sorted(entry["tag"] for entry in
                         list(self.results.values())[0]["kinds"].values()
                         if not counted.get(str(entry["tag"]), 0))
        self.assertTrue(missing)
        sentence = re.search(r"^\w[^\n]*occur anywhere in the scanned code: [^\n]*$",
                             self.text, re.M)
        self.assertIsNotNone(sentence)
        for tag in missing:
            # The name comes off the table on the page rather than out of a map here, so
            # the two halves of the page have to be talking about the same kind.
            row = re.search(rf"^\| {tag} \| `(?P<name>CONSTANT_\w+)` \|", self.text, re.M)
            self.assertIsNotNone(row, tag)
            self.assertIn(f"`{row.group('name')}`", sentence.group(0))

    def test_the_page_carries_a_hash_of_all_four_sources(self):
        self.assertIn("## 2.8 Provenance", self.text)
        for source in (gen.HEADER_PATH, gen.TAG_HPP, gen.TAG_CPP):
            self.assertIn(f"`{source}`", self.text)
        digests = re.findall(r"^\| .+ \| `([0-9a-f]{64})` \|$", self.text, re.M)
        self.assertEqual(len(digests), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
