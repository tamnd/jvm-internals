#!/usr/bin/env python3
"""Tests for gen_markword.py.

The fixture below is the shape of the real `markWord.hpp` constant chain, cut down to
what the generator reads. It is a fixture rather than the real file so the tests run
with no network and so a change upstream shows up in the `--check` job rather than as
a mysterious unit test failure. Those are different problems and they want different
error messages.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_markword as gen  # noqa: E402


FIXTURE = """\
// The markWord describes the header of an object.
//
//  64 bits (without compact headers):
//  ----------------------------------
//  unused:22  hash:31  valhalla:4  age:4  self-fwd:1  lock:2
//
//  64 bits (with compact headers):
//  -------------------------------
//  klass:22   hash:31  valhalla:4  age:4  self-fwd:1  lock:2

class markWord {
 public:
  static const int lock_bits                      = 2;
  static const int self_fwd_bits                  = 1;
  static const int age_bits                       = 4;
  static const int valhalla_reserved_bits         = LP64_ONLY(4) NOT_LP64(0);
  static const int max_hash_bits                  = BitsPerWord - age_bits - lock_bits - self_fwd_bits - valhalla_reserved_bits;
  static const int hash_bits                      = max_hash_bits > 31 ? 31 : max_hash_bits;

  static const int lock_shift                     = 0;
  static const int self_fwd_shift                 = lock_shift + lock_bits;
  static const int age_shift                      = self_fwd_shift + self_fwd_bits;
  static const int valhalla_reserved_shift        = age_shift + age_bits;
  static const int hash_shift                     = valhalla_reserved_shift + valhalla_reserved_bits;

  static const uintptr_t lock_mask_in_place       = right_n_bits(lock_bits) << lock_shift;

