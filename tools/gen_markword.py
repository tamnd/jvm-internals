#!/usr/bin/env python3
"""Generate the object header bit layout from HotSpot's own header file.

The mark word is 64 bits carrying five things at once, and every lesson about object
layout, locking, identity hash and garbage collection ages has to say where each one
sits. Those positions are not in the JVMS. They are not in any book that is current.
They are in `src/hotspot/share/oops/markWord.hpp`, expressed as a chain of constants
that each define themselves in terms of the one below.

So this reads that file at the pinned tag and works the chain out, rather than a
person reading it once and typing the numbers into a diagram. A number typed by hand
is a number that is wrong after the next release and that nobody notices, and the
positions in this particular file moved twice in the last three releases.

The output is `docs/generated/markword.json`: every field with its shift, its width,
its mask, and the line of `markWord.hpp` each of those came from. The line numbers
are what make the citations in a lesson resolvable, and the SHA-256 of the whole file
is what makes a claim that the file has not changed underneath us checkable.

  python tools/gen_markword.py            regenerate docs/generated/markword.json
  python tools/gen_markword.py --check    regenerate in memory and fail on a difference
  python tools/gen_markword.py --print    print the layout as a table

The source is fetched from raw.githubusercontent.com at the tag in docs/pin.json, or
read from a local OpenJDK checkout when JVX_JDK_SRC points at one, which is what
makes this work offline and in a build tree.

This is the small, working half of `bpc`. The other half reads the Serviceability
Agent type database out of a live VM, which is a better source because it is what the
VM itself believes, and it is a harder one because it needs a process to attach to.
Both are needed and they check each other. Where they disagree, the disagreement is
the interesting thing rather than a bug in one of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HEADER_PATH = "src/hotspot/share/oops/markWord.hpp"
RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"

OUTPUT = pathlib.Path("docs/generated/markword.json")

# `static const int lock_bits = 2;` and `static constexpr int klass_bits = 22;`.
# Both spellings appear in the file and both mean the same thing here.
CONSTANT = re.compile(
    r"^\s*static\s+const(?:expr)?\s+(?:int|uintptr_t)\s+"
    r"(?P<name>\w+)\s*=\s*(?P<expr>[^;]+);"
)

# The subset of C++ the constant chain is written in. Anything outside it is a
# reason to stop rather than to guess.
LP64_ONLY = re.compile(r"LP64_ONLY\(\s*([^)]*)\s*\)")
NOT_LP64 = re.compile(r"NOT_LP64\(\s*([^)]*)\s*\)")
TERNARY = re.compile(r"^(?P<cond>.+?)\?(?P<yes>.+?):(?P<no>.+)$")
SAFE = re.compile(r"^[\w\s+\-*/()<>=!]*$")

# `BitsPerWord` comes from globalDefinitions.hpp and is 64 on every platform this
# project targets. It is stated here rather than parsed because it is a property of
# the build, not of markWord.hpp, and pretending otherwise would be a lie about
# where the number came from.
ENVIRONMENT = {"BitsPerWord": 64, "BitsPerByte": 8}

# The fields, least significant first, and the constant that gives each its width.
# The order is the file's own order and the names are the file's own names.
FIELDS = [
    ("lock", "lock_shift", "lock_bits", "the lock state, and whether a collector has marked or forwarded the object"),
    ("self_fwd", "self_fwd_shift", "self_fwd_bits", "set when a collector forwarded the object in place"),
    ("age", "age_shift", "age_bits", "how many collections the object has survived"),
    ("valhalla", "valhalla_reserved_shift", "valhalla_reserved_bits", "reserved for Valhalla, unused today"),
    ("hash", "hash_shift", "hash_bits", "the identity hash, written the first time anything asks for it"),
    ("klass", "klass_shift", "klass_bits", "the compressed class pointer, present only when UseCompactObjectHeaders is on"),
]

LOCK_STATES = [
    ("00", "locked", "a stack lock is held, and the real header is in the lock record"),
    ("01", "unlocked", "the ordinary state"),
    ("10", "monitor", "the lock is inflated"),
    ("11", "marked", "a collector is using the word, and the real header is elsewhere"),
]


def load_pin(root: pathlib.Path) -> dict:
    return json.loads((root / "docs" / "pin.json").read_text(encoding="utf-8"))


def fetch_header(tag: str) -> tuple[str, str]:
    """Return the header source and where it came from."""
    local = os.environ.get("JVX_JDK_SRC")
    if local:
        path = pathlib.Path(local) / HEADER_PATH
        if not path.is_file():
            raise SystemExit(f"JVX_JDK_SRC is set but {path} is not there")
        return path.read_text(encoding="utf-8"), str(path)
    url = RAW.format(tag=urllib.parse.quote(tag, safe=""), path=HEADER_PATH)
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        return response.read().decode("utf-8"), url


def evaluate(expr: str, known: dict[str, int]) -> int:
    """Work out one constant, given the ones below it.

    The file writes these as `age_shift = self_fwd_shift + self_fwd_bits` and once as
    a ternary, and wraps one of them in the LP64_ONLY macro. That is the whole
    language. Anything else raises, because a generator that guesses is worse than
    no generator: it produces a number that looks generated.
    """
    text = expr.strip()
    text = LP64_ONLY.sub(r"\1", text)
    text = NOT_LP64.sub("", text)
    text = text.strip()

    match = TERNARY.match(text)
    if match:
        cond = evaluate_arithmetic(match.group("cond"), known, boolean=True)
        branch = match.group("yes") if cond else match.group("no")
        return evaluate(branch, known)

    return evaluate_arithmetic(text, known)


def evaluate_arithmetic(text: str, known: dict[str, int], boolean: bool = False) -> int:
    text = text.strip()
    if not SAFE.match(text):
        raise ValueError(f"cannot read {text!r}, and guessing is not an option here")
    scope = dict(ENVIRONMENT)
    scope.update(known)
    for name in re.findall(r"[A-Za-z_]\w*", text):
        if name not in scope:
            raise ValueError(f"{text!r} uses {name!r}, which is not defined yet")
    value = eval(text, {"__builtins__": {}}, scope)  # noqa: S307
    return bool(value) if boolean else int(value)


def parse(source: str) -> tuple[dict[str, int], dict[str, int]]:
    """Return every constant we could work out, and the line each was defined on."""
    known: dict[str, int] = {}
    lines: dict[str, int] = {}
    for number, line in enumerate(source.split("\n"), start=1):
        match = CONSTANT.match(line)
        if not match:
            continue
        name = match.group("name")
        if name in known:
            continue
        try:
            known[name] = evaluate(match.group("expr"), known)
        except ValueError:
            # Masks and pointer-shaped constants use helpers this does not model.
            # They are not needed for the layout, so skipping them is honest.
            continue
        lines[name] = number
    return known, lines


def layout_comment_lines(source: str) -> dict[str, int]:
    """Find the two ASCII diagrams at the top of the file, so a lesson can cite them."""
    found = {}
    for number, line in enumerate(source.split("\n"), start=1):
        if "64 bits (with compact headers)" in line:
            found["compact"] = number
        elif "64 bits (without compact headers)" in line:
            found["legacy"] = number
    return found


def build(root: pathlib.Path) -> dict:
    pin = load_pin(root)
    tag = pin["jdk_tag"]
    source, origin = fetch_header(tag)
    known, lines = parse(source)

    missing = [c for _, s, b, _ in FIELDS for c in (s, b) if c not in known]
    if missing:
        raise SystemExit(
            f"markWord.hpp at {tag} does not define {sorted(set(missing))}. "
            f"The constants were renamed or restructured upstream, which is a real "
            f"finding rather than a bug here. Read the file before changing this."
        )

    fields = []
    for name, shift_const, bits_const, meaning in FIELDS:
        shift = known[shift_const]
        bits = known[bits_const]
        fields.append(
            {
                "name": name,
                "shift": shift,
                "bits": bits,
                "mask": f"0x{((1 << bits) - 1) << shift:016x}",
                "meaning": meaning,
                "defined_at": {
                    "shift": f"{HEADER_PATH}:{lines[shift_const]}@{tag}",
                    "bits": f"{HEADER_PATH}:{lines[bits_const]}@{tag}",
                },
            }
        )

    total = sum(f["bits"] for f in fields)
    diagrams = layout_comment_lines(source)

    return {
        "generated_by": "tools/gen_markword.py",
        "source": {
            "path": HEADER_PATH,
            "tag": tag,
            "origin": origin if origin.startswith("http") else "local checkout",
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "lines": len(source.split("\n")),
        },
        "word_bits": ENVIRONMENT["BitsPerWord"],
        "fields": fields,
        "total_field_bits": total,
        "lock_states": [
            {"bits": bits, "name": name, "meaning": meaning}
            for bits, name, meaning in LOCK_STATES
        ],
        "diagram_lines": {
            kind: f"{HEADER_PATH}:{line}@{tag}" for kind, line in diagrams.items()
        },
        "note": (
            "The klass field is present only when UseCompactObjectHeaders is on, which "
            "is the default on 64 bit platforms from JDK 27. With the flag off, those 22 "
            "bits are unused in the mark word and the class pointer is a separate 4 byte "
            "field after it. The C++ oopDesc struct declares that field either way, so "
            "reading the struct alone will tell you the object has one when it does not."
        ),
    }


def render(data: dict) -> str:
    rows = ["bits         field      width  meaning", "-" * 78]
    for field in reversed(data["fields"]):
        hi = field["shift"] + field["bits"] - 1
        span = f"{hi}..{field['shift']}"
        rows.append(f"{span:<12} {field['name']:<10} {field['bits']:>5}  {field['meaning']}")
    rows.append("")
    rows.append(f"{data['total_field_bits']} bits accounted for, in a {data['word_bits']} bit word")
    rows.append("")
    rows.append("lock  state      meaning")
    rows.append("-" * 78)
    for state in data["lock_states"]:
        rows.append(f"{state['bits']:<5} {state['name']:<10} {state['meaning']}")
    rows.append("")
    rows.append(f"from {data['source']['path']} at {data['source']['tag']}")
    rows.append(f"sha256 {data['source']['sha256']}")
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    parser.add_argument("--print", dest="show", action="store_true", help="print the layout")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    data = build(root)
    text = json.dumps(data, indent=2) + "\n"
    target = root / OUTPUT

    if args.show:
        print(render(data))
        return 0

    if args.check:
        if not target.is_file():
            print(f"{OUTPUT} is not committed, run tools/gen_markword.py", file=sys.stderr)
            return 1
        committed = target.read_text(encoding="utf-8")
        if committed != text:
            old = json.loads(committed)
            print(f"{OUTPUT} is stale.", file=sys.stderr)
            if old.get("source", {}).get("sha256") != data["source"]["sha256"]:
                print(
                    f"  markWord.hpp changed upstream at {data['source']['tag']}. "
                    f"This is a driftbot event, not a rebuild: read the diff before "
                    f"regenerating, because a field that moved breaks every diagram "
                    f"and every decode in the object layout lessons.",
                    file=sys.stderr,
                )
            else:
                print("  the file is the same, so the generator changed. Regenerate.", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is current at {data['source']['tag']}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} from {data['source']['path']} at {data['source']['tag']}")
    print()
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
