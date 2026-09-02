#!/usr/bin/env python3
"""Tests for the hprof counting parser.

A parser for a binary format is the easiest kind of code to be confidently wrong in.
It reads a file nobody looks at by eye, produces numbers nobody can check by hand, and
fails by drifting a byte at a time until it lands on a tag it does not recognise
thousands of records later. So most of these tests build a heap dump byte by byte with
a known answer in it, and then check that the parser gives that answer back.

The last test is the one that matters most: it asks a real JVM for a real dump of a
known number of objects and checks the count. Synthetic files test the decoding, and
only a real file tests that the decoding is of the right format.

  python tools/test_hprof_count.py
"""

from __future__ import annotations

import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hprof_count  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Builder:
    """Assemble an hprof file with known contents.

    Deliberately not written in terms of the parser's constants where it would hide a
    mistake. A builder that shares its tag numbers with the thing it is testing agrees
    with it about everything including its errors.
    """

    def __init__(self, idsize: int = 8):
        self.idsize = idsize
        self.records: list[bytes] = []
        self.heap: list[bytes] = []
        self.next_id = 0x1000

    def ident(self, value: int) -> bytes:
        return struct.pack(">Q" if self.idsize == 8 else ">I", value)

    def new_id(self) -> int:
        self.next_id += 8
        return self.next_id

    def record(self, tag: int, body: bytes) -> None:
        self.records.append(struct.pack(">BII", tag, 0, len(body)) + body)

    def string(self, text: str) -> int:
        key = self.new_id()
        self.record(0x01, self.ident(key) + text.encode("utf-8"))
        return key

    def load_class(self, name: str) -> int:
        name_id = self.string(name)
        class_id = self.new_id()
        self.record(
            0x02,
            struct.pack(">I", 1) + self.ident(class_id) + struct.pack(">I", 0)
            + self.ident(name_id),
        )
        return class_id

    def class_dump(self, class_id: int, statics: list[int] = (), fields: int = 0) -> None:
        body = self.ident(class_id) + struct.pack(">I", 0)
        body += self.ident(0) * 6  # super, loader, signers, domain, two reserved
        body += struct.pack(">I", 0)  # instance size
        body += struct.pack(">H", 0)  # no constant pool entries
        body += struct.pack(">H", len(statics))
        for tag in statics:
            width = self.idsize if tag == 2 else hprof_count.PRIMITIVE_WIDTH[tag]
            body += self.ident(self.string("s")) + struct.pack(">B", tag) + b"\0" * width
        body += struct.pack(">H", fields)
        for _ in range(fields):
            body += self.ident(self.string("f")) + struct.pack(">B", 10)
        self.heap.append(b"\x20" + body)

    def instance(self, class_id: int, field_bytes: int = 4) -> None:
        body = self.ident(self.new_id()) + struct.pack(">I", 0) + self.ident(class_id)
        body += struct.pack(">I", field_bytes) + b"\0" * field_bytes
        self.heap.append(b"\x21" + body)

    def object_array(self, class_id: int, count: int) -> None:
        body = self.ident(self.new_id()) + struct.pack(">I", 0)
        body += struct.pack(">I", count) + self.ident(class_id)
        body += b"\0" * (count * self.idsize)
        self.heap.append(b"\x22" + body)

    def primitive_array(self, type_tag: int, count: int) -> None:
        width = hprof_count.PRIMITIVE_WIDTH[type_tag]
        body = self.ident(self.new_id()) + struct.pack(">I", 0)
        body += struct.pack(">I", count) + struct.pack(">B", type_tag)
        body += b"\0" * (count * width)
        self.heap.append(b"\x23" + body)

    def root(self, tag: int, extra_u4: int = 0, second_id: bool = False) -> None:
        body = self.ident(self.new_id())
        if second_id:
            body += self.ident(0)
        body += b"\0" * (4 * extra_u4)
        self.heap.append(struct.pack(">B", tag) + body)

    def build(self) -> bytes:
        header = b"JAVA PROFILE 1.0.2\0" + struct.pack(">I", self.idsize)
        header += struct.pack(">Q", 0)
        segment = b"".join(self.heap)
        return header + b"".join(self.records) + struct.pack(">BII", 0x1C, 0, len(segment)) + segment


def parse(data: bytes) -> dict:
    parser = hprof_count.Hprof(data)
    parser.parse()
    return parser.summary()


