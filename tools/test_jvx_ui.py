#!/usr/bin/env python3
"""Tests for the markup jvx puts on the screen.

Two kinds of test. The first reads the helper sources and needs nothing installed, and it
is the one that guards the rules probes/widgets measured: no style tag, no id, no script,
no onclick, no iframe, no form control. Those five are not style preferences. Each one is
something a saved notebook throws away, so a widget that uses one looks right on the
machine it was written on and is broken for every reader who has not run the page.

The second kind runs the real helper surface in a real jshell and reads the markup back,
because a rule checked against source is a rule checked against the wrong thing. It skips
when there is no JDK, the same way the grader tests do.

  python tools/test_jvx_ui.py
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The four that survive a notebook nobody has run, measured on two platforms and written
# up in docs/probes/widgets.md. Everything the helper surface draws is built from these.
SURVIVES = ["style attribute", "details and summary", "img with a data URI", "markdown"]

# Each of these is removed, renamed or disabled by the sanitizer. The pattern is what it
# looks like in markup, and the sentence is what the reader loses when it is used.
BANNED = [
    (r"<style", "a style tag is removed, and the class that wanted it is kept"),
    (r"\sid=", "an id is renamed to data-jupyter-id, so nothing that looks it up matches"),
    (r"<script", "a script tag is removed"),
    (r"\son[a-z]+=", "an event handler attribute is stripped"),
    (r"<iframe", "an iframe is removed"),
    (r"<input", "a form control arrives disabled, so the reader cannot use it"),
    (r"<button", "a button with nothing behind it is a lie about what the page can do"),
]

DRIVER = """
String M = "--8<--";
void mark(String name) { System.out.println(M + name); }

mark("ask");
System.out.println(Gate.askHtml("gate_1",
    "How many bytes does a bare `new Object()` occupy on JDK 27?",
    new String[] { "a) 8", "b) 12", "c) 16", "d) it depends on the platform" }));

mark("answer");
System.out.println(Gate.answerHtml("gate_1", "c"));

mark("right");
System.out.println(Gate.revealHtml("gate_1", "a", "a"));

mark("wrong");
System.out.println(Gate.revealHtml("gate_1", "a", "c"));

mark("silent");
System.out.println(Gate.revealHtml("gate_1", "a", null));

mark("picture");
System.out.println(Ui.img("<svg xmlns='http://www.w3.org/2000/svg'></svg>", "a picture"));

mark("escaping");
System.out.println(Ui.prose("a <b> & a `code span`, a \\"quote\\" and a `dangling"));

mark("rich");
System.out.println(Ui.rich());

// Every class in the surface, named once, so one that failed to compile is a failure
// here rather than a surprise for a reader. jshell accepts a snippet with an unresolved
// reference and only complains when something uses it, which is exactly how a broken
// bootstrap can look fine.
mark("loaded");
System.out.println(jvx.PIN + " " + MarkWord.hex(0L).length() + " " + Gate.question.size()
    + " " + Ui.esc("<").length());

mark("end");
/exit
"""


# The kernel's imports, and only these.
#
# A jshell you start in a terminal imports java.nio.file.*, java.util.stream.* and
# java.util.function.* for you. JJava does not. It builds its own list, this one, taken
# from org.dflib.jjava.distro.NotebookInitializer in jjava 1.0a8. The difference is not
# academic: the helper surface loaded fine in a terminal and failed in the kernel with
# `cannot find symbol: variable jvx`, because one class in it named Path. Starting the
# test jshell with this list and nothing else is what makes a terminal able to answer a
# question about the notebook.
KERNEL_IMPORTS = """
import java.util.*;
import java.io.*;
import java.math.*;
import java.net.*;
import java.time.*;
import java.util.concurrent.*;
import java.util.prefs.*;
import java.util.regex.*;
"""


def printf_lines(text: str) -> list[int]:
    """Line numbers where something the kernel would run calls printf.

    Java text blocks are taken out first. A text block in this project is always a whole
    program handed to `jvx.run`, which starts a fresh JVM and reads its output in one go,
    so printf inside one is fine and printf outside one is not. Toggling on every triple
    quote is enough, because nothing in here nests them.
    """
    found = []
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.count('"""') % 2 == 1:
            inside = not inside
            continue
        if not inside and "System.out.printf" in line:
            found.append(number)
    return found


