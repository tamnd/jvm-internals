#!/usr/bin/env python3
"""Tests for the assignability section.

The generator joins four sources and its whole value is that it stops when they stop
agreeing, so the tests are in four parts: whether each source is read correctly, whether
one source moving stops the run, whether the committed measurements hold together on
their own, and whether the committed page still says what the committed results say.

Everything here is offline. The parsers run against fixtures copied out of the real
specification section and the real HotSpot files, because a test that needs
docs.oracle.com and raw.githubusercontent.com to be up is a test that reports somebody
else's weather.

  python tools/test_gen_verify.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_verify as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 4.10.1.2 in miniature: the diagram, a fact, a rule, the four widening rules including
# the one that does not parse, and the section after it that says where to stop. A
# section before it holds an `isAssignable` clause that belongs to a different relation,
# which is what a parser that reads the whole chapter instead of one section would take.
SPEC = """
<a name="jvms-4.10.1.1"></a><h4>4.10.1.1. Accessors</h4>
<pre class="programlisting">
isAssignable(notThisOne, alsoNotThisOne).
</pre>
<a name="jvms-4.10.1.2"></a><h4>4.10.1.2. Verification Type System</h4>
<pre class="programlisting">
Verification type hierarchy:

                             top
                 ____________/\\____________
                /                          \\
            oneWord                       twoWord
           /   |   \\                     /       \\
        int  float  reference        long        double
                     /     \\
           uninitialized                    +------------------+
            /         \\                     |  reference type  |
uninitializedThis  uninitialized(Offset)    +------------------+
                                                    null
</pre>
<pre class="programlisting">
isAssignable(X, X).
</pre>
<pre class="programlisting">
isAssignable(oneWord, top).
isAssignable(twoWord, top).
</pre>
<pre class="programlisting">
isAssignable(int, X)    :- isAssignable(oneWord, X).
isAssignable(null, X) :- isAssignable(reference, X).
</pre>
<pre class="programlisting">
isAssignable(From, To) :- isWideningReference(From, To).
</pre>
<pre class="programlisting">
isWideningReference(class(_, _), class(To, L)) :-
    loadedClass(To, L, ToClass),
    classIsInterface(ToClass).
</pre>
<pre class="programlisting">
isWideningReference(arrayOf(_), class(ClassName, L)) :-
    (ClassName = 'java/lang/Object' ;
     ClassName = 'java/lang/Cloneable' ;
     ClassName = 'java/io/Serializable')
    loadedClass(ClassName, L, LoadedClass),
    classDefiningLoader(LoadedClass, BL),
    isBootstrapLoader(BL).

isWideningReference(arrayOf(X), arrayOf(Y)) :-
    isWideningReference(X, Y).
</pre>
<a name="jvms-4.10.1.3"></a><h4>4.10.1.3. Instruction Representation</h4>
<pre class="programlisting">
isAssignable(neitherIsThis, norThis).
</pre>
"""

# The two files the version constants come from, cut down to the enum and the define.
VERIFIER_HPP = """
class Verifier : AllStatic {
 public:
  enum {
    STACKMAP_ATTRIBUTE_MAJOR_VERSION    = 50,
    INVOKEDYNAMIC_MAJOR_VERSION         = 51,
    NO_RELAX_ACCESS_CTRL_CHECK_VERSION  = 52,
    DYNAMICCONSTANT_MAJOR_VERSION       = 55
  };
"""

VERIFIER_CPP = """
#include "classfile/verifier.hpp"

#define NOFAILOVER_MAJOR_VERSION                       51
#define STATIC_METHOD_IN_INTERFACE_MAJOR_VERSION       52
#define MAX_ARRAY_DIMENSIONS 255
"""

TYPE_CPP = """
bool VerificationType::resolve_and_check_assignability(InstanceKlass* current_klass,
                                                       Symbol* target_name) {
  bool is_intf = target_klass->is_interface();
  if (is_intf && (!from_field_is_protected ||
      from_name != vmSymbols::java_lang_Object())) {
    // If we are not trying to access a protected field or method in
    // java.lang.Object then, for arrays, we only allow assignability
    // to interfaces java.lang.Cloneable and java.io.Serializable.
    // Otherwise, we treat interfaces as java.lang.Object.
    return !from_is_array ||
      target_klass == vmClasses::Cloneable_klass() ||
      target_klass == vmClasses::Serializable_klass();
  } else if (from_is_object) {
    return from_klass->is_subclass_of(target_klass);
  }
  return false;
}
"""

ORDER = ["top", "int", "null", "Object", "Runnable", "Cloneable", "Serializable",
         "Object[]"]
REFERENCES = ["Object", "Runnable", "Cloneable", "Serializable", "Object[]"]
INTERFACES = ["Runnable", "Cloneable", "Serializable"]


def tiny_cells() -> dict[str, int]:
    """A grid small enough to reason about, with the same shape as the measured one.

