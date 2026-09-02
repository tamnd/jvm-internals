#!/usr/bin/env python3
"""Count what is in an hprof heap dump, by class, without opening a GUI.

The JDK writes heap dumps that nothing in the JDK will summarise on the command line.
`jhat` is gone, Mission Control and VisualVM both want a window, and the probe that
needs these numbers runs on machines reached over SSH. So this reads the format.

It is a counting parser and not a heap analyser. It never reconstructs an object graph
or resolves a field, which is what makes it short: every record in the format carries
its own length or a length that can be computed from a type tag, so the whole file can
be walked while decoding only the few record types that carry a name or a count.

  python tools/hprof_count.py heap.hprof
  python tools/hprof_count.py heap.hprof --json
  python tools/hprof_count.py heap.hprof --match 'REPL|JShell'

Format reference: the hprof binary format is documented in the JDK sources at
src/hotspot/share/services/heapDumper.cpp, in the comment block at the top.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import struct
import sys

# Top level record tags. Only the three that carry names or heap contents are decoded.
TAG_STRING = 0x01
TAG_LOAD_CLASS = 0x02
TAG_HEAP_DUMP = 0x0C
TAG_HEAP_DUMP_SEGMENT = 0x1C

# Sub-record tags inside a heap dump segment.
SUB_CLASS_DUMP = 0x20
SUB_INSTANCE_DUMP = 0x21
SUB_OBJECT_ARRAY_DUMP = 0x22
SUB_PRIMITIVE_ARRAY_DUMP = 0x23

# Roots. Every one of these is a fixed number of bytes after the tag, so they can be
# skipped by table lookup. The value is the number of extra u4 fields after the leading
# id, except for the two that carry a second id instead.
ROOT_EXTRA_U4 = {
    0xFF: 0,  # unknown
    0x05: 0,  # sticky class
    0x07: 0,  # monitor used
    0x02: 2,  # jni local
    0x03: 2,  # java frame
    0x04: 1,  # native stack
    0x06: 1,  # thread block
    0x08: 2,  # thread object
}
ROOT_SECOND_ID = {0x01}  # jni global carries a second id

# Primitive type tags and their widths. 2 is an object reference, whose width is the
# identifier size the file header declares, so it is filled in at parse time.
PRIMITIVE_WIDTH = {4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
PRIMITIVE_NAME = {
    2: "object",
    4: "boolean",
    5: "char",
    6: "float",
    7: "double",
    8: "byte",
    9: "short",
    10: "int",
    11: "long",
}


class Hprof:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.strings: dict[int, str] = {}
        self.class_name: dict[int, str] = {}
        self.instances: collections.Counter[str] = collections.Counter()
        self.instance_bytes: collections.Counter[str] = collections.Counter()
        self.arrays: collections.Counter[str] = collections.Counter()
        self.array_bytes: collections.Counter[str] = collections.Counter()
        self.classes = 0
        self.idsize = 4

    # Reading primitives. `pos` is an index into one bytes object rather than a file
    # handle, because a heap dump that does not fit in memory is a different problem
    # than this tool has, and indexing is several times faster than seeking.

    def u1(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u2(self) -> int:
        value = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u4(self) -> int:
        value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def ident(self) -> int:
        if self.idsize == 8:
            value = struct.unpack_from(">Q", self.data, self.pos)[0]
        else:
            value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += self.idsize
        return value

    def width(self, tag: int) -> int:
        return self.idsize if tag == 2 else PRIMITIVE_WIDTH[tag]

    def header(self) -> None:
        end = self.data.index(b"\0", 0)
        magic = self.data[:end].decode("ascii")
        if not magic.startswith("JAVA PROFILE"):
            raise ValueError(f"not an hprof file, it starts with {magic!r}")
        self.magic = magic
        self.pos = end + 1
        self.idsize = self.u4()
        if self.idsize not in (4, 8):
            raise ValueError(f"identifier size is {self.idsize}, expected 4 or 8")
        self.pos += 8  # timestamp, a u8 nobody here needs

    def parse(self) -> None:
        self.header()
        size = len(self.data)
        while self.pos < size:
            tag = self.u1()
            self.u4()  # microseconds since the header, unused
            length = self.u4()
            end = self.pos + length
            if tag == TAG_STRING:
                key = self.ident()
                self.strings[key] = self.data[self.pos:end].decode("utf-8", "replace")
            elif tag == TAG_LOAD_CLASS:
                self.u4()
                object_id = self.ident()
                self.u4()
                name_id = self.ident()
                # Class names arrive in JVM internal form. Nothing here depends on the
                # dotted form and a slash is easier to grep for than an escaped dot.
                self.class_name[object_id] = self.strings.get(name_id, "?")
            elif tag in (TAG_HEAP_DUMP, TAG_HEAP_DUMP_SEGMENT):
                self.segment(end)
            self.pos = end

    def segment(self, end: int) -> None:
        while self.pos < end:
            tag = self.u1()
            if tag in ROOT_EXTRA_U4:
                self.pos += self.idsize + 4 * ROOT_EXTRA_U4[tag]
            elif tag in ROOT_SECOND_ID:
                self.pos += 2 * self.idsize
            elif tag == SUB_CLASS_DUMP:
                self.class_dump()
            elif tag == SUB_INSTANCE_DUMP:
                self.instance_dump()
            elif tag == SUB_OBJECT_ARRAY_DUMP:
                self.object_array_dump()
            elif tag == SUB_PRIMITIVE_ARRAY_DUMP:
                self.primitive_array_dump()
            else:
                raise ValueError(f"unknown sub-record tag 0x{tag:02x} at {self.pos - 1}")

    def class_dump(self) -> None:
        self.classes += 1
        self.ident()  # class object id
        self.u4()  # stack trace serial
        self.pos += 5 * self.idsize  # super, loader, signers, protection domain, 2 spare
        self.pos += self.idsize
        self.u4()  # instance size

        # The type tag goes into a local before the skip. `self.pos += width(self.u1())`
        # looks equivalent and is not: Python loads `self.pos` before it evaluates the
        # right hand side, so the byte `u1` consumed is added and then thrown away. That
        # loses one byte per field and the file only stops making sense thousands of
        # records later, which is a bad afternoon.
        for _ in range(self.u2()):  # constant pool
            self.u2()
            tag = self.u1()
            self.pos += self.width(tag)
        for _ in range(self.u2()):  # static fields
            self.ident()
            tag = self.u1()
            self.pos += self.width(tag)
        for _ in range(self.u2()):  # instance fields
            self.ident()
            self.u1()

    def instance_dump(self) -> None:
        self.ident()
        self.u4()
        class_id = self.ident()
        length = self.u4()
        self.pos += length
        name = self.class_name.get(class_id, "?")
        self.instances[name] += 1
        # What the dump says the fields occupy. This is not the object's size in the
        # heap: it has no header and no alignment padding, because a dump records field
        # values and not layout. Reported under its own name for that reason.
        self.instance_bytes[name] += length

    def object_array_dump(self) -> None:
        self.ident()
        self.u4()
        count = self.u4()
        class_id = self.ident()
        self.pos += count * self.idsize
        name = self.class_name.get(class_id, "?")
        self.arrays[name] += 1
        self.array_bytes[name] += count * self.idsize

    def primitive_array_dump(self) -> None:
        self.ident()
        self.u4()
        count = self.u4()
        tag = self.u1()
        width = self.width(tag)
        self.pos += count * width
        name = "[" + PRIMITIVE_NAME[tag]
        self.arrays[name] += 1
        self.array_bytes[name] += count * width

    def summary(self) -> dict:
        return {
            "format": self.magic,
            "identifier_size": self.idsize,
            "file_bytes": len(self.data),
            "classes_dumped": self.classes,
            "classes_loaded": len(self.class_name),
            "instance_total": sum(self.instances.values()),
            "instance_field_bytes": sum(self.instance_bytes.values()),
            "array_total": sum(self.arrays.values()),
            "array_element_bytes": sum(self.array_bytes.values()),
            "instances": dict(self.instances),
            "arrays": dict(self.arrays),
        }


def count(path: pathlib.Path) -> dict:
    parser = Hprof(path.read_bytes())
    parser.parse()
    return parser.summary()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump", type=pathlib.Path)
    ap.add_argument("--json", action="store_true", help="machine readable, everything")
    ap.add_argument("--match", help="regular expression, only classes whose name matches")
    ap.add_argument("--top", type=int, default=20, help="how many classes to print")
    args = ap.parse_args(argv)

    found = count(args.dump)
    if args.json:
        json.dump(found, sys.stdout, indent=2, sort_keys=True)
        print()
        return 0

    print(f"{args.dump}")
    print(f"  {found['file_bytes']:,} bytes, {found['identifier_size']} byte identifiers")
    print(f"  {found['classes_loaded']:,} classes loaded, {found['classes_dumped']:,} dumped")
    print(f"  {found['instance_total']:,} instances, {found['array_total']:,} arrays")

    rows = collections.Counter(found["instances"]) + collections.Counter(found["arrays"])
    if args.match:
        import re

        keep = re.compile(args.match)
        rows = collections.Counter({k: v for k, v in rows.items() if keep.search(k)})
        print(f"  {sum(rows.values()):,} of them match {args.match}")
    print()
    for name, number in rows.most_common(args.top):
        print(f"  {number:>10,}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