class TestParsing(unittest.TestCase):
    def test_an_empty_dump_parses_to_nothing(self):
        found = parse(Builder().build())
        self.assertEqual(found["instance_total"], 0)
        self.assertEqual(found["array_total"], 0)

    def test_instances_are_counted_by_class_name(self):
        b = Builder()
        widget = b.load_class("com/example/Widget")
        gadget = b.load_class("com/example/Gadget")
        for _ in range(7):
            b.instance(widget)
        for _ in range(3):
            b.instance(gadget)
        found = parse(b.build())
        self.assertEqual(found["instances"]["com/example/Widget"], 7)
        self.assertEqual(found["instances"]["com/example/Gadget"], 3)
        self.assertEqual(found["instance_total"], 10)

    def test_arrays_are_counted_separately_from_instances(self):
        b = Builder()
        holder = b.load_class("[Lcom/example/Widget;")
        b.object_array(holder, 5)
        b.primitive_array(8, 12)  # byte[12]
        found = parse(b.build())
        self.assertEqual(found["instance_total"], 0)
        self.assertEqual(found["array_total"], 2)
        self.assertEqual(found["arrays"]["[byte"], 1)
        self.assertEqual(found["arrays"]["[Lcom/example/Widget;"], 1)

    def test_every_primitive_array_width_is_walked_correctly(self):
        # Each of these is followed by an instance the parser can only reach if it
        # skipped exactly the right number of element bytes.
        for tag in sorted(hprof_count.PRIMITIVE_WIDTH):
            with self.subTest(type=hprof_count.PRIMITIVE_NAME[tag]):
                b = Builder()
                marker = b.load_class("Marker")
                b.primitive_array(tag, 9)
                b.instance(marker)
                found = parse(b.build())
                self.assertEqual(found["instances"]["Marker"], 1)

    def test_a_class_dump_with_static_fields_does_not_swallow_the_next_record(self):
        # The regression test for the bug this parser shipped with. `self.pos +=
        # width(self.u1())` loads self.pos before it calls u1, so the byte u1 consumed
        # was added and then thrown away, one byte lost per static field. With a marker
        # instance right afterwards the loss shows up immediately instead of thousands
        # of records later.
        for statics in ([2], [2, 10, 11], [8] * 20):
            with self.subTest(statics=statics):
                b = Builder()
                described = b.load_class("Described")
                marker = b.load_class("Marker")
                b.class_dump(described, statics=statics)
                b.instance(marker)
                found = parse(b.build())
                self.assertEqual(found["instances"]["Marker"], 1)
                self.assertEqual(found["classes_dumped"], 1)

    def test_a_class_dump_with_instance_fields_is_walked_correctly(self):
        b = Builder()
        described = b.load_class("Described")
        marker = b.load_class("Marker")
        b.class_dump(described, fields=6)
        b.instance(marker)
        found = parse(b.build())
        self.assertEqual(found["instances"]["Marker"], 1)

    def test_every_root_record_shape_is_skipped_correctly(self):
        for tag, extra in sorted(hprof_count.ROOT_EXTRA_U4.items()):
            with self.subTest(root=hex(tag)):
                b = Builder()
                marker = b.load_class("Marker")
                b.root(tag, extra_u4=extra)
                b.instance(marker)
                self.assertEqual(parse(b.build())["instances"]["Marker"], 1)
        b = Builder()
        marker = b.load_class("Marker")
        b.root(0x01, second_id=True)
        b.instance(marker)
        self.assertEqual(parse(b.build())["instances"]["Marker"], 1)

    def test_four_byte_identifiers_work_too(self):
        # A 32 bit VM writes 4 byte ids. Nothing in this project produces one, but a
        # parser that only handles the size it was tested against is a parser that is
        # wrong the first time somebody hands it a dump from elsewhere.
        b = Builder(idsize=4)
        widget = b.load_class("Widget")
        for _ in range(4):
            b.instance(widget)
        found = parse(b.build())
        self.assertEqual(found["identifier_size"], 4)
        self.assertEqual(found["instances"]["Widget"], 4)

    def test_an_unknown_sub_record_is_an_error_and_not_a_wrong_answer(self):
        b = Builder()
        b.heap.append(b"\x7f" + b"\0" * 8)
        with self.assertRaises(ValueError) as caught:
            parse(b.build())
        self.assertIn("0x7f", str(caught.exception))

    def test_a_file_that_is_not_a_dump_says_so(self):
        with self.assertRaises(ValueError) as caught:
            parse(b"this is not a heap dump\0and never was")
        self.assertIn("not an hprof file", str(caught.exception))


def working_java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    binary = str(pathlib.Path(home) / "bin" / "java") if home else shutil.which("java")
    if binary is None or not pathlib.Path(binary).is_file():
        return None
    try:
        done = subprocess.run([binary, "-version"], capture_output=True, timeout=60)
    except OSError:
        return None
    return binary if done.returncode == 0 else None


JAVA = working_java()

DUMPER = """\
import com.sun.management.HotSpotDiagnosticMXBean;
import java.lang.management.ManagementFactory;

public class Dumper {
    static Thing[] kept;

    public static void main(String[] args) throws Exception {
        kept = new Thing[%d];
        for (int i = 0; i < kept.length; i++) {
            kept[i] = new Thing();
        }
        ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class)
            .dumpHeap(args[0], true);
    }

    static class Thing {
        int a;
    }
}
"""


class TestAgainstARealDump(unittest.TestCase):
    @unittest.skipUnless(JAVA, "no working java on PATH and no JAVA_HOME")
    def test_a_real_dump_of_a_known_number_of_objects_counts_right(self):
        wanted = 12_345
        with tempfile.TemporaryDirectory() as raw:
            work = pathlib.Path(raw)
            source = work / "Dumper.java"
            source.write_text(DUMPER % wanted, encoding="utf-8")
            dump = work / "heap.hprof"
            done = subprocess.run(
                [JAVA, str(source), str(dump)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertTrue(dump.is_file(), done.stdout + done.stderr)
            found = hprof_count.count(dump)

        matched = [name for name in found["instances"] if name.endswith("Thing")]
        self.assertEqual(len(matched), 1, matched)
        self.assertEqual(found["instances"][matched[0]], wanted)

        # A dump of a live heap always has these in it. If any is missing the parser is
        # reading the file but not reaching all of it.
        self.assertGreater(found["instances"]["java/lang/String"], 100)
        self.assertGreater(found["classes_loaded"], 500)
        self.assertGreater(found["array_total"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