    Everything reaches `top`, `null` reaches every reference, every reference reaches
    `Object`, `Object` and every interface reach each other, and the array reaches the
    two interfaces named in `verificationType.cpp` and no other. That is the mutually
    assignable group and the non transitive triples in miniature.
    """
    cells = {f"{a}->{b}": 0 for a in ORDER for b in ORDER}
    for one in ORDER:
        cells[f"{one}->{one}"] = 1
        cells[f"{one}->top"] = 1
    for one in REFERENCES:
        cells[f"null->{one}"] = 1
        cells[f"{one}->Object"] = 1
    for one in INTERFACES:
        cells[f"Object->{one}"] = 1
        for other in INTERFACES:
            cells[f"{one}->{other}"] = 1
    cells["Object[]->Cloneable"] = 1
    cells["Object[]->Serializable"] = 1
    return cells


def tiny_results(**changes) -> dict[str, dict]:
    """One environment's results file, in the shape `read` returns."""
    cells = tiny_cells()
    got = gen.shape(cells, ORDER)
    found = {
        "probe": "verify",
        "issue": 15,
        "order": ORDER,
        "types": {one: "reference" for one in ORDER},
        "cells": cells,
        "grid": {"types": len(ORDER), "cells": len(cells), "unmeasurable": 0,
                 "assignable": got["assignable"], "refusal_shapes": 1},
        "properties": {"reflexive": got["reflexive"],
                       "nontransitive": got["nontransitive"],
                       "mutual": got["mutual"]},
        "refusal_shapes": {"Type <type> is not assignable to <type>":
                           len(cells) - got["assignable"]},
        "worked_by_hand": {"int->top": 1, "null->Object": 1, "wrong": 0},
        "hole": {"verifies": 1, "call_verifies": 1, "control_verifies": 0,
                 "throws": "java.lang.IncompatibleClassChangeError: nope",
                 "control_rejected_by": "not assignable"},
        "failover": {"49": {"wrong_loads": 1, "right_loads": 1},
                     "50": {"wrong_loads": 1, "right_loads": 1},
                     "51": {"wrong_loads": 0, "right_loads": 1,
                            "rejected_by": "VerifyError"}},
        "census": {"module": "java.base", "classes": 10, "methods": 20,
                   "methods_with_code": 15, "instructions": 100, "code_bytes": 400},
        "ops": {"INVOKEINTERFACE": 7, "CHECKCAST": 3},
    }
    found.update(changes)
    return {"tiny": found}


CONSTANTS = {"STACKMAP_ATTRIBUTE_MAJOR_VERSION": 50, "NOFAILOVER_MAJOR_VERSION": 51}


def run_verify(data: dict[str, dict], terms=None, found=None, constants=None):
    """`verify` with everything it needs, so a test can move one thing and nothing else."""
    one = next(iter(data.values()))
    cells = one["cells"]
    order = one["order"]
    got = gen.shape(cells, order)
    return gen.verify(
        terms if terms is not None else ["top", "int", "null"],
        found if found is not None else [{"name": "isWideningReference", "head": "h",
                                          "body": "b", "text": "t"}],
        order, cells, one["properties"], got, one["hole"], one["failover"],
        constants if constants is not None else CONSTANTS, one["ops"], data)


class TestReadingTheSpecification(unittest.TestCase):
    def setUp(self):
        self.section = gen.section_of(SPEC)
        self.boxes = gen.listings(self.section)

    def test_the_section_stops_before_the_next_one(self):
        self.assertNotIn("neitherIsThis", self.section)

    def test_the_section_starts_after_the_previous_one(self):
        self.assertNotIn("notThisOne", self.section)

