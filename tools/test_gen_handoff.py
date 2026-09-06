#!/usr/bin/env python3
"""Tests for the notebooks that get handed to a person.

The failure that matters here is not a malformed notebook, because a malformed notebook
announces itself the moment somebody opens it. It is a notebook that opens fine and
cannot produce the thing it was written to produce: no JSON block at the end, an issue
number that points at the wrong issue, or a protocol page that does not mention it. Each
of those costs somebody a whole sitting on a machine this project does not have.

  python tools/test_gen_handoff.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_handoff as gen  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "probes" / "handoff.md"


class TestTheParser(unittest.TestCase):
    def parse(self, text):
        return gen.parse(text, pathlib.Path("test.py"))

    def test_a_markdown_cell_loses_its_comment_prefix(self):
        found = self.parse("# %% [markdown]\n# # Title\n#\n# A paragraph.\n")
        self.assertEqual(found, [("markdown", "# Title\n\nA paragraph.")])

    def test_a_code_cell_keeps_everything(self):
        found = self.parse("# %%\nx = 1  # a comment\n")
        self.assertEqual(found, [("code", "x = 1  # a comment")])

    def test_anything_before_the_first_marker_is_dropped(self):
        """The module docstring is for somebody reading the source, not for the reader."""
        found = self.parse('"""Notes for an editor."""\n\n# %%\nx = 1\n')
        self.assertEqual(found, [("code", "x = 1")])

    def test_trailing_blank_lines_do_not_reach_the_notebook(self):
        found = self.parse("# %%\nx = 1\n\n\n")
        self.assertEqual(found, [("code", "x = 1")])

    def test_an_unknown_marker_is_a_hard_stop(self):
        """Rather than silently becoming a code cell full of prose."""
        with self.assertRaises(SystemExit):
            self.parse("# %% [raw]\nnothing\n")

    def test_a_file_with_no_cells_is_a_hard_stop(self):
        with self.assertRaises(SystemExit):
            self.parse("x = 1\n")


class TestTheNotebook(unittest.TestCase):
    def build(self, text):
        seen = {}
        return [gen.cell(kind, body, seen)
                for kind, body in gen.parse(text, pathlib.Path("test.py"))]

    def test_two_identical_cells_still_get_two_ids(self):
        """nbformat forbids a repeated id, and a notebook can repeat a cell."""
        cells = self.build("# %%\nprint(1)\n\n# %%\nprint(1)\n")
        self.assertNotEqual(cells[0]["id"], cells[1]["id"])

    def test_an_id_is_the_same_on_the_next_build(self):
        first = self.build("# %%\nprint(1)\n")[0]["id"]
        self.assertEqual(first, self.build("# %%\nprint(1)\n")[0]["id"])

    def test_a_code_cell_carries_no_session(self):
        cell = self.build("# %%\nprint(1)\n")[0]
        self.assertIsNone(cell["execution_count"])
        self.assertEqual(cell["outputs"], [])

    def test_the_source_is_split_the_way_nbformat_wants_it(self):
        cell = self.build("# %%\na = 1\nb = 2\n")[0]
        self.assertEqual(cell["source"], ["a = 1\n", "b = 2"])


class TestTheRealNotebooks(unittest.TestCase):
    """What CI runs, over the four that get handed to somebody."""

    def test_every_committed_notebook_matches_its_source(self):
        for name in sorted(gen.HANDOFFS):
            with self.subTest(handoff=name):
                path = gen.output(name)
                self.assertTrue(path.is_file(), f"{path} is missing")
                self.assertEqual(path.read_text(encoding="utf-8"), gen.notebook(name))

    def test_every_notebook_says_which_issue_it_answers(self):
        """In the metadata and in the first paragraph, and they have to agree.

        A results file pasted back three weeks from now is worth nothing if nobody can
        tell which question it answers.
        """
        for name, about in sorted(gen.HANDOFFS.items()):
            with self.subTest(handoff=name):
                book = json.loads(gen.output(name).read_text(encoding="utf-8"))
                self.assertEqual(book["metadata"]["jvx"]["issue"], about["issue"])
                text = "".join(book["cells"][0]["source"])
                self.assertIn(f"issues/{about['issue']}", text)

    def test_every_notebook_ends_by_printing_a_result(self):
        """The deliverable is one JSON block somebody can copy. No block, no probe."""
        for name in sorted(gen.HANDOFFS):
            with self.subTest(handoff=name):
                book = json.loads(gen.output(name).read_text(encoding="utf-8"))
                last = "".join(book["cells"][-1]["source"])
                self.assertIn("json.dumps(RESULT", last)
                self.assertEqual(last.count('print("-" * 72)'), 2,
                                 "the block needs a rule line at each end")

    def test_the_result_names_the_probe_and_the_issue(self):
        for name in sorted(gen.HANDOFFS):
            with self.subTest(handoff=name):
                text = gen.source(name).read_text(encoding="utf-8")
                self.assertIn('"probe":', text)
                self.assertIn(f'"issue": {gen.HANDOFFS[name]["issue"]}', text)

    def test_no_notebook_ships_an_output(self):
        """Somebody else's session baked into a notebook is not a measurement."""
        for name in sorted(gen.HANDOFFS):
            book = json.loads(gen.output(name).read_text(encoding="utf-8"))
            for cell in book["cells"]:
                if cell["cell_type"] == "code":
                    with self.subTest(handoff=name, cell=cell["id"]):
                        self.assertEqual(cell["outputs"], [])

    def test_the_protocol_page_mentions_every_one_of_them(self):
        """A notebook nobody is told to run is a notebook nobody runs."""
        text = PROTOCOL.read_text(encoding="utf-8")
        for name, about in sorted(gen.HANDOFFS.items()):
            with self.subTest(handoff=name):
                self.assertIn(f"probes/{name}/colab.ipynb", text)
                self.assertIn(f"issues/{about['issue']}", text)

    def test_the_protocol_page_covers_the_script_as_well(self):
        """jcstress has no notebook because it is not run in a browser, and it is still
        one of the five somebody has to run."""
        text = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("probes/jcstress/run.py", text)
        self.assertIn("issues/9", text)
        self.assertTrue((ROOT / "probes" / "jcstress" / "run.py").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