  static constexpr int klass_offset_in_bytes      = 4;
  static constexpr int klass_shift                = hash_shift + hash_bits;
  static constexpr int klass_bits                 = 22;
};
"""


class TestEvaluate(unittest.TestCase):
    def test_a_plain_number(self) -> None:
        self.assertEqual(gen.evaluate("2", {}), 2)

    def test_a_sum_of_constants_below_it(self) -> None:
        self.assertEqual(gen.evaluate("a + b", {"a": 3, "b": 4}), 7)

    def test_lp64_only_keeps_the_64_bit_value(self) -> None:
        self.assertEqual(gen.evaluate("LP64_ONLY(4) NOT_LP64(0)", {}), 4)

    def test_the_ternary_picks_the_right_branch(self) -> None:
        self.assertEqual(gen.evaluate("m > 31 ? 31 : m", {"m": 53}), 31)
        self.assertEqual(gen.evaluate("m > 31 ? 31 : m", {"m": 20}), 20)

    def test_an_unknown_name_raises_rather_than_guessing(self) -> None:
        with self.assertRaises(ValueError) as caught:
            gen.evaluate("right_n_bits(lock_bits)", {"lock_bits": 2})
        self.assertIn("not defined yet", str(caught.exception))

    def test_something_outside_the_subset_raises(self) -> None:
        with self.assertRaises(ValueError):
            gen.evaluate('"a string"', {})


class TestParse(unittest.TestCase):
    def setUp(self) -> None:
        self.known, self.lines = gen.parse(FIXTURE)

    def test_the_widths_come_out(self) -> None:
        self.assertEqual(self.known["lock_bits"], 2)
        self.assertEqual(self.known["self_fwd_bits"], 1)
        self.assertEqual(self.known["age_bits"], 4)
        self.assertEqual(self.known["valhalla_reserved_bits"], 4)
        self.assertEqual(self.known["klass_bits"], 22)

    def test_the_hash_width_is_capped_at_31(self) -> None:
        self.assertEqual(self.known["max_hash_bits"], 53)
        self.assertEqual(self.known["hash_bits"], 31)

    def test_the_shifts_chain_correctly(self) -> None:
        self.assertEqual(self.known["lock_shift"], 0)
        self.assertEqual(self.known["self_fwd_shift"], 2)
        self.assertEqual(self.known["age_shift"], 3)
        self.assertEqual(self.known["valhalla_reserved_shift"], 7)
        self.assertEqual(self.known["hash_shift"], 11)
        self.assertEqual(self.known["klass_shift"], 42)

    def test_a_constant_it_cannot_read_is_skipped_not_guessed(self) -> None:
        self.assertNotIn("lock_mask_in_place", self.known)

    def test_every_constant_records_the_line_it_came_from(self) -> None:
        self.assertEqual(FIXTURE.split("\n")[self.lines["klass_shift"] - 1].strip(),
                         "static constexpr int klass_shift                = hash_shift + hash_bits;")

    def test_the_fields_fill_the_word_exactly(self) -> None:
        total = sum(
            self.known[bits] for _, _, bits, _ in gen.FIELDS
        )
        self.assertEqual(total, 64)

    def test_no_two_fields_overlap(self) -> None:
        spans = []
        for _, shift_const, bits_const, _ in gen.FIELDS:
            shift = self.known[shift_const]
            spans.append((shift, shift + self.known[bits_const]))
        spans.sort()
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertEqual(end, start)

    def test_both_diagrams_are_found(self) -> None:
        found = gen.layout_comment_lines(FIXTURE)
        self.assertEqual(set(found), {"compact", "legacy"})
        self.assertLess(found["legacy"], found["compact"])


class TestCommittedOutput(unittest.TestCase):
    """The committed file has to say what the generator says, and be internally sane.

    This does not fetch anything. It reads what is on disk, which is what a lesson
    and a diagram actually consume, and checks the properties that would make one of
    them silently wrong.
    """

    def setUp(self) -> None:
        import json

        root = pathlib.Path(__file__).resolve().parent.parent
        path = root / gen.OUTPUT
        if not path.is_file():
            self.skipTest(f"{gen.OUTPUT} is not generated yet")
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def test_the_fields_fill_the_word(self) -> None:
        self.assertEqual(self.data["total_field_bits"], self.data["word_bits"])

    def test_every_mask_matches_its_shift_and_width(self) -> None:
        for field in self.data["fields"]:
            expected = ((1 << field["bits"]) - 1) << field["shift"]
            self.assertEqual(int(field["mask"], 16), expected, field["name"])

    def test_every_field_cites_the_line_it_came_from(self) -> None:
        for field in self.data["fields"]:
            for kind in ("shift", "bits"):
                citation = field["defined_at"][kind]
                self.assertIn("markWord.hpp:", citation)
                self.assertIn("@", citation)

    def test_the_recorded_hash_is_a_sha256(self) -> None:
        self.assertRegex(self.data["source"]["sha256"], r"^[0-9a-f]{64}$")

    def test_the_observed_mark_word_decodes_to_the_observed_identity_hash(self) -> None:
        """The one test that is not about the generator.

        These two 64 bit values were read out of a live JDK 27 on aarch64, before and
        after calling System.identityHashCode on the same object, which returned
        0x1dfe2924. If applying the generated shifts to the second value does not
        produce that number, then the layout this repository publishes is wrong in a
        way that no amount of parsing correctness would catch.
        """
        before = 0x0017AC0000000011
        after = 0x0017ACEFF1492011
        observed_hash = 0x1DFE2924

        by_name = {f["name"]: f for f in self.data["fields"]}

        def extract(word: int, name: str) -> int:
            field = by_name[name]
            return (word >> field["shift"]) & ((1 << field["bits"]) - 1)

        self.assertEqual(extract(before, "hash"), 0)
        self.assertEqual(extract(after, "hash"), observed_hash)
        self.assertEqual(extract(before, "klass"), extract(after, "klass"))
        self.assertEqual(extract(after, "lock"), 0b01)
        self.assertEqual(extract(after, "self_fwd"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