    def test_a_missing_anchor_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.section_of("<html>no sections here</html>")

    def test_a_section_that_no_longer_ends_where_it_did_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.section_of(SPEC[:SPEC.find('<a name="jvms-4.10.1.3"></a>')])

    def test_a_section_with_no_listings_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.listings('<a name="jvms-4.10.1.2"></a><p>prose only</p>')

    def test_the_hierarchy_diagram_gives_up_its_terms(self):
        terms = gen.hierarchy(self.boxes)
        self.assertEqual(sorted(terms), sorted(gen.WRITABLE))

    def test_a_diagram_that_lost_a_term_stops_the_run(self):
        without = [one.replace("uninitializedThis", "somethingElse")
                   for one in self.boxes]
        with self.assertRaises(SystemExit):
            gen.hierarchy(without)

    def test_a_section_that_lost_its_diagram_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.hierarchy(["isAssignable(X, X)."])

    def test_every_clause_in_the_section_is_found(self):
        found = gen.clauses(self.boxes)
        names = [one["head"].split("(")[0] for one in found]
        self.assertEqual(names.count("isWideningReference"), 3)
        self.assertGreaterEqual(names.count("isAssignable"), 6)

    def test_a_fact_is_read_as_a_clause_with_no_body(self):
        found = gen.clauses(self.boxes)
        fact = next(one for one in found if one["head"] == "isAssignable(X, X)")
        self.assertEqual(fact["body"], "")

    def test_a_rule_keeps_its_body(self):
        found = gen.clauses(self.boxes)
        rule = next(one for one in found if one["head"] == "isAssignable(int, X)")
        self.assertEqual(rule["body"], "isAssignable(oneWord, X)")

    def test_a_section_with_no_clauses_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.clauses(["nothing here resembles a clause"])

    def test_the_clause_that_does_not_parse_is_found(self):
        bad = gen.unparsable(gen.clauses(self.boxes))
        self.assertEqual(len(bad), 1)
        self.assertTrue(bad[0]["head"].startswith("isWideningReference(arrayOf(_)"))
        self.assertTrue(bad[0]["at"].startswith("loadedClass"))

    def test_a_clause_whose_goals_are_comma_separated_is_not_reported(self):
        good = [{"name": "isWideningReference",
                 "head": "isWideningReference(a, b)",
                 "body": "one(X), two(Y), three(Z)", "text": ""}]
        self.assertEqual(gen.unparsable(good), [])

    def test_a_disjunction_followed_by_a_comma_is_not_reported(self):
        good = [{"name": "isAssignable", "head": "isAssignable(a, b)",
                 "body": "(one(X) ; two(X)), three(Y)", "text": ""}]
        self.assertEqual(gen.unparsable(good), [])

    def test_a_fact_is_never_reported_as_unparsable(self):
        self.assertEqual(gen.unparsable([{"name": "isAssignable", "head": "h",
                                          "body": "", "text": ""}]), [])


class TestReadingHotSpot(unittest.TestCase):
    def test_the_enum_constant_is_read(self):
        found = gen.versions(VERIFIER_HPP, gen.ENUM_VERSION, "verifier.hpp")
        self.assertEqual(found["STACKMAP_ATTRIBUTE_MAJOR_VERSION"], 50)

    def test_the_define_is_read(self):
        found = gen.versions(VERIFIER_CPP, gen.DEFINE_VERSION, "verifier.cpp")
        self.assertEqual(found["NOFAILOVER_MAJOR_VERSION"], 51)

    def test_a_file_with_no_version_constants_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.versions("int x = 3;", gen.ENUM_VERSION, "somewhere.hpp")

    def test_the_define_pattern_does_not_read_the_enum(self):
        self.assertEqual(gen.versions(VERIFIER_CPP, gen.DEFINE_VERSION, "c").get(
            "STACKMAP_ATTRIBUTE_MAJOR_VERSION"), None)

    def test_the_quoted_block_starts_where_it_is_asked_to(self):
        number, lines = gen.quoted(TYPE_CPP, "if (is_intf &&", "verificationType.cpp", 10)
        self.assertIn("if (is_intf &&", lines[0])
        self.assertEqual(len(lines), 10)
        self.assertEqual(TYPE_CPP.splitlines()[number - 1], lines[0])

