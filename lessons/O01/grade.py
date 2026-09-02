#!/usr/bin/env python3
"""Grade the boss fight for O01.

The task: a class called `Candidate`, at most four fields, only `int`, `long` and
reference types, whose instances are 24 bytes with compact object headers on and 32
bytes with them off.

The grader measures rather than reads. It wraps the reader's class in a program that
asks Unsafe where every field sits, runs that program twice in two fresh JVMs, and
compares the two sizes. A grader that pattern matched on the source would pass a class
that looks right and fails, and would fail a correct class written in a way nobody
thought of. There is more than one right answer here and measuring accepts all of them.

Exits 0 when the artifact is correct and 1 when it is not. The failure message names
what is wrong and by how much, because "incorrect" teaches nobody anything.

  python lessons/O01/grade.py lessons/O01/answer.java
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

WANT_COMPACT = 24
WANT_LEGACY = 32
MAX_FIELDS = 4
OPEN = "--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED"

# The same measuring program `jvx.sizeProbe` builds in the lesson, written out again
# here rather than parsed out of `jvx/20-jvx.jsh`. Two copies of twenty five lines is
# a smaller problem than a grader that breaks when somebody reformats a text block,
# and `test_grade.py` runs both against the same class to keep them agreeing.
PROBE = """\
import jdk.internal.misc.Unsafe;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;

public class Answer {
    public static void main(String[] args) {
        Unsafe u = Unsafe.getUnsafe();
        long end = 0;
        int count = 0;
        for (Class<?> c = Candidate.class; c != null; c = c.getSuperclass()) {
            for (Field f : c.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers())) continue;
                count++;
                end = Math.max(end, u.objectFieldOffset(c, f.getName()) + width(u, f.getType()));
            }
        }
        System.out.printf("fields=%d size=%d%n", count, (end + 7) / 8 * 8);
    }

    static int width(Unsafe u, Class<?> t) {
        if (t == long.class || t == double.class) return 8;
        if (t == int.class || t == float.class) return 4;
        if (t == short.class || t == char.class) return 2;
        if (t == byte.class || t == boolean.class) return 1;
        return u.arrayIndexScale(Object[].class);
    }
}
"""

RESULT = re.compile(r"fields=(\d+) size=(\d+)")

# Only these are allowed. The point of the exercise is the interaction between header
# size and 8 byte alignment, and a `double` or a `short` would let somebody reach the
# answer without ever meeting it. Arrays are allowed whatever they hold, because a
# `double[]` field is a reference and the element type never appears in this layout.
ALLOWED_TYPE = re.compile(r"^(?:int|long|(?:[\w.]*\.)?[A-Z]\w*|[\w.]+(?:\[\])+)$")

# A lookbehind for the brace or semicolon rather than a match on it, because matches do
# not overlap: a pattern that eats the semicolon it starts from eats the one the next
# field needs to start from, and then the grader reads every second field and waves the
# rest through. Not anchored to the start of a line either, because
# `class Candidate { double d; long x; }` is an ordinary thing to write.
FIELD = re.compile(
    r"(?<=[{;])\s*(?:(?:private|public|protected|final|volatile|transient|static)\s+)*"
    r"([\w.]+(?:\[\])*)\s+(\w+)\s*(?:=[^;]*)?;"
)


def java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = pathlib.Path(home) / "bin" / "java"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("java")


def measure(source: str, compact: bool) -> tuple[int, int] | str:
    """Run the reader's class in a fresh JVM and return (field count, size in bytes)."""
    binary = java()
    if binary is None:
        return "no java on PATH and no JAVA_HOME, so nothing can be measured"

    with tempfile.TemporaryDirectory() as raw:
        path = pathlib.Path(raw) / "Answer.java"
        path.write_text(PROBE + "\n" + source + "\n", encoding="utf-8")
        command = [binary, OPEN]
        if not compact:
            command.append("-XX:-UseCompactObjectHeaders")
        command.append(str(path))
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)

    found = RESULT.search(done.stdout)
    if not found:
        detail = (done.stdout + done.stderr).strip().splitlines()
        first = detail[0] if detail else "no output at all"
        return f"the program did not run: {first}"
    return int(found.group(1)), int(found.group(2))


def grade(artifact: str) -> tuple[bool, str]:
    path = pathlib.Path(artifact)
    if not path.is_file():
        return False, f"{artifact} does not exist. Save your class there first."

    source = path.read_text(encoding="utf-8")
    if not re.search(r"\bclass\s+Candidate\b", source):
        return False, (
            f"{artifact} has no class called Candidate. The grader wraps your class in a "
            f"program that measures it, and that program looks for Candidate by name."
        )

    body = source[source.index("class Candidate"):]
    for found in FIELD.finditer(body):
        # A static field is not part of an instance, so it cannot affect the size and
        # there is no reason to police its type.
        if "static" in found.group(0):
            continue
        if not ALLOWED_TYPE.match(found.group(1)):
            return False, (
                f"field {found.group(2)} is a {found.group(1)}, and this exercise allows "
                f"only int, long and reference types. A double or a short would let you "
                f"reach 24 and 32 without meeting the alignment rule the task is about."
            )

    compact = measure(source, compact=True)
    if isinstance(compact, str):
        return False, compact
    legacy = measure(source, compact=False)
    if isinstance(legacy, str):
        return False, legacy

    count, compact_size = compact
    _, legacy_size = legacy

    if count > MAX_FIELDS:
        return False, (
            f"Candidate has {count} instance fields and the limit is {MAX_FIELDS}. "
            f"The sizes come out at {compact_size} and {legacy_size}, so the layout is "
            f"not the problem, the field count is."
        )

    if compact_size == WANT_COMPACT and legacy_size == WANT_LEGACY:
        return True, (
            f"Correct. {count} fields, {compact_size} bytes with compact headers and "
            f"{legacy_size} without. You made the alignment padding fall differently in "
            f"the two configurations, which is the only way to get a gap of 8 out of a "
            f"header that changed by 4."
        )

    problems = []
    if compact_size != WANT_COMPACT:
        problems.append(
            f"compact is {compact_size} and should be {WANT_COMPACT} "
            f"({'too big' if compact_size > WANT_COMPACT else 'too small'} "
            f"by {abs(compact_size - WANT_COMPACT)})"
        )
    if legacy_size != WANT_LEGACY:
        problems.append(
            f"legacy is {legacy_size} and should be {WANT_LEGACY} "
            f"({'too big' if legacy_size > WANT_LEGACY else 'too small'} "
            f"by {abs(legacy_size - WANT_LEGACY)})"
        )

    hint = ""
    gap = legacy_size - compact_size
    if gap == 0:
        hint = (
            " Both configurations give the same size, which means the 4 bytes the header "
            "gave back went into padding, exactly like Integer. Your fields need to end "
            "on a boundary that the shorter header changes."
        )
    elif gap == 8 and compact_size != WANT_COMPACT:
        hint = (
            " The gap of 8 is right, so the shape is correct and only the total is off. "
            "Adding or removing 4 bytes of fields should move both numbers together."
        )
    return False, "; ".join(problems) + "." + hint


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: grade.py <artifact>", file=sys.stderr)
        raise SystemExit(2)
    ok, message = grade(sys.argv[1])
    print(message)
    raise SystemExit(0 if ok else 1)
