#!/usr/bin/env python3
"""Tests for the O01 boss fight grader.

Two kinds of test live here. The first kind reads source and needs nothing installed,
so it runs everywhere including CI. The second kind actually starts a JVM, so it skips
when there is no java to be found. The skip is deliberate rather than a fixture: a
grader that measures is only honest if it is tested by measuring, and faking a JVM here
would test the fake.

  python lessons/O01/test_grade.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import grade  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]


def have_java() -> bool:
    """Run it rather than look for it.

    macOS ships a `/usr/bin/java` that exists, is executable, and does nothing except
    tell you there is no Java runtime. A guard that only checks the path is therefore
    true on a laptop with no JDK, and every measuring test below fails instead of
    skipping. The only honest question is whether the thing starts.
    """
    binary = grade.java()
    if binary is None:
        return False
    try:
        done = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=60
        )
    except OSError:
        return False
    return done.returncode == 0


HAVE_JAVA = have_java()
NEEDS_JAVA = unittest.skipUnless(HAVE_JAVA, "no working java on PATH and no JAVA_HOME")


def write(source: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".java", delete=False, encoding="utf-8"
    )
    handle.write(source)
    handle.close()
    return handle.name


class TestReading(unittest.TestCase):
    """Everything the grader can decide without starting a JVM."""

    def test_a_missing_file_says_so(self):
        ok, message = grade.grade("lessons/O01/there-is-no-such-file.java")
        self.assertFalse(ok)
        self.assertIn("does not exist", message)

    def test_a_class_by_another_name_is_rejected(self):
        ok, message = grade.grade(write("class Nominee { long x; }\n"))
        self.assertFalse(ok)
        self.assertIn("Candidate", message)

    def test_a_banned_type_is_caught_on_one_line(self):
        # The bug this test exists for: a line anchored pattern reads straight past
        # every field in a class whose body is written on a single line.
        ok, message = grade.grade(write("class Candidate { double d; long x; }\n"))
        self.assertFalse(ok)
        self.assertIn("double", message)

    def test_a_banned_type_is_caught_when_it_is_not_the_first_field(self):
        # The regression test for the semicolon eating bug. A pattern that consumes the
        # delimiter it starts from finds `long x` and then starts looking again after
        # the semicolon that `double d` needed, so it reads every second field and lets
        # the rest through. Position matters here, so both orders are checked.
        for body in ["long x; double d;", "long x; short s; int a;"]:
            with self.subTest(body=body):
                ok, message = grade.grade(write("class Candidate { %s }\n" % body))
                self.assertFalse(ok, message)

    def test_a_banned_type_is_caught_across_lines(self):
        source = "class Candidate {\n    long x;\n    short s;\n}\n"
        ok, message = grade.grade(write(source))
        self.assertFalse(ok)
        self.assertIn("short", message)

    def test_a_static_field_of_any_type_is_ignored(self):
        # A static is not part of an instance, so it cannot change the size and there
        # is nothing to police. This one would be rejected if the check looked at it.
        source = "class Candidate {\n    static double SCALE = 1.5;\n    long x;\n}\n"
        for found in grade.FIELD.finditer(source[source.index("class Candidate"):]):
            if "static" in found.group(0):
                continue
            self.assertRegex(found.group(1), grade.ALLOWED_TYPE)

    def test_reference_fields_are_allowed(self):
        body = "class Candidate { long x; Object r; String s; int[] a; }"
        types = [f.group(1) for f in grade.FIELD.finditer(body)]
        self.assertEqual(types, ["long", "Object", "String", "int[]"])
        for name in types:
            self.assertRegex(name, grade.ALLOWED_TYPE)


class TestMeasuring(unittest.TestCase):
    """The part that only means anything with a real JVM behind it."""

    @NEEDS_JAVA
    def test_a_known_good_answer_passes(self):
        ok, message = grade.grade(write("class Candidate { long x; int a; int b; }\n"))
        self.assertTrue(ok, message)
        self.assertIn("24", message)
        self.assertIn("32", message)

    @NEEDS_JAVA
    def test_the_other_known_good_answers_pass_too(self):
        # More than one class is correct here, which is the reason the grader measures
        # instead of matching. If any of these ever fails, the exercise has silently
        # become a guess the shape puzzle.
        for body in [
            "long x; long y;",
            "int a; int b; int c; int d;",
            "long x; int a; Object r;",
        ]:
            with self.subTest(body=body):
                ok, message = grade.grade(write("class Candidate { %s }\n" % body))
                self.assertTrue(ok, message)

    @NEEDS_JAVA
    def test_the_integer_trap_is_named_when_somebody_falls_in_it(self):
        # Fields that pad to the same size in both configurations. This is exactly the
        # mistake gate 3 is about, so the message has to point at it by name.
        ok, message = grade.grade(write("class Candidate { long x; int a; }\n"))
        self.assertFalse(ok)
        self.assertIn("Integer", message)

    @NEEDS_JAVA
    def test_too_many_fields_is_reported_as_a_count_not_a_layout(self):
        body = "int a; int b; int c; int d; int e; int f;"
        ok, message = grade.grade(write("class Candidate { %s }\n" % body))
        self.assertFalse(ok)
        self.assertIn("field count", message)

    @NEEDS_JAVA
    def test_a_failure_message_names_the_numbers(self):
        ok, message = grade.grade(write("class Candidate { int a; }\n"))
        self.assertFalse(ok)
        self.assertRegex(message, r"\d+ and should be \d+")

    @NEEDS_JAVA
    def test_the_grader_and_the_lesson_measure_the_same_thing(self):
        """The grader carries its own copy of the probe that `jvx.sizeProbe` builds.

        Two copies is the cheaper problem, but only while they agree. This runs both
        against the same class and checks that the size they report is the same number.
        """
        source = "class Candidate { long x; int a; int b; }"
        _, mine = grade.measure(source + "\n", compact=True)

        jsh = (ROOT / "jvx" / "20-jvx.jsh").read_text(encoding="utf-8")
        self.assertIn("static String sizeProbe(", jsh, "sizeProbe has been renamed")

        script = ROOT / "jvx"
        pieces = sorted(p.read_text(encoding="utf-8") for p in script.glob("*.jsh"))
        # `show` prints and returns void, so it goes on a line of its own, and its
        # first argument is the class name that `sizeProbe` gives its program.
        program = "\n".join(pieces) + '\njvx.show("Answer", jvx.sizeProbe("%s"), "%s");\n/exit\n' % (
            source.replace('"', '\\"'),
            grade.OPEN,
        )
        with tempfile.NamedTemporaryFile("w", suffix=".jsh", delete=False) as handle:
            handle.write(program)
            path = handle.name

        binary = pathlib.Path(grade.java()).parent / "jshell"
        if not binary.is_file():
            self.skipTest("no jshell next to java")
        done = subprocess.run(
            [str(binary), "-q", "-R" + grade.OPEN, path],
            capture_output=True,
            text=True,
            timeout=300,
        )
        found = re.search(r"object is (\d+) bytes", done.stdout)
        self.assertIsNotNone(found, done.stdout + done.stderr)
        self.assertEqual(int(found.group(1)), mine)


if __name__ == "__main__":
    if not HAVE_JAVA:
        print("no java found, so the measuring tests will skip", file=sys.stderr)
    unittest.main(verbosity=2)