def jshell() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = pathlib.Path(home) / "bin" / "jshell"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("jshell")


JSHELL = jshell()
NEEDS_JSHELL = unittest.skipUnless(
    JSHELL, "no jshell, so the markup cannot be read back out of the thing that makes it"
)


def run_driver() -> dict[str, str]:
    """Load the whole helper surface into a jshell and print the markup it builds."""
    bootstrap = build.Context(ROOT).bootstrap()
    with tempfile.TemporaryDirectory() as directory:
        startup = pathlib.Path(directory) / "startup.jsh"
        startup.write_text(KERNEL_IMPORTS, encoding="utf-8")
        script = pathlib.Path(directory) / "drive.jsh"
        script.write_text(bootstrap + "\n" + DRIVER, encoding="utf-8")
        done = subprocess.run(
            [JSHELL, "--execution", "local", "--startup", str(startup), "-s", str(script)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    trouble = "\n".join(
        line for line in done.stdout.splitlines()
        if "Error:" in line or "cannot find symbol" in line or "REJECTED" in line
    )
    if trouble:
        raise AssertionError("jshell refused part of the helper surface:\n" + trouble)
    if "--8<--end" not in done.stdout:
        raise AssertionError(
            "the driver did not reach the end, so something in jvx did not load:\n"
            + done.stdout[-4000:]
            + done.stderr[-2000:]
        )
    pieces = {}
    current = None
    for line in done.stdout.splitlines():
        if line.startswith("--8<--"):
            current = line[len("--8<--"):].strip()
            pieces[current] = []
        elif current:
            pieces[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in pieces.items()}


class TestTheSources(unittest.TestCase):
    """Rules that hold whatever the markup turns out to say."""

    def setUp(self):
        self.sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "jvx").glob("*.jsh"))
        }

    def test_no_helper_uses_anything_a_saved_notebook_throws_away(self):
        for name, text in self.sources.items():
            # Comments explain what is banned and why, so they mention the very things
            # this test looks for. Stripping them is what keeps the rule enforceable
            # without making the file impossible to explain.
            code = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("//")
            )
            for pattern, why in BANNED:
                with self.subTest(file=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, code), f"{name}: {why}")

    def test_nothing_the_kernel_runs_uses_printf(self):
        # The kernel sends every write on System.out as its own stream message, and
        # java.util.Formatter writes each padding space on its own. So `%-28s` reaches
        # the reader as thirty messages and the notebook renders each on its own line.
        # The banner did exactly this, and it looked like the JVM was broken rather than
        # the printing. String.format then println is one message and one line.
        for name, text in self.sources.items():
            with self.subTest(file=name):
                self.assertEqual(printf_lines(text), [], f"jvx/{name}: format it first")

    def test_no_lesson_cell_uses_printf_either(self):
        for lesson in sorted((ROOT / "lessons").glob("*/lesson.py")):
            text = lesson.read_text(encoding="utf-8")
            with self.subTest(lesson=lesson.name):
                self.assertEqual(printf_lines(text), [], f"{lesson}: format it first")

    def test_only_one_place_talks_to_the_front_end(self):
        # Every display call echoes the id it assigned. One wrapper swallows it, and the
        # fix only holds while nothing else calls display on its own.
        callers = [name for name, text in self.sources.items() if "displayMethod" in text]
        self.assertEqual(callers, ["05-ui.jsh"])

    def test_the_measured_rules_are_written_down_where_somebody_will_read_them(self):
        # The helper points at the measurement it was built from, and the measurement is
        # still there. Somebody reading this code in a year needs both halves.
        self.assertIn("probes/widgets", self.sources["05-ui.jsh"])
        self.assertIn("docs/probes/widgets.md", self.sources["05-ui.jsh"])
        report = (ROOT / "docs" / "probes" / "widgets.md").read_text(encoding="utf-8")
        for phrase in ["<details>", "data URI", "sanitize"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, report)


