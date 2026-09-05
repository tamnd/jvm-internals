#!/usr/bin/env python3
"""Build a section index for the pinned specification edition.

`CONTRIBUTING.md` rule 7 says a claim marked `[JVMS]` whose section does not say what the
claim says is a P1 bug, treated more seriously than a wrong number, because it is the one
error that will cause somebody to write production code on a guarantee that does not
exist. Catching that by machine takes two things. This is the first: knowing which
sections exist and what each one is called.

  python tools/gen_jvms_index.py            write docs/generated/jvms-index.json
  python tools/gen_jvms_index.py --check    fail if the committed index is stale
  python tools/gen_jvms_index.py --print    the chapter list and the section counts

What gets committed is section numbers, section titles, and a hash of each chapter page.
Not the text. The specification is Oracle's, this repository is not a mirror of it, and a
number and a title are the facts a citation needs checking against. The hash is there so
that a rewritten chapter is visible as an event rather than discovered later as a
citation that silently means something else, which is the same reason `gen_markword.py`
hashes the header it reads.

The title is the part that does the work. A section number on its own is a weak check,
because `§2.7` exists in every edition ever published and will still exist in editions
nobody has written yet. A number with the title it had when the claim was made is a
check that catches a renumbering, which is the way specification citations actually rot.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = pathlib.Path("docs/generated/jvms-index.json")
PIN = ROOT / "docs" / "pin.json"

PAGE = "https://docs.oracle.com/javase/specs/jvms/{edition}/html/jvms-{chapter}.html"

# JVMS is seven chapters. There are appendices, and nothing in this repository cites one,
# so they are left out rather than fetched and never read. Add one here when a citation
# needs it, and the index grows by exactly the page that was needed.
CHAPTERS = ["1", "2", "3", "4", "5", "6", "7"]

# `<a name="jvms-2.1"></a>2.1.&nbsp;The <code class="literal">class</code> File Format`
# closed by the heading tag it sits in. Capturing to the close tag rather than to the
# next `<` is what keeps the markup inside a title, and the titles with markup in them
# are the interesting ones: three of the sections this repository might cite have a
# `<code>` element in the middle of the name.
HEADING = re.compile(
    r'<a name="(jvms-\d[\d.]*)"></a>(.*?)</h\d>', re.DOTALL)
TAG = re.compile(r"<[^>]+>")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def title(raw: str) -> str:
    """One heading, with its markup removed and its whitespace collapsed."""
    text = html.unescape(TAG.sub("", raw))
    return " ".join(text.split())


def numbered(name: str, text: str) -> tuple[str, str] | None:
    """A section number and its title, or nothing if this heading is not a section.

    A chapter heading reads `Chapter 2. The Structure of the Java Virtual Machine` and a
    section heading reads `2.1. The class File Format`. Only the second is a thing a
    citation points at, and telling them apart on the anchor rather than on the text is
    what keeps this from depending on the word "Chapter" staying in English.
    """
    number = name.removeprefix("jvms-")
    if "." not in number:
        return None
    prefix = f"{number}."
    if not text.startswith(prefix):
        return None
    return number, text[len(prefix):].strip()


def chapter(edition: str, which: str) -> dict:
    url = PAGE.format(edition=edition.lower(), chapter=which)
    body = fetch(url)
    text = body.decode("utf-8", errors="replace")
    sections: dict[str, str] = {}
    name = ""
    for match in HEADING.finditer(text):
        anchor, raw = match.group(1), match.group(2)
        heading = title(raw)
        found = numbered(anchor, heading)
        if found is None:
            if anchor == f"jvms-{which}":
                # `Chapter 2. The Structure of the Java Virtual Machine`. The number is
                # already the key, so keep the part that is not a repeat of it.
                name = heading.split(".", 1)[-1].strip()
            continue
        sections[found[0]] = found[1]
    # Chapter 7 is a single opcode table with no subsections, so an empty section list is
    # a real answer. A page with no chapter heading either is not, because that is what a
    # redesigned template or a moved URL looks like, and it must not pass as an index
    # that happens to be empty.
    if not name:
        raise SystemExit(f"{url} produced no chapter heading, so the page shape changed")
    return {
        "chapter": which,
        "title": name,
        "url": url,
        "sha256": hashlib.sha256(body).hexdigest(),
        "sections": dict(sorted(sections.items(), key=order)),
    }


def order(item: tuple[str, str]) -> list[int]:
    """Sort 2.10 after 2.9 rather than after 2.1, which is what a reader expects."""
    return [int(part) for part in item[0].split(".")]


def build(edition: str) -> dict:
    return {
        "generated_by": "tools/gen_jvms_index.py",
        "edition": edition,
        "note": (
            "Section numbers and titles only. The text of the specification is Oracle's "
            "and is fetched when a citation is checked rather than copied here."
        ),
        "chapters": [chapter(edition, which) for which in CHAPTERS],
    }


def sections(index: dict) -> dict[str, str]:
    """Every section in the index, flattened, which is how a checker wants it."""
    found: dict[str, str] = {}
    for one in index["chapters"]:
        found.update(one["sections"])
    return found


def load() -> dict:
    path = ROOT / OUTPUT
    if not path.is_file():
        raise SystemExit(f"{OUTPUT} is not committed, run tools/gen_jvms_index.py")
    return json.loads(path.read_text(encoding="utf-8"))


def render(index: dict) -> str:
    lines = [f"JVMS {index['edition']}"]
    for one in index["chapters"]:
        lines.append(f"  {one['chapter']}. {one['title']}: "
                     f"{len(one['sections'])} sections, page {one['sha256'][:12]}")
    lines.append(f"  {len(sections(index))} sections in total")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed index is stale")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the chapter list without writing anything")
    args = ap.parse_args(argv)

    edition = json.loads(PIN.read_text(encoding="utf-8"))["jvms_edition"]
    index = build(edition)
    text = json.dumps(index, indent=2) + "\n"
    target = ROOT / OUTPUT

    if args.show:
        print(render(index))
        return 0

    if args.check:
        if not target.is_file():
            print(f"{OUTPUT} is not committed, run tools/gen_jvms_index.py",
                  file=sys.stderr)
            return 1
        committed = target.read_text(encoding="utf-8")
        if committed == text:
            print(f"{OUTPUT} is current at {edition}, "
                  f"{len(sections(index))} sections")
            return 0
        print(f"{OUTPUT} is stale.", file=sys.stderr)
        was = {one["chapter"]: one for one in json.loads(committed)["chapters"]}
        for one in index["chapters"]:
            before = was.get(one["chapter"])
            if before and before["sha256"] != one["sha256"]:
                print(f"  chapter {one['chapter']} was republished at {edition}. Read "
                      f"the diff before regenerating: a section that was renumbered or "
                      f"renamed changes what every claim citing it says.", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(render(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
