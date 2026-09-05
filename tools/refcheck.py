#!/usr/bin/env python3
"""Resolve every citation in the repository, source against the pinned tree and
specification against the pinned edition's section index.

`CONTRIBUTING.md` promises this: "A source citation is resolved against the pinned tree
and the surrounding lines are hashed, so a citation that still points at a line whose
content changed is caught. That is the failure mode that matters, because a line number
that drifts is obvious and a line number that still exists and now says something
different is not." This is the thing that keeps that promise.

  python tools/refcheck.py            resolve every citation against the ledger
  python tools/refcheck.py --update   rewrite the ledger from the pinned tree
  python tools/refcheck.py --offline  use only what is already cached

The ledger is `docs/citations.json`. It records, for each cited line, the tag it was
resolved at, the text of the line, and a hash of the line with two lines of context
either side. The ledger is keyed on `path:line` without the tag, on purpose. A tag is
immutable, so checking a citation against the tag it was written for can never fail and
would be a check that cannot catch anything. Keying without the tag means that when the
pin moves, every citation is re-resolved against the new tree and the ones whose content
changed come out as a list. That list is the cost of the version bump, measured rather
than estimated, which is what M6 (issue #19) exists to find out.

Source files are fetched from raw.githubusercontent.com at the pinned tag and cached
under `~/.cache/jvx/src/<tag>/`. A tag never moves, so the cache never needs to expire.
Set `JVX_JDK_SRC` to a checkout to use that instead of the network, which is what a
machine that has already cloned the JDK should do.

A specification citation is resolved against `docs/generated/jvms-index.json`, which
`gen_jvms_index.py` builds from Oracle's published edition. The section has to exist in
the edition the pin names, and `--report` prints the title the section actually has next
to every citation, because a reviewer who sees `JVMS 2.7 Representation of Objects`
beside a claim about object headers can tell when the pairing is wrong and a reviewer who
sees `JVMS 2.7` alone cannot.

What that does not check is whether the section says what the claim says. That is the P1
bug in `CONTRIBUTING.md` rule 7 and it still needs a human, or a convention for quoting a
fragment that this repository does not have yet. The existence check catches the
renumbering, which is how specification citations rot in practice; it does not catch the
misattribution, which is how they mislead.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_jvms_index  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = pathlib.Path("docs/citations.json")
RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
CACHE = pathlib.Path(os.environ.get("JVX_CACHE",
                                    pathlib.Path.home() / ".cache" / "jvx")) / "src"

# The same two forms prosecheck matches, because a citation the two tools disagree about
# is a citation one of them is not checking.
SOURCE_CITE = re.compile(
    r"([A-Za-z0-9_/.$-]+\.(?:cpp|hpp|c|h|java|ad|xml|md)):(\d+)@(\S+?)(?=[\s)\]}.,;`\"']|$)")
SPEC_CITE = re.compile(
    r"(JVMS|JLS)\s*§?\s*(\d[\d.]*?)@(\S+?)(?=[\s)\]}.,;`\"']|$)")

# Where citations live. Prose, lesson markdown and jvx comments are what prosecheck
# reads. The two JSON shapes are here as well because a claim ledger and a generated
# layout both carry citations, and a citation nobody resolves is the whole problem.
PROSE = ("*.md",)
JSON_WITH_CITATIONS = ("lessons/*/claims.json", "docs/generated/*.json")

# How much of the file around the cited line goes into the hash. Two either side is
# enough to catch an edit that moved the meaning without moving the line number, and
# small enough that an unrelated edit six lines away does not cry wolf.
CONTEXT = 2

# A line ending in this is a citation being described rather than made. The format has to
# be documented somewhere, and the document that describes it cannot be the one file the
# checker is not allowed to read.
ALLOW = "<!-- refcheck-ok -->"


def load_pin() -> dict:
    return json.loads((ROOT / "docs" / "pin.json").read_text(encoding="utf-8"))


def sources() -> list[pathlib.Path]:
    """Every file a citation can live in, as a repository relative path."""
    found: list[pathlib.Path] = []
    for path in sorted(ROOT.rglob("*.md")):
        if ".git" not in path.parts:
            found.append(path)
    for pattern in ("lessons/*/lesson.py", "jvx/*.jsh"):
        found.extend(sorted(ROOT.glob(pattern)))
    for pattern in JSON_WITH_CITATIONS:
        found.extend(sorted(ROOT.glob(pattern)))
    return found


def cited(paths: list[pathlib.Path]) -> tuple[dict[str, dict], list[dict]]:
    """Every source citation, and every specification citation, with where each was made.

    Keyed on `path:line`, with the tags collected as a set rather than folded into the
    key. Two citations of one line at two different tags is a thing worth seeing, and a
    key that included the tag would hide it by making them two unrelated entries.

    JSON files are read as text rather than parsed, because a citation is a citation
    whatever key it sits under and a parser that only looked at the keys it knew about
    would quietly stop checking the day somebody added a field.
    """
    source: dict[str, dict] = {}
    spec: list[dict] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if ALLOW in line:
                continue
            where = f"{path.relative_to(ROOT)}:{number}"
            for found in SOURCE_CITE.finditer(line):
                key = f"{found.group(1)}:{found.group(2)}"
                entry = source.setdefault(
                    key, {"cited_at": [], "cited_in": [], "tags": []})
                if where not in entry["cited_at"]:
                    entry["cited_at"].append(where)
                # The ledger records the file and not the line, so that adding a
                # paragraph above a citation does not dirty an entry about a line in
                # somebody else's repository. The line is kept for the error message,
                # where it is what somebody needs to go and look at the thing.
                name = str(path.relative_to(ROOT))
                if name not in entry["cited_in"]:
                    entry["cited_in"].append(name)
                if found.group(3) not in entry["tags"]:
                    entry["tags"].append(found.group(3))
            for found in SPEC_CITE.finditer(line):
                spec.append({"spec": found.group(1), "section": found.group(2),
                             "edition": found.group(3), "where": where,
                             "text": found.group(0)})
    return source, spec


def fetch(path: str, tag: str, offline: bool) -> list[str] | None:
    """One file from the pinned tree, cached, as a list of lines without their endings."""
    local = os.environ.get("JVX_JDK_SRC")
    if local:
        on_disk = pathlib.Path(local) / path
        if not on_disk.is_file():
            return None
        return on_disk.read_text(encoding="utf-8", errors="replace").splitlines()

    cached = CACHE / tag / path
    if not cached.is_file():
        if offline:
            return None
        url = RAW.format(tag=urllib.parse.quote(tag, safe=""), path=path)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
                body = response.read()
        except OSError as failure:
            print(f"could not fetch {url}: {failure}", file=sys.stderr)
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(body)
    return cached.read_text(encoding="utf-8", errors="replace").splitlines()


def resolve(key: str, tag: str, offline: bool) -> dict:
    """What the pinned tree says at one cited line."""
    path, _, number = key.rpartition(":")
    line = int(number)
    body = fetch(path, tag, offline)
    if body is None:
        return {"resolved": False, "why": f"could not read {path} at {tag}"}
    if line < 1 or line > len(body):
        return {"resolved": False,
                "why": f"{path} at {tag} has {len(body)} lines and the citation is to {line}"}
    window = body[max(0, line - 1 - CONTEXT):line + CONTEXT]
    digest = hashlib.sha256(
        "\n".join(text.rstrip() for text in window).encode("utf-8")).hexdigest()
    return {"resolved": True, "tag": tag, "line": body[line - 1].rstrip(),
            "context_sha256": digest, "file_lines": len(body)}


def ledger() -> dict:
    path = ROOT / LEDGER
    if not path.is_file():
        return {"citations": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def build(source: dict[str, dict], accepted: list[str],
          offline: bool) -> tuple[dict, list[str]]:
    """Resolve every citation and return the new ledger, plus what could not be resolved.

    Each citation is resolved against the tag it names rather than against the pin, so
    that a citation written at the GA tag is checked against the GA tree. The tag still
    has to be one the pin accepts, which is what stops a citation naming a tag that says
    whatever the author wished it said.
    """
    entries: dict[str, dict] = {}
    unresolved: list[str] = []
    for key in sorted(source):
        tags = source[key]["tags"]
        where = ", ".join(source[key]["cited_at"])
        if len(tags) > 1:
            unresolved.append(
                f"{key} is cited at more than one tag ({', '.join(tags)}), at {where}. "
                f"One line cannot be two lines. Pick the tag the prose is written for.")
            continue
        tag = tags[0]
        if tag not in accepted:
            unresolved.append(
                f"{key} names the tag {tag}, which docs/pin.json does not accept "
                f"({', '.join(accepted)}), at {where}")
            continue
        found = resolve(key, tag, offline)
        if not found["resolved"]:
            unresolved.append(f"{key}: {found['why']}, cited at {where}")
            continue
        entries[key] = {
            "tag": found["tag"],
            "line": found["line"],
            "context_sha256": found["context_sha256"],
            "cited_in": sorted(source[key]["cited_in"]),
        }
    return entries, unresolved


def compare(known: dict, entries: dict, source: dict[str, dict]) -> list[str]:
    """Everything that changed between the ledger and the tree, in words."""
    problems: list[str] = []
    for key in sorted(entries):
        was = known.get(key)
        now = entries[key]
        where = ", ".join(source[key]["cited_at"])
        if was is None:
            problems.append(
                f"{key} is cited at {where} and is not in the ledger. "
                f"Run tools/refcheck.py --update and read the diff before committing it.")
            continue
        if was["context_sha256"] != now["context_sha256"]:
            problems.append(
                f"{key} resolved at {now['tag']} but its content changed. The ledger has "
                f"{was['line']!r} at {was['tag']} and the tree has {now['line']!r}. "
                f"Cited at {where}. Read the new tree and move the citation or fix the "
                f"prose, then run --update.")
            continue
        if was["cited_in"] != now["cited_in"]:
            problems.append(
                f"{key} is cited in different files than the ledger records: "
                f"{was['cited_in']} became {now['cited_in']}. Run --update.")
    for key in sorted(set(known) - set(entries)):
        problems.append(
            f"{key} is in the ledger and is cited nowhere. Run --update to drop it.")
    return problems


def specifications(spec: list[dict], pin: dict) -> tuple[list[dict], list[str]]:
    """Every specification citation, resolved against the committed section index.

    What this can check is that the section exists in the edition the citation names and
    what that section is called. What it cannot check is whether the section says what
    the claim says, which is the P1 bug in rule 7 and needs a human or a quoted fragment.
    So every resolved citation is returned with its title, for the report to print, on
    the theory that a reviewer who sees `JVMS 2.7 Representation of Objects` next to a
    claim about object headers can tell at a glance when the pairing is wrong, and a
    reviewer who sees `JVMS 2.7` alone cannot.
    """
    resolved: list[dict] = []
    problems: list[str] = []
    edition = pin["jvms_edition"]
    index = None
    for citation in spec:
        if citation["spec"] != "JVMS":
            resolved.append(dict(citation, title=None,
                                 why=f"no index exists for {citation['spec']}"))
            continue
        if citation["edition"] != edition:
            problems.append(
                f"{citation['text']} at {citation['where']} names edition "
                f"{citation['edition']} and docs/pin.json says {edition}")
            continue
        if index is None:
            index = gen_jvms_index.sections(gen_jvms_index.load())
        title = index.get(citation["section"])
        if title is None:
            problems.append(
                f"{citation['text']} at {citation['where']} names section "
                f"{citation['section']}, which is not in JVMS {edition}")
            continue
        resolved.append(dict(citation, title=title, why=None))
    return resolved, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true", help="rewrite the ledger")
    ap.add_argument("--offline", action="store_true",
                    help="use the cache and a local checkout only, fetch nothing")
    ap.add_argument("--report", action="store_true",
                    help="print what was checked and what was not")
    args = ap.parse_args(argv)

    pin = load_pin()
    accepted = [pin[key] for key in ("jdk_tag", "jdk_ga_tag") if pin.get(key)]
    files = sources()
    source, spec = cited(files)
    if not source:
        print("no source citations found, which cannot be right", file=sys.stderr)
        return 1

    entries, unresolved = build(source, accepted, args.offline)

    if args.update:
        written = {
            "generated_by": "tools/refcheck.py --update",
            "updated": datetime.datetime.now(datetime.UTC).date().isoformat(),
            "accepted_tags": accepted,
            "context_lines": CONTEXT,
            "note": (
                "Keyed on path:line without the tag, so that moving the pin re-resolves "
                "every citation against the new tree and the ones whose content changed "
                "come out as a list. That list is the cost of the version bump."
            ),
            "citations": entries,
        }
        (ROOT / LEDGER).write_text(
            json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {LEDGER}: {len(entries)} citations")
        if unresolved:
            for line in unresolved:
                print(f"unresolved: {line}", file=sys.stderr)
            return 1
        return 0

    problems = compare(ledger().get("citations", {}), entries, source)
    for line in unresolved:
        problems.append(f"unresolved: {line}")

    resolved, wrong = specifications(spec, pin)
    problems.extend(wrong)
    unindexed = [one for one in resolved if one["title"] is None]

    if args.report or problems:
        print(f"{len(source)} source citations in {len(files)} files, resolved against "
              f"{' and '.join(accepted)}")
        print(f"{len(spec)} specification citations, resolved against "
              f"{gen_jvms_index.OUTPUT}")
        for one in sorted(resolved, key=lambda c: c["where"]):
            title = one["title"] or f"unchecked, {one['why']}"
            print(f"  {one['spec']} {one['section']:10s} {title}  at {one['where']}")

    for line in problems:
        print(f"refcheck: {line}", file=sys.stderr)
    if problems:
        return 1
    print(f"refcheck: {len(entries)} source citations resolve and none has drifted, "
          f"{len(resolved) - len(unindexed)} specification sections exist in "
          f"{pin['jvms_edition']}, {len(unindexed)} unchecked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
