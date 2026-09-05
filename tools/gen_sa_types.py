#!/usr/bin/env python3
"""Turn the Serviceability Agent probe into one page: who gets in, and what they read.

`probes/sa/run.py` tries four ways into HotSpot's type database on one machine and writes
what happened to `probes/sa/results/<name>.json`. This puts every results file side by
side: a row per route, a column per environment, and then the struct layouts themselves,
which are the thing the routes were being tried for.

  python tools/gen_sa_types.py           regenerate the page
  python tools/gen_sa_types.py --check   regenerate in memory and fail on a diff

The layouts are printed once rather than once per machine, because every environment that
opened at all read the same numbers out of the same JDK build. If two of them ever stop
agreeing this refuses to write anything and says which field differs, because a merged
table that quietly picked a winner would be the worst possible output here.

The output goes to docs/generated/, which is marked linguist-generated, because it is
derived from the results files and editing it by hand would be editing a measurement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

RESULTS = pathlib.Path("probes/sa/results")
PAGE = pathlib.Path("docs/generated/sa-types.md")

# The order the routes are printed in, which is the order of increasing cooperation: what
# a person tries first, then what a tool that starts its own VM can do, then what needs
# the target to agree, then what needs no tracing at all. The control goes last.
ROUTES = [
    ("attach_running", "attach to a JVM that was already running"),
    ("attach_spawned", "start the JVM and attach to your own child"),
    ("attach_ptracer_any", "the target opts in with `prctl(PR_SET_PTRACER_ANY)`"),
    ("core_self", "the target dumps its own core, nothing attaches"),
    ("jhsdb_jstack", "`jhsdb jstack`, the control, which #33 also measured"),
]

# Smallest first, which is also the order they contain each other in: an oop holds a mark
# word and points at a Klass, and an InstanceKlass is a Klass. A type the probe asks for
# that is not in this list is printed after them rather than dropped.
ORDER = ["oopDesc", "markWord", "Klass", "InstanceKlass"]


def load() -> dict[str, dict]:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        sys.exit(f"no results in {RESULTS}, run probes/sa/run.py first")
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}


def account(data: dict) -> str:
    if data.get("root") is True:
        return "root"
    if data.get("root") is False:
        return "an ordinary user"
    return "unknown"


def cell(text: str) -> str:
    """One table cell. A core_pattern that pipes to a crash handler starts with a pipe,
    and an unescaped pipe in a markdown table silently becomes a column boundary."""
    return text.replace("|", "\\|")


def yes_no(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "not asked"


def fields(type_: dict) -> list[tuple]:
    """One type's fields as comparable tuples, in offset order, statics last.

    A static field has an address rather than an offset, and the probe records that as
    null rather than as a number, so sorting has to keep those away from the integers.
    """
    return sorted(
        ((f["offset"], f["name"], f["type"], f["static"]) for f in type_["fields"]),
        key=lambda f: (f[0] is None, f[0], f[1]),
    )


def agreed(results: dict[str, dict]) -> tuple[dict, list[str]]:
    """The types, and the environments that read them, or an explanation of a difference."""
    read = {name: data["types"] for name, data in results.items() if data.get("types")}
    if not read:
        sys.exit(
            "no environment in the results read the type database. That is a finding "
            "rather than a bug here, and it belongs in the report before it belongs in "
            "a generated table."
        )
    names = list(read)
    first = read[names[0]]
    for other in names[1:]:
        difference = describe(first, read[other])
        if difference:
            sys.exit(
                f"{names[0]} and {other} disagree about the type database: {difference}. "
                f"Two machines reading the same JDK build should not, so this is the "
                f"interesting thing rather than something to regenerate past. Read both "
                f"results files before touching this generator."
            )
    return first, names


def describe(left: dict, right: dict) -> str:
    """The first real difference between two type dumps, in words, or an empty string."""
    if set(left) != set(right):
        return f"one has {sorted(set(left) ^ set(right))} and the other does not"
    for name in left:
        one, other = left[name], right[name]
        if one is None or other is None:
            if one != other:
                return f"{name} is missing from one of them"
            continue
        if one["size"] != other["size"]:
            return f"{name} is {one['size']} bytes in one and {other['size']} in the other"
        if one["super"] != other["super"]:
            return f"{name} extends {one['super']} in one and {other['super']} in the other"
        if fields(one) != fields(other):
            gone = [f for f in fields(one) if f not in fields(other)]
            extra = [f for f in fields(other) if f not in fields(one)]
            return f"{name} has {gone[:3]} in one and {extra[:3]} in the other"
    return ""


def build(results: dict[str, dict]) -> str:
    names = list(results)
    types, read_by = agreed(results)
    builds = sorted({d["java_build"] for d in results.values()})

    out: list[str] = []
    out.append("# HotSpot's own struct layouts, and who is allowed to read them")
    out.append("")
    out.append(
        "Generated by `tools/gen_sa_types.py` from the files in `probes/sa/results/`. "
        "Do not edit it, edit the measurement. The report that explains it is "
        "[docs/probes/sa-types.md](../probes/sa-types.md)."
    )
    out.append("")
    out.append(
        f"{len(ROUTES)} ways into the Serviceability Agent's type database, tried on "
        f"{len(names)} environments, on java {' and '.join(builds)}. "
        f"{len(read_by)} of the {len(names)} environments got at the database at all, "
        f"and the ones that did read the same numbers."
    )
    out.append("")

    out.append("## Where it ran")
    out.append("")
    out.append("| name | platform | account | `ptrace_scope` | `core_pattern` | measured |")
    out.append("|---|---|---|---|---|---|")
    for name in names:
        data = results[name]
        scope = data.get("ptrace_scope")
        pattern = data.get("core_pattern") or "unknown"
        out.append(
            f"| `{name}` | {data['platform']} | {account(data)} | "
            f"{'`' + scope + '`' if scope else 'no yama'} | `{cell(pattern)}` | "
            f"{data['measured'][:10]} |"
        )
    out.append("")

    out.append("## Who gets in")
    out.append("")
    header = "| route | " + " | ".join(f"`{n}`" for n in names) + " |"
    out.append(header)
    out.append("|---|" + "---|" * len(names))
    for route, blurb in ROUTES:
        cells = " | ".join(
            yes_no(results[n]["routes"].get(route, {}).get("worked")) for n in names
        )
        out.append(f"| {blurb} | {cells} |")
    out.append("")
    out.append("Then the two questions that matter more than any single yes.")
    out.append("")
    out.append(header)
    out.append("|---|" + "---|" * len(names))
    out.append(
        "| every route that opened read the same thing | "
        + " | ".join(yes_no(results[n].get("routes_agree")) for n in names) + " |"
    )
    out.append(
        "| and it is what `jhsdb clhsdb` prints by hand | "
        + " | ".join(yes_no(results[n].get("clhsdb_agrees")) for n in names) + " |"
    )
    out.append("")

    refused = [
        (name, route, results[name]["routes"][route].get("error", ""))
        for name in names
        for route, _ in ROUTES
        if results[name]["routes"].get(route, {}).get("worked") is False
    ]
    if refused:
        out.append("## Why the noes")
        out.append("")
        out.append(
            "Every route that was refused, in the words it was refused with. A pid in a "
            "message is the pid of a target the probe started and then killed."
        )
        out.append("")
        for name, route, error in refused:
            out.append(f"- `{route}` on `{name}`: {error or 'no reason given'}")
        out.append("")

    out.append("## What they read")
    out.append("")
    out.append(
        "The resolved layout of each type, as the VM itself believes it, from "
        + " and ".join(f"`{n}`" for n in read_by)
        + ". Offsets are bytes from the start of the struct. A type the build does not "
        "have would appear here as missing rather than be left out."
    )
    out.append("")
    for name in ORDER + [n for n in types if n not in ORDER]:
        if name not in types:
            continue
        type_ = types[name]
        if type_ is None:
            out.append(f"### {name}")
            out.append("")
            out.append("Not in the type database of this build.")
            out.append("")
            continue
        extends = f", extends `{type_['super']}`" if type_["super"] else ", no superclass"
        rows = fields(type_)
        out.append(f"### {name}")
        out.append("")
        out.append(
            f"`{name}` is {type_['size']} bytes{extends}, and the type database exports "
            f"{len(rows) if rows else 'none'} of its fields."
        )
        out.append("")
        if not rows:
            out.append(
                "The type is there and it has a size, and not one field of it is "
                "exported, so nothing here can tell you what is inside it."
            )
            out.append("")
            continue
        out.append("| offset | field | type |")
        out.append("|---|---|---|")
        for offset, field, kind, static in rows:
            where = "static" if static else str(offset)
            out.append(f"| {where} | `{field}` | `{kind}` |")
        out.append("")

    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = ap.parse_args(argv)

    wanted = build(load())
    if args.check:
        if not PAGE.is_file():
            print(f"{PAGE} is missing, run tools/gen_sa_types.py", file=sys.stderr)
            return 1
        if PAGE.read_text(encoding="utf-8") != wanted:
            print(f"{PAGE} does not match {RESULTS}, run tools/gen_sa_types.py",
                  file=sys.stderr)
            return 1
        print(f"the type database page matches {RESULTS}")
        return 0

    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(wanted, encoding="utf-8")
    print(f"wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