    def test_the_quoted_block_reaches_the_sentence_the_page_is_about(self):
        _, lines = gen.quoted(TYPE_CPP, "if (is_intf &&", "verificationType.cpp", 10)
        self.assertIn("we treat interfaces as java.lang.Object",
                      "\n".join(lines))

    def test_a_marker_that_is_gone_stops_the_run(self):
        with self.assertRaises(SystemExit):
            gen.quoted(TYPE_CPP, "if (nothing_like_this)", "verificationType.cpp", 3)


class TestTheArithmetic(unittest.TestCase):
    def setUp(self):
        self.cells = tiny_cells()
        self.got = gen.shape(self.cells, ORDER)

    def test_reflexivity_is_counted_rather_than_assumed(self):
        self.assertEqual(self.got["reflexive"], len(ORDER))

    def test_a_grid_that_is_not_reflexive_is_reported_as_such(self):
        cells = dict(self.cells, **{"int->int": 0})
        self.assertEqual(gen.shape(cells, ORDER)["reflexive"], len(ORDER) - 1)

    def test_the_non_transitive_triple_is_found(self):
        self.assertIn("Object[]->Object->Runnable", self.got["triples"])
        self.assertEqual(self.got["nontransitive"], len(self.got["triples"]))

    def test_a_transitive_grid_reports_no_triples(self):
        cells = dict(self.cells, **{"Object[]->Runnable": 1})
        self.assertEqual(gen.shape(cells, ORDER)["nontransitive"], 0)

    def test_the_triples_all_end_at_the_interface_the_array_cannot_reach(self):
        self.assertTrue(all(one.endswith("->Runnable") for one in self.got["triples"]))

    def test_the_mutually_assignable_pairs_are_found(self):
        self.assertIn("Object and Runnable", self.got["pairs"])
        self.assertIn("Cloneable and Serializable", self.got["pairs"])

    def test_each_pair_is_counted_once_rather_than_in_both_directions(self):
        # Four types that all reach each other is six unordered pairs, not twelve.
        self.assertEqual(self.got["mutual"], 6)

    def test_the_assignable_count_is_the_cells_that_say_so(self):
        self.assertEqual(self.got["assignable"],
                         sum(1 for one in self.cells.values() if one == 1))

    def test_pairs_close_into_groups(self):
        groups = gen.classes(["a and b", "b and c", "d and e"], ["a", "b", "c", "d", "e"])
        self.assertEqual(groups, [["a", "b", "c"], ["d", "e"]])

    def test_the_largest_group_comes_first(self):
        groups = gen.classes(["d and e", "a and b", "b and c"],
                             ["a", "b", "c", "d", "e"])
        self.assertEqual(groups[0], ["a", "b", "c"])

    def test_no_pairs_close_into_no_groups(self):
        self.assertEqual(gen.classes([], ORDER), [])

    def test_a_group_is_in_the_order_the_grid_was_built_in(self):
        groups = gen.classes(self.got["pairs"], ORDER)
        self.assertEqual(groups, [["Object", "Runnable", "Cloneable", "Serializable"]])


class TestTheFailoverPrediction(unittest.TestCase):
    def test_below_the_stack_map_version_the_attribute_is_never_read(self):
        self.assertEqual(gen.predict(49, CONSTANTS), "loads")

    def test_at_the_stack_map_version_a_refusal_is_not_final(self):
        self.assertEqual(gen.predict(50, CONSTANTS), "loads")

    def test_at_the_no_failover_version_a_refusal_is_final(self):
        self.assertEqual(gen.predict(51, CONSTANTS), "refused")

    def test_every_later_version_is_final_too(self):
        for major in (52, 61, 71):
            self.assertEqual(gen.predict(major, CONSTANTS), "refused")

    def test_the_prediction_follows_the_constants_rather_than_the_numbers(self):
        moved = {"STACKMAP_ATTRIBUTE_MAJOR_VERSION": 60, "NOFAILOVER_MAJOR_VERSION": 62}
        self.assertEqual(gen.predict(61, moved), "loads")
        self.assertEqual(gen.predict(62, moved), "refused")


class TestOneSourceMoving(unittest.TestCase):
    def test_the_sources_as_they_are_do_not_stop_the_run(self):
        self.assertTrue(run_verify(tiny_results()))

