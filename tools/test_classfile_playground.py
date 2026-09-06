#!/usr/bin/env python3
"""Tests for the class file playground, most of which run the reader's own notebook.

M2's exit criterion is that a reader can build a class file by hand in a notebook cell
that runs and that javap agrees with. That is a claim about a specific notebook rather
than about a library, so the strongest test here takes the code cells out of the built
notebook, feeds them to a real jshell with the real bootstrap, and checks what came back.
A test that exercised a copy of that code would pass on the day the page stopped working.

The rest are about the two ways this could rot quietly. Cf hands out constant pool
indices and patches length fields, and both are arithmetic a reader cannot check by
reading, so both are checked here against what javap makes of the result. And every
opcode number the playground uses is looked up on the reader's JDK rather than stored,
so the lookup is checked against the committed opcode table, which was generated from
three sources that had to agree.

  python tools/test_classfile_playground.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build  # noqa: E402
import test_jvx_ui as ui  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "playgrounds" / "classfile.ipynb"
OPCODE_TABLE = ROOT / "docs" / "generated" / "opcodes.md"

# `| 0 `0x00` | `nop` | 1 | ...`, which is the shape gen_opcodes.py writes.
OPCODE_ROW = re.compile(r"^\|\s*(\d+)\s+`0x[0-9a-f]+`\s*\|\s*`([a-z_0-9]+)`\s*\|")

# The four opcodes a class file may contain that java.lang.classfile has no constant for,
# for two different reasons. `wide` is a prefix, and the class file API folds it into the
# twelve wide forms instead, which docs/generated/opcodes.md says in its own words.
# `breakpoint`, `impdep1` and `impdep2` are the three the specification reserves for a
# debugger and for an implementation {[JVMS §6.2@SE25]}, and no class file may contain
# them, so a library for reading and writing class files has no use for them.
#
# Naming them here rather than skipping anything that fails to resolve is the point. The
# day a new opcode is added and the class file API has not caught up, this test says so.
NO_CONSTANT = {"wide", "breakpoint", "impdep1", "impdep2"}


def notebook_code() -> str:
    """Every code cell of the playground, in order, exactly as a reader runs them."""
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    return "\n\n".join(
        "".join(cell["source"]) for cell in cells if cell["cell_type"] == "code"
    )


# Run after the notebook's own cells, so `c` is the file the reader built and every
# assertion below is about that file rather than about one written for the test.
PROBES = """
mark("javap");
System.out.println(Cf.javap(bytes));

mark("dedup");
System.out.println(c.utf8("main") + " " + c.classEntry("Handwritten"));

mark("slots");
Cf two = jvx.cf();
System.out.println(two.longEntry(7L) + " " + two.utf8("after") + " " + two.longEntry(7L));

mark("nul");
System.out.println(Cf.modifiedUtf8("a\\u0000b").length + " "
    + (Cf.modifiedUtf8("a\\u0000b")[1] & 0xff));

mark("lengths");
System.out.println(c.field("code_length").length() + " " + c.field("attribute_length").length());

mark("open");
Cf half = jvx.cf();
half.u2("x", 1);
half.openU4("attribute_length");
try { half.bytes(); System.out.println("no complaint"); }
catch (IllegalStateException e) { System.out.println("refused: " + e.getMessage()); }

mark("late");
Cf shut = jvx.cf();
shut.utf8("first");
shut.pool();
try { shut.utf8("second"); System.out.println("no complaint"); }
catch (IllegalStateException e) { System.out.println("refused"); }

mark("opcodes");
for (String name : new String[] { "nop", "getstatic", "ldc", "invokevirtual", "return",
        "aload_0", "invokespecial", "ireturn", "goto", "lookupswitch" }) {
    System.out.print(name + "=" + Cf.opcode(name) + " ");
}
System.out.println();

mark("table");
StringBuilder every = new StringBuilder();
for (Opcode o : Opcode.values()) {
    if (!o.isWide()) every.append(o.name().toLowerCase(Locale.ROOT))
        .append('=').append(o.bytecode()).append(' ');
}
System.out.println(every);