@NEEDS_JSHELL
class TestTheMarkup(unittest.TestCase):
    """What the helper surface actually builds, read out of a running jshell."""

    @classmethod
    def setUpClass(cls):
        cls.parts = run_driver()

    def test_the_question_and_every_option_are_on_the_card(self):
        card = self.parts["ask"]
        self.assertIn("How many bytes", card)
        for option in ["8", "12", "16", "it depends on the platform"]:
            self.assertIn(option, card)
        for letter in "abcd":
            self.assertIn(f">{letter}</span>", card)

    def test_the_question_card_does_not_contain_the_answer(self):
        # The gate is the one place in this project where showing too much is not a
        # cosmetic problem, so it gets its own test rather than a comment.
        self.assertNotIn("answer is", self.parts["ask"])

    def test_backticks_in_a_lesson_become_code_spans(self):
        self.assertIn("<code", self.parts["ask"])
        self.assertIn("new Object()", self.parts["ask"])

    def test_markup_in_a_lesson_is_escaped_and_an_unclosed_backtick_is_left_alone(self):
        text = self.parts["escaping"]
        self.assertIn("&lt;b&gt;", text)
        self.assertIn("&amp;", text)
        self.assertIn("&quot;quote&quot;", text)
        self.assertIn("<code", text)
        self.assertIn("`dangling", text)

    def test_a_reader_who_answered_is_told_whether_they_were_right(self):
        self.assertIn("that is right", self.parts["right"])
        self.assertIn("The answer is", self.parts["wrong"])
        self.assertIn(">c<", self.parts["wrong"])

    def test_a_reader_who_did_not_answer_has_to_open_the_answer(self):
        # This is the reason the card version exists at all. On the published page
        # nothing has been run, so every reveal would otherwise hand out its answer to
        # somebody scrolling past, and the gate would be decoration.
        silent = self.parts["silent"]
        self.assertIn("<details", silent)
        self.assertNotIn("<details open", silent)
        summary = silent.split("<summary")[1].split("</summary>")[0]
        self.assertNotIn("answer is", summary)
        self.assertIn("The answer is", silent.split("</summary>")[1])

    def test_a_reader_who_did_answer_is_not_made_to_click(self):
        for name in ["right", "wrong"]:
            with self.subTest(reveal=name):
                self.assertNotIn("<details", self.parts[name])

    def test_a_picture_is_an_img_with_a_data_uri(self):
        picture = self.parts["picture"]
        self.assertIn('src="data:image/svg+xml;base64,', picture)
        self.assertIn('alt="a picture"', picture)
        self.assertNotIn("<svg", picture)

    def test_every_card_the_helpers_build_obeys_the_measured_rules(self):
        for name, markup in self.parts.items():
            if not markup.startswith("<"):
                continue
            for pattern, why in BANNED:
                with self.subTest(card=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, markup), f"{name}: {why}")

    def test_the_whole_helper_surface_loads_with_only_the_imports_the_kernel_has(self):
        # The driver names every class in the surface, so reaching this line at all is
        # most of the test. The pin is checked too, because a bootstrap that loads and
        # then tells a reader it is pinned to nothing is its own kind of broken.
        pin = build.load_pin(ROOT)["jdk_tag"]
        self.assertTrue(self.parts["loaded"].startswith(pin), self.parts["loaded"])

    def test_a_plain_jshell_is_not_mistaken_for_a_notebook(self):
        # There is no kernel here, so the helper has to say so and let the caller print
        # text. A false positive would mean a terminal reader gets silence.
        self.assertEqual(self.parts["rich"], "false")


if __name__ == "__main__":
    if not JSHELL:
        print(
            "no jshell found, so the tests that read markup out of a running jshell will "
            "skip. Point JAVA_HOME at the pinned JDK to run them.",
            file=sys.stderr,
        )
    unittest.main(verbosity=2)