    def test_a_type_the_generator_cannot_describe_stops_the_run(self):
        data = tiny_results()
        one = next(iter(data.values()))
        one["order"] = ORDER + ["Widget"]
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_type_that_is_neither_an_atom_nor_a_class_name_stops_the_run(self):
        saved = dict(gen.MEANS)
        gen.MEANS["oddity"] = "something"
        try:
            data = tiny_results()
            next(iter(data.values()))["order"] = ORDER + ["oddity"]
            with self.assertRaises(SystemExit):
                run_verify(data)
        finally:
            gen.MEANS.clear()
            gen.MEANS.update(saved)

    def test_a_grid_claiming_to_have_measured_an_unwritable_atom_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["order"] = ORDER + ["oneWord"]
        with self.assertRaises(SystemExit):
            run_verify(data, terms=["top", "int", "null", "oneWord"])

    def test_the_two_readers_disagreeing_about_reflexivity_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["properties"]["reflexive"] += 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_the_two_readers_disagreeing_about_transitivity_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["properties"]["nontransitive"] = 0
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_the_two_readers_disagreeing_about_the_mutual_pairs_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["properties"]["mutual"] = 4
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_header_that_disagrees_with_the_cells_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["grid"]["assignable"] += 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_missing_cell_stops_the_run(self):
        data = tiny_results()
        del next(iter(data.values()))["cells"]["int->null"]
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_null_not_reaching_a_reference_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["cells"]["null->Runnable"] = 0
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_reference_not_reaching_object_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["cells"]["Object[]->Object"] = 0
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_an_array_reaching_an_interface_it_should_not_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["cells"]["Object[]->Runnable"] = 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_an_array_not_reaching_the_two_interfaces_it_should_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["cells"]["Object[]->Serializable"] = 0
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_section_with_no_widening_clauses_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_verify(tiny_results(), found=[{"name": "isAssignable", "head": "h",
                                               "body": "", "text": "t"}])

    def test_a_failover_row_the_constants_do_not_predict_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["failover"]["51"]["wrong_loads"] = 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_constant_moving_without_the_measurement_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_verify(tiny_results(),
                       constants={"STACKMAP_ATTRIBUTE_MAJOR_VERSION": 49,
                                  "NOFAILOVER_MAJOR_VERSION": 50})

    def test_a_correct_frame_that_did_not_load_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["failover"]["52"] = {"wrong_loads": 0, "right_loads": 0}
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_hole_that_no_longer_verifies_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["hole"]["verifies"] = 0
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_hole_that_no_longer_fails_at_run_time_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["hole"]["throws"] = "nothing happened"
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_a_control_that_verifies_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["hole"]["control_verifies"] = 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_an_opcode_the_generator_cannot_describe_stops_the_run(self):
        data = tiny_results()
        next(iter(data.values()))["ops"]["FROBNICATE"] = 1
        with self.assertRaises(SystemExit):
            run_verify(data)

    def test_two_environments_that_disagree_stop_the_run(self):
        data = tiny_results()
        other = json.loads(json.dumps(next(iter(data.values()))))
        other["cells"]["Object->Runnable"] = 0
        data["other"] = other
        with self.assertRaises(SystemExit):
            gen.one_answer(data, "cells")

    def test_two_environments_that_agree_do_not(self):
        data = tiny_results()
        data["other"] = json.loads(json.dumps(next(iter(data.values()))))
        self.assertEqual(gen.one_answer(data, "cells"), tiny_cells())

    def test_counts_sum_across_environments_rather_than_averaging(self):
        data = tiny_results()
        data["other"] = json.loads(json.dumps(next(iter(data.values()))))
        self.assertEqual(gen.summed(data, "ops")["INVOKEINTERFACE"], 14)

    def test_the_module_name_does_not_sum(self):
        data = tiny_results()
        data["other"] = json.loads(json.dumps(next(iter(data.values()))))
        self.assertNotIn("module", gen.summed(data, "census"))


class TestTheMeasurements(unittest.TestCase):
    """The committed results, checked against themselves without reading the page."""

    @classmethod
    def setUpClass(cls):
        cls.results = gen.read(ROOT / gen.RESULTS, "probes/verify/run.py")