mark("end");
/exit
"""


class TestTheNotebookARunsAndAgrees(unittest.TestCase):
    """The exit criterion itself, asked of the committed notebook."""

    @classmethod
    def setUpClass(cls):
        if not ui.JSHELL:
            raise unittest.SkipTest("no jshell, so the playground cannot be run")
        cls.parts = ui.run_driver(
            'String M = "--8<--";\n'
            + "void mark(String name) { System.out.println(M + name); }\n"
            + 'mark("notebook");\n'
            + notebook_code()
            + PROBES,
            # HotSpot's VerifyError prints a block headed `Exception Details:`, which is
            # the notebook working rather than the notebook failing. It is the only line
            # of the playground's output that reads like a jshell diagnostic.
            noise=("Exception Details:",),
        )

    def test_the_class_the_reader_built_prints_when_a_fresh_jvm_runs_it(self):
        # run_driver already fails on any exception or rejected snippet, so reaching
        # here means all 30 or so cells compiled and ran. This is the output.
        everything = "\n".join(self.parts.values())
        self.assertIn("hello from a file I typed", everything)

    def test_javap_reads_the_handwritten_file(self):
        # The disassembler is the JDK's own, so agreeing with it is agreeing with the
        # reader that javac would have produced the same shape.
        javap = self.parts["javap"]
        self.assertIn("public class Handwritten", javap)
        self.assertIn("public static void main(java.lang.String[])", javap)
        self.assertNotIn("Error", javap)

    def test_the_vm_accepted_it(self):
        self.assertIn("the VM accepted it: class Handwritten", self.parts["notebook"])

    def test_the_page_says_what_the_vm_actually_says(self):
        """The prose names four refusals. A reader gets to compare them with the output.

        This is the failure this file is really here for. Every other kind of drift in a
        lesson is visible to somebody reading it; a paragraph explaining an error message
        that stopped being the error message reads perfectly and is a lie.
        """
        printed = self.parts["notebook"]
        self.assertIn("VerifyError", printed)
        self.assertIn("Exceeded max stack size", printed)
        self.assertIn("Arguments can't fit into locals", printed)
        self.assertIn("Unknown constant tag 0", printed)
        self.assertIn("Incompatible magic value", printed)

    def test_the_hook_says_the_size_the_file_actually_is(self):
        """The first paragraph names a byte count, and a reader meets it before any code.

        A number in a hook is the one number nobody checks, because it arrives before
        there is anything on the screen to check it against. This one moves the moment
        somebody adds a pool entry or an instruction, so it is held to the file.
        """
        printed = re.search(r"^(\d+) bytes$", self.parts["notebook"], re.M)
        self.assertIsNotNone(printed, "the size cell printed nothing")
        page = (ROOT / "playgrounds" / "classfile" / "playground.py").read_text(
            encoding="utf-8"
        )
        claimed = re.search(r"is (\d+) bytes of nothing mysterious", page)
        self.assertIsNotNone(claimed, "the hook stopped naming a size")
        self.assertEqual(claimed.group(1), printed.group(1))

    def test_a_pool_entry_asked_for_twice_is_written_once(self):
        # `main` and `Handwritten` were both asked for while the file was built. Asking
        # again has to give the same index, because a second copy would be a second
        # entry, every index after it would move, and the file would still be valid,
        # which is the worst kind of wrong: it works and it is twice the size.
        again = self.parts["dedup"].split()
        self.assertEqual(again, ["19", "2"])

    def test_a_long_takes_two_slots_and_the_next_entry_knows(self):
        first, after, again = self.parts["slots"].split()
        self.assertEqual(first, "1")
        self.assertEqual(after, "3", "the entry after a long is two higher, not one")
        self.assertEqual(again, "1", "the same long is the same entry")

    def test_a_zero_character_is_two_bytes_and_not_one(self):
        # This is the whole difference between modified UTF-8 and UTF-8 that a reader is
        # likely to hit, and getting it wrong produces a file javap reads happily and
        # the VM refuses with a message about the constant pool.
        length, second = self.parts["nul"].split()
        self.assertEqual(length, "4", "a, then two bytes for the zero, then b")
        self.assertEqual(second, "192", "0xc0, the first byte of the two byte form")

    def test_the_length_fields_are_the_width_the_format_says(self):
        self.assertEqual(self.parts["lengths"].split(), ["4", "4"])

    def test_a_length_left_open_is_refused_rather_than_written_as_zero(self):
        # A zero length attribute is a valid class file that describes an empty method,
        # so this mistake does not fail on its own. It fails as something else later.
        self.assertTrue(self.parts["open"].startswith("refused:"), self.parts["open"])

    def test_a_pool_entry_asked_for_after_the_pool_is_written_is_refused(self):
        self.assertEqual(self.parts["late"], "refused")

    def test_the_opcodes_the_playground_names_resolve(self):
        found = dict(pair.split("=") for pair in self.parts["opcodes"].split())
        self.assertEqual(found["nop"], "0")
        self.assertEqual(found["getstatic"], "178")
        self.assertEqual(found["return"], "177")
        self.assertEqual(found["aload_0"], "42")

    def test_every_opcode_in_the_committed_table_resolves_to_the_same_byte(self):
        """The generated table and the runtime lookup are two answers to one question.

        The table comes from JVMS chapter 7, HotSpot's bytecodes.cpp and the class file
        API, checked against each other by tools/gen_opcodes.py. The playground asks the
        class file API directly, on whatever JDK the reader has. If those two ever
        disagree, one of the two documents in front of the reader is wrong.
        """
        live = dict(
            pair.split("=") for pair in self.parts["table"].split() if "=" in pair
        )
        rows = 0
        for line in OPCODE_TABLE.read_text(encoding="utf-8").splitlines():
            match = OPCODE_ROW.match(line)
            if not match:
                continue
            number, mnemonic = match.group(1), match.group(2)
            rows += 1
            if mnemonic in NO_CONSTANT:
                self.assertNotIn(
                    mnemonic, live, f"{mnemonic} has a constant now, so the note is stale"
                )
                continue
            with self.subTest(opcode=mnemonic):
                self.assertIn(mnemonic, live, f"{mnemonic} is in the table and not on this JDK")
                self.assertEqual(live[mnemonic], number)
        self.assertGreater(rows, 200, "the opcode table did not parse")


class TestTheSource(unittest.TestCase):
    """Rules about the helper that hold whether or not there is a JDK here."""

    def setUp(self):
        self.text = (ROOT / "jvx" / "12-classfile.jsh").read_text(encoding="utf-8")

    def test_it_sorts_before_the_file_that_forwards_to_it(self):
        # build.py inlines the jvx sources in filename order, and JShell will not accept
        # a method body naming a class that has not been declared yet. So the number in
        # the filename is load bearing, and this is the test that says so.
        names = sorted(path.name for path in (ROOT / "jvx").glob("*.jsh"))
        self.assertLess(names.index("12-classfile.jsh"), names.index("20-jvx.jsh"))

    def test_no_pool_tag_is_typed_into_the_helper(self):
        """Every tag comes off the JDK, which is the point of the file.

        A table copied in here would be right today, wrong on the JDK that adds a
        constant kind, and wrong in a way nobody notices, because the file it produces
        would still load on the JDK it was copied from.
        """
        for constant in ("TAG_UTF8", "TAG_CLASS", "TAG_METHODREF", "TAG_LONG"):
            self.assertIn(f"PoolEntry.{constant}", self.text)
        code = "\n".join(
            line
            for line in self.text.splitlines()
            if not line.strip().startswith(("//", "*", "/*"))
        )
        for typed in ("cafebabe", "= 178", "TAG_UTF8 = ", "tag = 1;"):
            self.assertNotIn(typed, code.lower() if typed.islower() else code)

    def test_the_playground_takes_its_constants_from_the_jdk_too(self):
        # The page a reader edits is the one that has to model the habit. Writing
        # 0xcafebabe into a cell would work and would teach the opposite of the point.
        page = (ROOT / "playgrounds" / "classfile" / "playground.py").read_text(
            encoding="utf-8"
        )
        for constant in ("ClassFile.MAGIC_NUMBER", "ClassFile.latestMajorVersion()"):
            self.assertIn(constant, page)

    def test_the_playground_is_a_playground(self):
        lesson = build.load_lesson(ROOT / "playgrounds" / "classfile" / "playground.py")
        self.assertTrue(lesson.is_playground)
        self.assertEqual(
            build.notebook_path(ROOT, lesson).relative_to(ROOT).as_posix(),
            "notebooks/playgrounds/classfile.ipynb",
        )

    def test_the_notebook_is_built_from_the_source(self):
        # build.py check says this too. It is repeated here because this file runs the
        # notebook, and running a stale notebook proves something about a file nobody
        # will read again.
        lesson = build.load_lesson(ROOT / "playgrounds" / "classfile" / "playground.py")
        built = build.build_notebook(lesson, build.Context(ROOT))
        self.assertEqual(NOTEBOOK.read_text(encoding="utf-8"), built)


if __name__ == "__main__":
    if not ui.JSHELL:
        print(
            "no jshell found, so the tests that run the playground will skip. Point "
            "JAVA_HOME at the pinned JDK to run them.",
            file=sys.stderr,
        )
    unittest.main(verbosity=2)
