#!/usr/bin/env python3
"""Tests for build.py.

Standard library unittest, run with `python tools/test_build.py`. No fixtures on
disk except the ones each test writes into a temporary directory, so a test failure
points at the code rather than at a stale file somebody forgot to update.

The two tests that matter most are `test_three_builds_are_byte_identical` and
`test_check_catches_a_one_character_edit`, because those two are the promise the
whole pipeline rests on: the notebook is output, and if you edit it, CI says so.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build  # noqa: E402


LESSON = """\
# ---
# id: X01
# title: A test lesson
# question: Does the pipeline work?
# part: 0
# pin: jdk-27+35
# blueprints: [BP-TEST]
# requires: []
# flags: []
# terms: []
# reviews:
#   beginner: null
#   expert: null
# ---

# %% [markdown] id=badge generated=badge

# %% [markdown] id=hook
# A hook with a surprise in it.

# %% id=bootstrap generated=bootstrap env=E0

# %% id=setup env=E0
var x = 1;

# %% id=measure tags=[bake] env=E0
System.out.println(x);

# %% id=gate_1 tags=[predict] env=E0
jvx.gate("gate_1");

# %% [markdown] id=what_you_now_know
# You can read a notebook.
"""


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "pin.json").write_text(
            json.dumps({"jdk_tag": "jdk-27+35"}), encoding="utf-8"
        )
        repo = pathlib.Path(__file__).resolve().parent.parent
        shutil.copytree(repo / "jvx", self.root / "jvx")
        (self.root / "docs" / "generated").mkdir()
        shutil.copy(
            repo / "docs" / "generated" / "markword.json",
            self.root / "docs" / "generated" / "markword.json",
        )
        self.lesson_dir = self.root / "lessons" / "X01"
        (self.lesson_dir / "baked").mkdir(parents=True)
        (self.lesson_dir / "lesson.py").write_text(LESSON, encoding="utf-8")
        (self.lesson_dir / "grade.py").write_text("# grader\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, text: str) -> None:
        (self.lesson_dir / "lesson.py").write_text(text, encoding="utf-8")

    def load(self) -> build.Lesson:
        return build.load_lesson(self.lesson_dir / "lesson.py")

    def ctx(self) -> build.Context:
        return build.Context(self.root)


class TestParsing(Harness):
    def test_front_matter_reads_scalars_lists_and_nesting(self) -> None:
        lesson = self.load()
        self.assertEqual(lesson.front["id"], "X01")
        self.assertEqual(lesson.front["part"], 0)
        self.assertEqual(lesson.front["blueprints"], ["BP-TEST"])
        self.assertEqual(lesson.front["reviews"], {"beginner": None, "expert": None})

    def test_cells_come_out_in_source_order(self) -> None:
        lesson = self.load()
        self.assertEqual(
            [c.name for c in lesson.cells],
            ["badge", "hook", "bootstrap", "setup", "measure", "gate_1", "what_you_now_know"],
        )
        self.assertEqual(lesson.cells[0].cell_type, "markdown")
        self.assertEqual(lesson.cells[2].cell_type, "code")

    def test_markdown_loses_its_comment_prefix(self) -> None:
        lesson = self.load()
        self.assertEqual(lesson.cells[1].source, "A hook with a surprise in it.")

    def test_an_unknown_directive_is_rejected(self) -> None:
        self.write(LESSON.replace("id=setup env=E0", "id=setup enviroment=E0"))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("unknown directive", str(caught.exception))

    def test_an_unknown_tag_is_rejected(self) -> None:
        self.write(LESSON.replace("tags=[bake]", "tags=[baked]"))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("unknown tag", str(caught.exception))

    def test_a_bad_env_is_rejected(self) -> None:
        self.write(LESSON.replace("env=E0", "env=E9"))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("env is one of", str(caught.exception))

    def test_junk_on_the_marker_line_is_rejected(self) -> None:
        self.write(LESSON.replace("# %% id=setup env=E0", "# %% id=setup wat"))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("cannot read", str(caught.exception))

    def test_missing_front_matter_key_is_named(self) -> None:
        self.write(LESSON.replace("# question: Does the pipeline work?\n", ""))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("question", str(caught.exception))

    def test_id_must_match_the_directory(self) -> None:
        self.write(LESSON.replace("# id: X01", "# id: X02"))
        with self.assertRaises(build.LessonError) as caught:
            self.load()
        self.assertIn("directory", str(caught.exception))


class TestBuild(Harness):
    def test_three_builds_are_byte_identical(self) -> None:
        lesson = self.load()
        outputs = {build.build_notebook(lesson, self.ctx()) for _ in range(3)}
        self.assertEqual(len(outputs), 1)

    def test_a_rebuilt_notebook_is_valid_json_and_nbformat_4_5(self) -> None:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        self.assertEqual(notebook["nbformat"], 4)
        self.assertEqual(notebook["nbformat_minor"], 5)
        self.assertEqual(len(notebook["cells"]), 7)

    def test_cell_ids_are_unique_and_shaped_for_nbformat(self) -> None:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        ids = [c["id"] for c in notebook["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        for cell_id in ids:
            self.assertRegex(cell_id, r"^[a-f0-9]{16}$")

    def test_two_identical_cells_still_get_different_ids(self) -> None:
        doubled = LESSON + "\n# %% id=setup_again env=E0\nvar x = 1;\n"
        doubled = doubled.replace("# %% id=setup_again env=E0", "# %% env=E0")
        self.write(doubled)
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        ids = [c["id"] for c in notebook["cells"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_an_unchanged_cell_keeps_its_id_when_another_cell_changes(self) -> None:
        before = {
            c["metadata"].get("jvx_id"): c["id"]
            for c in json.loads(build.build_notebook(self.load(), self.ctx()))["cells"]
        }
        self.write(LESSON.replace("A hook with a surprise in it.", "A different hook."))
        after = {
            c["metadata"].get("jvx_id"): c["id"]
            for c in json.loads(build.build_notebook(self.load(), self.ctx()))["cells"]
        }
        self.assertNotEqual(before["hook"], after["hook"])
        self.assertEqual(before["setup"], after["setup"])
        self.assertEqual(before["gate_1"], after["gate_1"])

    def test_no_cell_carries_an_execution_count(self) -> None:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])

    def test_only_a_bake_cell_gets_output(self) -> None:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        by_name = {c["metadata"].get("jvx_id"): c for c in notebook["cells"]}
        self.assertEqual(by_name["setup"]["outputs"], [])
        self.assertTrue(by_name["measure"]["outputs"])

    def test_a_bake_cell_with_no_recording_says_so_instead_of_inventing_one(self) -> None:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        by_name = {c["metadata"].get("jvx_id"): c for c in notebook["cells"]}
        text = "".join(by_name["measure"]["outputs"][0]["data"]["text/plain"])
        self.assertIn("no recording yet", text)

    def test_a_recorded_output_is_used_verbatim(self) -> None:
        recorded = [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}]
        (self.lesson_dir / "baked" / "measure.json").write_text(
            json.dumps(recorded), encoding="utf-8"
        )
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        by_name = {c["metadata"].get("jvx_id"): c for c in notebook["cells"]}
        self.assertEqual(by_name["measure"]["outputs"], recorded)


class TestCheck(Harness):
    def build_notebooks(self) -> pathlib.Path:
        build.cmd_notebooks(self.root)
        return self.root / "notebooks" / "X01" / "lesson.ipynb"

    def test_check_passes_on_a_freshly_built_tree(self) -> None:
        self.build_notebooks()
        self.assertEqual(build.cmd_check(self.root), 0)

    def test_check_catches_a_one_character_edit(self) -> None:
        path = self.build_notebooks()
        text = path.read_text(encoding="utf-8")
        self.assertIn("var x = 1;", text)
        path.write_text(text.replace("var x = 1;", "var x = 2;"), encoding="utf-8")
        self.assertEqual(build.cmd_check(self.root), 1)

    def test_check_catches_a_notebook_that_was_never_built(self) -> None:
        self.assertEqual(build.cmd_check(self.root), 1)

    def test_the_drift_message_names_the_cell_that_was_edited(self) -> None:
        path = self.build_notebooks()
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("var x = 1;", "var x = 2;"), encoding="utf-8")
        message = build.describe_drift(
            path.read_text(encoding="utf-8"), build.build_notebook(self.load(), self.ctx())
        )
        self.assertIn("setup", message)

    def test_a_duplicate_cell_id_is_a_problem(self) -> None:
        self.write(LESSON.replace("id=gate_1 tags=[predict]", "id=setup tags=[predict]"))
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("already used" in p for p in problems))

    def test_a_bake_cell_without_an_id_is_a_problem(self) -> None:
        self.write(LESSON.replace("id=measure tags=[bake]", "tags=[bake]"))
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("bake cell needs an id" in p for p in problems))

    def test_a_lesson_with_no_prediction_gate_is_a_problem(self) -> None:
        self.write(LESSON.replace("tags=[predict]", ""))
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("no prediction gate" in p for p in problems))

    def test_a_pin_that_disagrees_with_docs_pin_json_is_a_problem(self) -> None:
        self.write(LESSON.replace("# pin: jdk-27+35", "# pin: jdk-26-ga"))
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("docs/pin.json says" in p for p in problems))

    def test_requires_pointing_at_nothing_is_a_problem(self) -> None:
        self.write(LESSON.replace("# requires: []", "# requires: [X99]"))
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("not a lesson" in p for p in problems))

    def test_a_requires_cycle_is_reported_with_its_path(self) -> None:
        a = build.Lesson(pathlib.Path("a"), {"id": "A", "requires": ["B"]}, [])
        b = build.Lesson(pathlib.Path("b"), {"id": "B", "requires": ["A"]}, [])
        problems = build.check_requires_dag([a, b])
        self.assertEqual(len(problems), 1)
        self.assertIn("A -> B -> A", problems[0])

    def test_too_many_tier_zero_cells_is_a_problem(self) -> None:
        extra = "".join(
            f"\n# %% id=filler_{n} env=E0\nvar f{n} = {n};\n"
            for n in range(build.CAP_E0_CELLS + 1)
        )
        self.write(LESSON + extra)
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(any("the cap is" in p for p in problems))


class TestScaffold(Harness):
    def test_new_creates_a_lesson_that_parses(self) -> None:
        self.assertEqual(build.cmd_new(self.root, "X02"), 0)
        lesson = build.load_lesson(self.root / "lessons" / "X02" / "lesson.py")
        self.assertEqual(lesson.id, "X02")
        self.assertEqual(lesson.front["pin"], "jdk-27+35")

    def test_new_refuses_to_overwrite(self) -> None:
        self.assertEqual(build.cmd_new(self.root, "X01"), 1)


class TestGenerated(Harness):
    """The badge and the bootstrap are written by the build, not by the author.

    That is the only way 112 lessons can be guaranteed to offer the same helper
    surface, and it is why the checks here are mostly about the source file being
    empty and the built file not being.
    """

    def source_of(self, kind: str) -> str:
        notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
        cells = [c for c in notebook["cells"] if c["metadata"].get("jvx_generated") == kind]
        self.assertEqual(len(cells), 1, f"expected one {kind} cell")
        return "".join(cells[0]["source"])

    def test_the_bootstrap_inlines_every_jvx_source(self) -> None:
        body = self.source_of("bootstrap")
        for path in sorted((self.root / "jvx").glob("*.jsh")):
            marker = f"jvx/{path.name}"
            self.assertIn(marker, body, f"{marker} is not named in the header")
        self.assertIn("class jvx {", body)
        self.assertIn("class MarkWord {", body)
        self.assertTrue(body.rstrip().endswith("jvx.banner()"))

    def test_the_bit_positions_come_from_the_generated_json(self) -> None:
        body = self.source_of("bootstrap")
        data = json.loads(
            (self.root / "docs" / "generated" / "markword.json").read_text(encoding="utf-8")
        )
        for field in data["fields"]:
            self.assertIn(
                f'new Field("{field["name"]}", {field["shift"]}, {field["bits"]},',
                body,
                f"{field['name']} is not in the bootstrap at the position the JSON gives",
            )

    def test_no_placeholder_survives_into_a_notebook(self) -> None:
        body = self.source_of("bootstrap")
        self.assertNotIn("@jvx:", body, "a placeholder would ship to a reader as literal text")

    def test_an_unfilled_placeholder_is_an_error_rather_than_a_shrug(self) -> None:
        (self.root / "jvx" / "99-broken.jsh").write_text(
            "// @jvx:nothing_fills_this@\n", encoding="utf-8"
        )
        with self.assertRaises(build.LessonError) as caught:
            build.build_notebook(self.load(), self.ctx())
        self.assertIn("@jvx:nothing_fills_this@", str(caught.exception))

    def test_the_badge_points_at_this_lesson(self) -> None:
        body = self.source_of("badge")
        self.assertIn("colab.research.google.com", body)
        self.assertIn(f"notebooks/{self.load().id}/lesson.ipynb", body)
        self.assertIn(self.load().front["question"], body)

    def test_changing_jvx_changes_the_bootstrap_cell_id(self) -> None:
        """A generated cell hashes what ships, not the empty placeholder.

        If it hashed the source, editing jvx would change what every reader runs while
        every cell id stayed the same, and the diff would show nothing.
        """
        def bootstrap_cell() -> dict:
            notebook = json.loads(build.build_notebook(self.load(), self.ctx()))
            return next(
                c for c in notebook["cells"] if c["metadata"].get("jvx_generated") == "bootstrap"
            )

        before = bootstrap_cell()
        path = self.root / "jvx" / "20-jvx.jsh"
        path.write_text(path.read_text(encoding="utf-8") + "\n// one more line\n", encoding="utf-8")
        after = bootstrap_cell()

        self.assertNotEqual(before["id"], after["id"])
        self.assertNotEqual(before["source"], after["source"])

    def test_writing_in_a_generated_cell_is_caught(self) -> None:
        self.write(
            (self.lesson_dir / "lesson.py").read_text(encoding="utf-8").replace(
                "# %% id=bootstrap generated=bootstrap env=E0\n",
                "# %% id=bootstrap generated=bootstrap env=E0\nSystem.out.println(1);\n",
            )
        )
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(
            any("generated cell has an empty body" in p for p in problems), problems
        )

    def test_a_lesson_without_a_bootstrap_is_caught(self) -> None:
        self.write(
            (self.lesson_dir / "lesson.py").read_text(encoding="utf-8").replace(
                "# %% id=bootstrap generated=bootstrap env=E0\n", ""
            )
        )
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(
            any("0 cells with generated=bootstrap" in p for p in problems), problems
        )

    def test_a_code_cell_above_the_bootstrap_is_caught(self) -> None:
        self.write(
            (self.lesson_dir / "lesson.py").read_text(encoding="utf-8").replace(
                "# %% [markdown] id=hook",
                "# %% id=too_early env=E0\nvar early = 1;\n\n# %% [markdown] id=hook",
            )
        )
        problems = build.check_structure(self.root, build.load_all(self.root))
        self.assertTrue(
            any("not the first code cell" in p for p in problems), problems
        )

    def test_a_missing_markword_json_names_the_generator_to_run(self) -> None:
        (self.root / "docs" / "generated" / "markword.json").unlink()
        with self.assertRaises(build.LessonError) as caught:
            build.build_notebook(self.load(), self.ctx())
        self.assertIn("tools/gen_markword.py", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