    def test_there_is_more_than_one_environment(self):
        self.assertGreater(len(self.results), 1)

    def test_every_result_says_which_probe_and_issue_it_came_from(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["probe"], "verify")
                self.assertEqual(found["issue"], 15)
                self.assertTrue(found["java_build"])

    def test_the_module_scanned_is_the_one_every_image_has(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["census"]["module"], "java.base")

    def test_every_ordered_pair_of_the_measured_types_has_an_answer(self):
        for name, found in self.results.items():
            with self.subTest(name):
                order = found["order"]
                self.assertEqual(len(order), found["grid"]["types"])
                for a in order:
                    for b in order:
                        self.assertIn(found["cells"][f"{a}->{b}"], (0, 1))

    def test_no_cell_was_unmeasurable(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["grid"]["unmeasurable"], 0)
                self.assertNotIn(-1, set(found["cells"].values()))

    def test_the_pairs_worked_out_by_hand_all_agreed(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["worked_by_hand"]["wrong"], 0)
                self.assertGreaterEqual(len(found["worked_by_hand"]), 10)

    def test_every_environment_measured_the_same_relation(self):
        cells = [found["cells"] for found in self.results.values()]
        for other in cells[1:]:
            self.assertEqual(other, cells[0])

    def test_the_relation_is_reflexive_on_every_type_measured(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["properties"]["reflexive"], len(found["order"]))

    def test_the_relation_is_neither_transitive_nor_antisymmetric(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertGreater(found["properties"]["nontransitive"], 0)
                self.assertGreater(found["properties"]["mutual"], 0)

    def test_object_is_in_the_group_the_verifier_cannot_tell_apart(self):
        for name, found in self.results.items():
            with self.subTest(name):
                groups = gen.classes(found["mutually_assignable"], found["order"])
                self.assertIn("Object", groups[0])
                self.assertGreater(len(groups[0]), 1)

    def test_every_refusal_came_back_in_one_sentence_shape(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(len(found["refusal_shapes"]), 1)
                self.assertEqual(sum(found["refusal_shapes"].values()),
                                 found["grid"]["cells"] - found["grid"]["assignable"])

    def test_the_hole_verifies_and_then_fails_at_run_time(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["hole"]["verifies"], 1)
                self.assertEqual(found["hole"]["call_verifies"], 1)
                self.assertIn("IncompatibleClassChangeError", found["hole"]["throws"])

    def test_the_control_for_the_hole_was_refused(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertEqual(found["hole"]["control_verifies"], 0)
                self.assertIn("not assignable", found["hole"]["control_rejected_by"])

    def test_a_correct_frame_loads_at_every_version_measured(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertGreaterEqual(len(found["failover"]), 3)
                for major, seen in found["failover"].items():
                    self.assertEqual(seen["right_loads"], 1, major)

    def test_a_wrong_frame_loads_below_the_failover_version_and_not_above(self):
        for name, found in self.results.items():
            with self.subTest(name):
                for major, seen in found["failover"].items():
                    self.assertEqual(bool(seen["wrong_loads"]), int(major) < 51, major)

    def test_the_census_counts_more_virtual_calls_than_interface_calls(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertGreater(found["ops"]["INVOKEVIRTUAL"],
                                   found["ops"]["INVOKEINTERFACE"])

    def test_the_census_counted_a_module_larger_than_the_checks_in_it(self):
        for name, found in self.results.items():
            with self.subTest(name):
                self.assertGreater(found["census"]["instructions"],
                                   sum(found["ops"].values()))
                self.assertGreater(found["census"]["classes"], 1000)


class TestTheCommittedPage(unittest.TestCase):
    """The page against the results, so a stale page is a failure rather than a surprise."""

    @classmethod
    def setUpClass(cls):
        cls.page = ROOT / gen.OUTPUT
        cls.text = cls.page.read_text(encoding="utf-8") if cls.page.is_file() else ""
        cls.results = gen.read(ROOT / gen.RESULTS, "probes/verify/run.py")
        cls.one = next(iter(cls.results.values()))

    def total(self, field: str, where: str = "ops") -> int:
        return sum(found[where][field] for found in self.results.values())

    def test_the_page_is_there_and_says_it_is_generated(self):
        self.assertTrue(self.page.is_file())
        self.assertIn("<!-- generated: tools/gen_verify.py", self.text)

    def test_every_type_measured_is_a_row_of_the_grid(self):
        for number, name in enumerate(self.one["order"], start=1):
            with self.subTest(name):
                self.assertIn(f"| {number}. `{name}` |", self.text)

    def test_the_grid_has_a_row_for_every_type_and_no_more(self):
        rows = re.findall(r"^\| \d+\. `.+?` \|", self.text, re.M)
        self.assertEqual(len(rows), len(self.one["order"]))

    def test_every_cell_on_the_page_is_the_cell_that_was_measured(self):
        order = self.one["order"]
        for number, a in enumerate(order, start=1):
            row = re.search(rf"^\| {number}\. `{re.escape(a)}` \| (.+) \|$",
                            self.text, re.M)
            self.assertIsNotNone(row, a)
            marks = [one.strip() for one in row.group(1).split("|")]
            self.assertEqual(len(marks), len(order))
            for mark, b in zip(marks, order):
                with self.subTest(f"{a}->{b}"):
                    self.assertEqual(mark, "y" if self.one["cells"][f"{a}->{b}"] == 1
                                     else ".")

    def test_the_assignable_count_on_the_page_is_the_measured_one(self):
        self.assertIn(f"{self.one['grid']['assignable']} of "
                      f"{self.one['grid']['cells']} cells are assignable", self.text)

    def test_the_page_says_the_relation_is_a_preorder_because_it_measured_that(self):
        self.assertIn("So it is a preorder.", self.text)
        self.assertGreater(self.one["properties"]["nontransitive"], 0)
        self.assertGreater(self.one["properties"]["mutual"], 0)

    def test_every_non_transitive_triple_measured_is_named_on_the_page(self):
        for triple in self.one["nontransitive_triples"]:
            a, b, c = triple.split("->")
            with self.subTest(triple):
                self.assertIn(f"- `{a}` to `{b}` to `{c}`", self.text)

    def test_the_group_the_verifier_cannot_tell_apart_is_named(self):
        groups = gen.classes(self.one["mutually_assignable"], self.one["order"])
        self.assertIn(", ".join("`" + one + "`" for one in groups[0]), self.text)

    def test_the_page_quotes_the_comment_the_finding_rests_on(self):
        self.assertIn("we treat interfaces as java.lang.Object", self.text)

    def test_the_failover_table_is_the_failover_that_was_measured(self):
        for major, seen in sorted(self.one["failover"].items(), key=lambda kv: int(kv[0])):
            with self.subTest(major):
                row = re.search(rf"^\| {major} \| (.+?) \|", self.text, re.M)
                self.assertIsNotNone(row)
                self.assertEqual(row.group(1) == "loads", bool(seen["wrong_loads"]))

    def test_the_error_the_hole_throws_is_quoted_rather_than_described(self):
        self.assertIn(self.one["hole"]["throws"], self.text)

    def test_the_refusal_the_control_got_is_quoted_too(self):
        self.assertIn(self.one["hole"]["control_rejected_by"], self.text)

    def test_every_counted_instruction_is_a_row_with_the_total_that_was_counted(self):
        for name in self.one["ops"]:
            with self.subTest(name):
                self.assertIn(f"| `{name.lower()}` | {self.total(name):,} |", self.text)

    def test_the_interface_call_count_is_the_summed_one(self):
        self.assertIn(f"`invokeinterface` is the one this page is about: "
                      f"{self.total('INVOKEINTERFACE'):,} sites", self.text)

    def test_the_page_counts_the_environments_rather_than_naming_one(self):
        self.assertIn(f"in {gen.word(len(self.results))} environments", self.text)

    def test_the_clause_that_does_not_parse_is_reported(self):
        self.assertIn("does not parse", self.text)

    def test_the_page_carries_a_hash_of_all_four_sources(self):
        self.assertIn("## What this was read from", self.text)
        for source in (gen.TYPE_CPP, gen.VERIFIER_HPP, gen.VERIFIER_CPP):
            self.assertIn(f"`{source}`", self.text)
        digests = re.findall(r": `([0-9a-f]{16})`$", self.text, re.M)
        self.assertEqual(len(digests), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
