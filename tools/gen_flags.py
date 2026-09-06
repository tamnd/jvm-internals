#!/usr/bin/env python3
"""Generate BP-FLAGS from HotSpot's globals headers and from a VM that was asked.

Issue #15. The gate says BP-FLAGS is generated from `globals.hpp` with types, defaults
and origins. Two of those three are in the header and the third is not, which is the
whole reason this joins two sources instead of parsing one.

The headers declare a flag with a macro that carries its type, its default, whether it
is diagnostic or experimental or manageable, its documentation string, and sometimes a
range and a constraint. That is a complete description of what the source says. It is
not a description of the VM you have. A `develop` flag is not in a product build at all.
A collector that was not compiled in takes its flags with it while leaving the switch
that turns it on behind. A `product_pd` flag has no default in the shared header because
the default is a `define_pd_global` in a platform header, and the two platforms measured
here disagree about four of them.

And the origin, which is the only thing that tells you whether a value is the header's
default or something the VM decided, is available nowhere except a running VM, where it
turns out to be less honest than it looks.

  python tools/gen_flags.py            regenerate docs/generated/flags.md
  python tools/gen_flags.py --check    regenerate in memory and fail on a difference

The headers come from raw.githubusercontent.com at the tag in `docs/pin.json`, or from a
local checkout when `JVX_JDK_SRC` points at one. The measurements come from
`probes/vmflags/results/*.json`, which are committed, so `--check` needs the network for
the headers and nothing else.

What stops this generator is a disagreement rather than a surprise. A flag the VM reports
that no header declares, a type or an attribute the two sources spell differently, a
declared range the VM does not enforce, a flag that is declared and absent for no reason
this can name, two platforms that disagree about what a flag is: each of those means one
of the two sources is being read wrong, and a page that averages over that is worse than
no page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

RAW = "https://raw.githubusercontent.com/openjdk/jdk/{tag}/{path}"
OUTPUT = pathlib.Path("docs/generated/flags.md")
RESULTS = pathlib.Path("probes/vmflags/results")

# Every header that declares a flag for the platforms this project measures, with the
# operating system it belongs to. A header with no operating system is compiled into
# every build. The cpu headers are the aarch64 ones because both measured platforms are
# aarch64, and a result from a machine this list cannot serve stops the run rather than
# being joined against the wrong header.
HEADERS = [
    ("src/hotspot/share/runtime/globals.hpp", None),
    ("src/hotspot/share/runtime/flags/debug_globals.hpp", None),
    ("src/hotspot/share/compiler/compiler_globals.hpp", None),
    ("src/hotspot/share/compiler/compiler_globals_pd.hpp", None),
    ("src/hotspot/share/c1/c1_globals.hpp", None),
    ("src/hotspot/share/opto/c2_globals.hpp", None),
    ("src/hotspot/share/cds/cds_globals.hpp", None),
    ("src/hotspot/share/gc/shared/gc_globals.hpp", None),
    ("src/hotspot/share/gc/shared/tlab_globals.hpp", None),
    ("src/hotspot/share/gc/epsilon/epsilon_globals.hpp", None),
    ("src/hotspot/share/gc/g1/g1_globals.hpp", None),
    ("src/hotspot/share/gc/parallel/parallel_globals.hpp", None),
    ("src/hotspot/share/gc/serial/serial_globals.hpp", None),
    ("src/hotspot/share/gc/shenandoah/shenandoah_globals.hpp", None),
    ("src/hotspot/share/gc/z/z_globals.hpp", None),
    ("src/hotspot/cpu/aarch64/globals_aarch64.hpp", None),
    ("src/hotspot/cpu/aarch64/c1_globals_aarch64.hpp", None),
    ("src/hotspot/cpu/aarch64/c2_globals_aarch64.hpp", None),
    ("src/hotspot/os/bsd/globals_bsd.hpp", "darwin"),
    ("src/hotspot/os_cpu/bsd_aarch64/globals_bsd_aarch64.hpp", "darwin"),
    ("src/hotspot/os/linux/globals_linux.hpp", "linux"),
    ("src/hotspot/os_cpu/linux_aarch64/globals_linux_aarch64.hpp", "linux"),
]

MACROS = {
    "product": "in every build, and settable on the command line",
    "product_pd": "in every build, with a default that each platform supplies",
    "develop": "in a debug build only, and a compile time constant otherwise",
    "develop_pd": "in a debug build only, with a per platform default",
    "notproduct": "in a debug build only, and absent rather than constant otherwise",
}

# The three attribute words a declaration can carry, and the word the VM prints for each.
ATTRIBUTES = {"DIAGNOSTIC": "diagnostic", "EXPERIMENTAL": "experimental",
              "MANAGEABLE": "manageable"}

# Every word the VM is allowed to print inside the first pair of braces. A word outside
# this set means the kind vocabulary changed and the counts below are answering a
# question that no longer exists.
KINDS = {"product", "diagnostic", "experimental", "manageable", "pd", "C1", "C2",
         "ARCH", "lp64_product"}

# The constants a default or a range is written in terms of. These are the widths of the
# C++ types, which are the same on every platform this project targets, and `K`, `M` and
# `G` as HotSpot spells them.
CONSTANTS = {
    "K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024,
    "max_jint": 2 ** 31 - 1, "min_jint": -(2 ** 31),
    "max_juint": 2 ** 32 - 1, "max_uint": 2 ** 32 - 1,
    "max_jlong": 2 ** 63 - 1, "min_jlong": -(2 ** 63),
    "max_intx": 2 ** 63 - 1, "min_intx": -(2 ** 63),
    "max_uintx": 2 ** 64 - 1, "max_uint64_t": 2 ** 64 - 1,
    "max_size_t": 2 ** 64 - 1, "max_jushort": 65535,
}

START = re.compile(r"\b(develop_pd|product_pd|develop|product|notproduct)\s*\(")
PD_GLOBAL = re.compile(r"\bdefine_pd_global\(\s*([\w:*]+)\s*,\s*(\w+)\s*,\s*([^)]*)\)")
SAFE = re.compile(r"^[\w\s+\-*/().]+$")
IF = re.compile(r"#\s*if(n?def)?\b")
ELIF = re.compile(r"#\s*elif\b")
ELSE = re.compile(r"#\s*else\b")
ENDIF = re.compile(r"#\s*endif\b")


def load_pin(root: pathlib.Path) -> dict:
    return json.loads((root / "docs" / "pin.json").read_text(encoding="utf-8"))


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def source(tag: str, path: str) -> tuple[str, str]:
    """One header, from a local checkout when there is one and the network otherwise."""
    local = os.environ.get("JVX_JDK_SRC")
    if local:
        found = pathlib.Path(local) / path
        if not found.is_file():
            sys.exit(f"JVX_JDK_SRC is set but {found} is not there")
        return found.read_text(encoding="utf-8"), "local checkout"
    url = RAW.format(tag=urllib.parse.quote(tag, safe=""), path=path)
    return fetch(url).decode("utf-8"), url


def continued(text: str) -> str:
    """One macro argument with the backslash continuations folded out of it."""
    return re.sub(r"\s*\\\s*\n\s*", " ", text).strip()


def arguments(text: str, opening: int) -> tuple[list[str] | None, int]:
    """Split one parenthesised argument list, respecting nesting and string literals.

    A regular expression cannot do this. `product(ccstr, NativeMemoryTracking,
    DEBUG_ONLY("summary") NOT_DEBUG("off"), "doc")` has parentheses inside an argument
    and a comma inside a string, and both of those appear in these headers.
    """
    depth = 0
    index = opening
    parts: list[str] = []
    current: list[str] = []
    while index < len(text):
        char = text[index]
        if char == '"':
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    break
                end += 1
            current.append(text[index:end + 1])
            index = end + 1
            continue
        if char == "(":
            depth += 1
            if depth == 1:
                index += 1
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                parts.append("".join(current))
                return parts, index + 1
        elif char == "," and depth == 1:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    return None, index


def guards(text: str) -> dict[int, tuple[str, ...]]:
    """For every line, the preprocessor conditions it sits inside.

    This is not a preprocessor. It does not evaluate anything, and it does not need to:
    the only question asked of it later is whether a declaration that the VM does not
    report was written inside a condition, and if so which one, so that the page can
    print the condition rather than a guess about the build.

    The one condition that is dropped is the file's own include guard. Every declaration
    in every one of these headers is inside it, so reporting it as the reason a flag is
    absent would put the same meaningless sentence against a quarter of them.
    """
    stack: list[str] = []
    found: dict[int, tuple[str, ...]] = {}
    include_guard = None
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if IF.match(stripped):
            once = re.fullmatch(r"#\s*ifndef\s+(\w+_HPP)", stripped)
            if once and include_guard is None and not stack:
                include_guard = stripped
            stack.append(stripped)
        elif ELIF.match(stripped) and stack:
            stack[-1] = stripped
        elif ELSE.match(stripped) and stack:
            stack[-1] = f"#else of {stack[-1]}"
        elif ENDIF.match(stripped) and stack:
            stack.pop()
        found[number] = tuple(item for item in stack if item != include_guard)
    return found


def declarations(path: str, text: str) -> list[dict]:
    """Every flag declaration in one header, with everything the macro says about it."""
    inside = guards(text)
    found = []
    for match in START.finditer(text):
        macro = match.group(1)
        args, end = arguments(text, match.end() - 1)
        if args is None:
            continue
        args = [continued(arg) for arg in args]
        # The macro name also appears in the parameter list of the `#define` that takes
        # it, as `product,` with no argument list of the right shape. A declaration is
        # two arguments that look like a type and an identifier, and nothing else does.
        if len(args) < 2:
            continue
        if not re.fullmatch(r"[A-Za-z_][\w:*]*", args[0]):
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", args[1]):
            continue
        line = text.count("\n", 0, match.start()) + 1
        rest = args[2:]
        default = None if macro.endswith("_pd") else (rest.pop(0) if rest else None)
        attributes = sorted(word for word in rest if word in ATTRIBUTES)
        # A documentation string too long for one line is written as two adjacent string
        # literals, which C++ concatenates and a naive read of the argument does not.
        documentation = "".join(
            piece for part in rest if part.startswith('"')
            for piece in re.findall(r'"((?:[^"\\]|\\.)*)"', part))
        tail = continued(text[end:end + 400])
        bounds = None
        opens = re.match(r"range\s*\(", tail)
        if opens:
            got, _ = arguments(tail, opens.end() - 1)
            if got and len(got) == 2:
                bounds = (continued(got[0]), continued(got[1]))
        after = re.sub(r"^range\s*\([^)]*\)\s*", "", tail)
        limit = re.match(r"constraint\s*\(", after)
        arguments_of_constraint = None
        if limit:
            got, _ = arguments(after, limit.end() - 1)
            if got:
                arguments_of_constraint = [continued(part) for part in got]
        found.append({
            "macro": macro,
            "type": args[0],
            "name": args[1],
            "default": default,
            "attributes": attributes,
            "documentation": documentation,
            "range": bounds,
            "constraint": arguments_of_constraint,
            "file": path,
            "line": line,
            "guards": inside.get(line, ()),
        })
    return found


def pd_globals(path: str, text: str) -> list[dict]:
    """`define_pd_global(intx, ThreadStackSize, 2048)`, which is where a _pd gets its
    default. There is one of these per platform per flag, which is the point of them."""
    inside = guards(text)
    found = []
    for match in PD_GLOBAL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        found.append({
            "type": match.group(1),
            "name": match.group(2),
            "default": match.group(3).strip(),
            "file": path,
            "line": line,
            "guards": inside.get(line, ()),
        })
    return found


def literal(expr: str | None) -> str | None:
    """One default or bound as the string the VM would print, or None if it is code.

    Most defaults are a number, a boolean or a string. A few are written in terms of the
    type widths or of `1*M`, which is arithmetic this can do. The rest are expressions
    that depend on the build or on the platform, like `trueInDebug` and
    `NOT_LP64(4*M) LP64_ONLY(512*M)`, and pretending to know what those come to would be
    inventing the answer rather than reading it.
    """
    if expr is None:
        return None
    text = expr.strip()
    if text in ("true", "false"):
        return text
    if text == "nullptr":
        return ""
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if not SAFE.match(text):
        return None
    for name in re.findall(r"[A-Za-z_]\w*", text):
        if name not in CONSTANTS:
            return None
    try:
        value = eval(text, {"__builtins__": {}}, dict(CONSTANTS))  # noqa: S307
    except (SyntaxError, TypeError, ZeroDivisionError, NameError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not re.search(r"\d\.\d|\.\d|\d\.", text):
        # C++ divides two integers into an integer. Python does not, and the six bounds
        # written as `max_jint / 4` are exactly where the difference shows.
        value = math.trunc(value)
    return repr(value) if isinstance(value, float) else str(value)


def same_number(one: str, two: str) -> bool:
    try:
        return math.isclose(float(one), float(two), rel_tol=1e-9, abs_tol=1e-9)
    except ValueError:
        return one == two


def operating_system(platform: str) -> str:
    return platform.split("-")[0]


def read_results(root: pathlib.Path) -> list[dict]:
    found = []
    for path in sorted((root / RESULTS).glob("*.json")):
        found.append(json.loads(path.read_text(encoding="utf-8")))
    if not found:
        sys.exit(
            f"{RESULTS} has no results in it. Run probes/vmflags/run.py with JAVA_HOME "
            f"pointed at the pinned JDK first."
        )
    return found


def headers_for(system: str) -> list[str]:
    return [path for path, only in HEADERS if only is None or only == system]


def commas(number: int) -> str:
    return f"{number:,}"


def missing_reason(entry: dict, orphaned: set[str]) -> str | None:
    """Why a flag the headers declare is not in the VM, or None if there is no reason."""
    if entry["macro"].startswith("develop") or entry["macro"] == "notproduct":
        return "a develop flag, which a product build does not compile in"
    if entry["guards"]:
        guard = entry["guards"][-1]
        if guard.startswith("#else of "):
            return f"declared in the `#else` of `{guard.removeprefix('#else of ')}`"
        return f"declared inside `{guard}`"
    if entry["file"] in orphaned:
        return "in a header for a component this build does not contain"
    return None


def build(root: pathlib.Path) -> dict:
    pin = load_pin(root)
    tag = pin["jdk_tag"]
    results = read_results(root)

    wanted = sorted({path for result in results
                     for path in headers_for(operating_system(result["platform"]))})
    unknown = sorted({operating_system(r["platform"]) for r in results}
                     - {only for _, only in HEADERS if only})
    if unknown:
        sys.exit(
            f"there are results from {unknown}, and HEADERS has no operating system "
            f"headers for that. Joining them against the shared headers alone would "
            f"report every platform flag as undeclared. Add the headers or drop the "
            f"result."
        )

    texts: dict[str, str] = {}
    hashes: dict[str, str] = {}
    origins: dict[str, str] = {}
    for path in wanted:
        text, came_from = source(tag, path)
        texts[path] = text
        hashes[path] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        origins[path] = came_from

    declared: dict[str, list[dict]] = {}
    supplied: dict[str, list[dict]] = {}
    for path, text in texts.items():
        for entry in declarations(path, text):
            declared.setdefault(entry["name"], []).append(entry)
        for entry in pd_globals(path, text):
            supplied.setdefault(entry["name"], []).append(entry)

    return {
        "tag": tag,
        "java_build": pin["jdk_build"],
        "headers": wanted,
        "hashes": hashes,
        "origins": origins,
        "declared": declared,
        "supplied": supplied,
        "results": results,
    }


def per_platform(data: dict) -> dict[str, dict]:
    """The join, one platform at a time. Everything the page says comes out of here."""
    joined = {}
    for result in data["results"]:
        platform = result["platform"]
        mine = set(headers_for(operating_system(platform)))
        declared = {
            name: [e for e in entries if e["file"] in mine]
            for name, entries in data["declared"].items()
        }
        declared = {name: entries for name, entries in declared.items() if entries}
        supplied = {
            name: [e for e in entries if e["file"] in mine]
            for name, entries in data["supplied"].items()
        }
        supplied = {name: entries for name, entries in supplied.items() if entries}

        reported = result["flags"]
        absent = sorted(set(declared) - set(reported))
        # A header whose every product flag is missing configures a component that is
        # not in this build. One missing out of eighty would be something else, and the
        # difference between those two is the whole reason this is computed rather than
        # listed.
        by_file: dict[str, list[str]] = {}
        for name, entries in declared.items():
            entry = entries[0]
            if entry["macro"].startswith("product") and not entry["guards"]:
                by_file.setdefault(entry["file"], []).append(name)
        orphaned = {
            path for path, names in by_file.items()
            if names and all(name not in reported for name in names)
        }

        joined[platform] = {
            "result": result,
            "declared": declared,
            "supplied": supplied,
            "absent": absent,
            "orphaned": orphaned,
        }
    return joined


def defaults_that_moved(view: dict) -> list[tuple[str, str, str]]:
    """Flags whose origin says default and whose value is not the declared default."""
    moved = []
    reported = view["result"]["flags"]
    for name, entries in sorted(view["declared"].items()):
        entry = entries[0]
        if name not in reported:
            continue
        seen = reported[name]
        if seen["origin"] != "default" or seen.get("unstable"):
            continue
        want = literal(entry["default"])
        if want is None and entry["macro"].endswith("_pd"):
            supply = view["supplied"].get(name)
            want = literal(supply[0]["default"]) if supply and len(supply) == 1 else None
        if want is None:
            continue
        if not same_number(want, seen["value"]):
            moved.append((name, want, seen["value"]))
    return moved


def comparable_defaults(view: dict) -> tuple[int, int]:
    """How many defaults could be compared at all, and how many agreed."""
    reported = view["result"]["flags"]
    compared = agreed = 0
    for name, entries in view["declared"].items():
        entry = entries[0]
        if name not in reported:
            continue
        seen = reported[name]
        if seen["origin"] != "default" or seen.get("unstable"):
            continue
        want = literal(entry["default"])
        if want is None and entry["macro"].endswith("_pd"):
            supply = view["supplied"].get(name)
            want = literal(supply[0]["default"]) if supply and len(supply) == 1 else None
        if want is None:
            continue
        compared += 1
        agreed += same_number(want, seen["value"])
    return compared, agreed


def range_check(view: dict) -> tuple[int, int, list[tuple[str, str, str]]]:
    """Declared bounds against enforced bounds: compared, unreadable, disagreements."""
    reported = view["result"]["ranges"]
    compared = unreadable = 0
    wrong = []
    for name, entries in sorted(view["declared"].items()):
        entry = entries[0]
        if not entry["range"] or name not in view["result"]["flags"]:
            continue
        low, high = (literal(entry["range"][0]), literal(entry["range"][1]))
        if low is None or high is None:
            unreadable += 1
            continue
        seen = reported.get(name)
        if seen is None:
            wrong.append((name, f"{entry['range'][0]} to {entry['range'][1]}",
                          "no bounds reported"))
            continue
        compared += 1
        if not (same_number(low, seen["min"]) and same_number(high, seen["max"])):
            wrong.append((name, f"{low} to {high}", f"{seen['min']} to {seen['max']}"))
    return compared, unreadable, wrong


def verify(data: dict, joined: dict) -> list[str]:
    """Stop on a disagreement between the two sources. Return notes worth printing."""
    notes: list[str] = []

    if len(data["headers"]) < 15:
        sys.exit(f"only {len(data['headers'])} headers were read, which cannot be right")
    total = sum(len(v) for v in data["declared"].values())
    if total < 800:
        sys.exit(
            f"the headers at {data['tag']} parsed to {total} declarations. That is far "
            f"too few, so the macro shape changed and the parser is reading past it."
        )
    doubled = sorted(name for name, entries in data["declared"].items()
                     if len({e["file"] for e in entries}) > 1)
    if doubled:
        sys.exit(
            f"{doubled[:5]} are declared in more than one header. The join below assumes "
            f"one declaration per flag per platform, so it would silently pick one."
        )

    for platform, view in sorted(joined.items()):
        result = view["result"]
        if result["java_build"] != data["java_build"]:
            sys.exit(
                f"{platform} was measured on java {result['java_build']} and docs/pin.json "
                f"pins {data['java_build']}. Rerun the probe on the pinned JDK."
            )

        undeclared = sorted(set(result["flags"]) - set(view["declared"]))
        if undeclared:
            sys.exit(
                f"{platform} reports {len(undeclared)} flags that no header in HEADERS "
                f"declares, starting with {undeclared[:5]}. Either a header moved or one "
                f"is missing from the list, and every count on the page would be wrong."
            )

        for name, seen in sorted(result["flags"].items()):
            entry = view["declared"][name][0]
            if entry["type"] != seen["type"]:
                sys.exit(
                    f"{name} is declared `{entry['type']}` in {entry['file']} and "
                    f"reported `{seen['type']}` on {platform}."
                )
            spoken = {word for word in seen["kind"] if word in ATTRIBUTES.values()}
            written = {ATTRIBUTES[word] for word in entry["attributes"]}
            if spoken != written:
                sys.exit(
                    f"{name} is declared {sorted(written) or 'with no attribute'} in "
                    f"{entry['file']} and reported as {sorted(spoken)} on {platform}."
                )
            strange = sorted(set(seen["kind"]) - KINDS)
            if strange:
                sys.exit(
                    f"{platform} prints {strange} in the kind column for {name}, which "
                    f"is not a word this generator knows. The vocabulary changed."
                )

        unexplained = [name for name in view["absent"]
                       if missing_reason(view["declared"][name][0], view["orphaned"])
                       is None]
        if unexplained:
            sys.exit(
                f"{platform} does not report {len(unexplained)} flags that its headers "
                f"declare and that nothing here explains, starting with "
                f"{unexplained[:5]}. A product flag missing from a product build is "
                f"either a build option this does not model or a parse error."
            )

        _, _, wrong = range_check(view)
        if wrong:
            sys.exit(
                f"{platform} enforces a different range than the header declares for "
                f"{wrong[:3]}. The header and the VM disagree about the same number."
            )

        for gate in result["gates"]:
            refused = gate["name"] != "the same diagnostic flag, unlocked"
            if refused and gate["exit"] == 0:
                sys.exit(
                    f"on {platform} the gate {gate['name']!r} was supposed to be refused "
                    f"and the VM accepted it. That is a real change in what a product "
                    f"build allows and the page below says otherwise."
                )
            if not refused and gate["exit"] != 0:
                sys.exit(
                    f"on {platform} the VM would not start with the diagnostic flag "
                    f"unlocked, which is the one case that is supposed to work."
                )

    platforms = sorted(joined)
    if len(platforms) > 1:
        first = joined[platforms[0]]["result"]["flags"]
        for other in platforms[1:]:
            second = joined[other]["result"]["flags"]
            for name in sorted(set(first) & set(second)):
                if first[name]["type"] != second[name]["type"]:
                    sys.exit(f"{name} is {first[name]['type']} on {platforms[0]} and "
                             f"{second[name]['type']} on {other}. Two JDKs, one pin.")
                if first[name]["kind"] != second[name]["kind"]:
                    sys.exit(f"{name} is {first[name]['kind']} on {platforms[0]} and "
                             f"{second[name]['kind']} on {other}. Two JDKs, one pin.")
    else:
        notes.append(
            "Only one platform was measured, so nothing below is cross checked against a "
            "second environment and the platform differences section is empty."
        )
    return notes


def anatomy(data: dict, joined: dict) -> dict | None:
    """One declaration to quote, with every optional part in it.

    The flag the gates in section 4 push against is preferred over any other, because a
    reader who has seen its range rejected and then its constraint rejected has a use for
    the line that declares both.
    """
    named = []
    for view in joined.values():
        for gate in view["result"]["gates"]:
            named.append(re.sub(r"^-XX:[+-]?", "", gate["options"][-1]).split("=")[0])
    whole = [entries[0] for entries in data["declared"].values()
             if entries[0]["range"] and entries[0]["constraint"]
             and entries[0]["default"]]
    for entry in whole:
        if entry["name"] in named:
            return entry
    return whole[0] if whole else None


def render(data: dict, joined: dict, notes: list[str]) -> str:
    platforms = sorted(joined)
    out: list[str] = []
    add = out.append

    add("# BP-FLAGS. Every flag a product JVM has, and who set it")
    add("")
    add(f"Generated by `tools/gen_flags.py`. Do not edit it, edit a source. There are "
        f"two: the {len(data['headers'])} HotSpot globals headers at `{data['tag']}` for "
        f"what is declared, and `probes/vmflags/results` for what a running VM reports. "
        f"Neither one answers the question on its own.")
    add("")
    measured = ", ".join(
        f"{platform} on {joined[platform]['result']['measured']} with "
        f"{joined[platform]['result']['processors']} processors"
        for platform in platforms)
    add(f"Measured on java {data['java_build']}: {measured}.")
    add("")
    for note in notes:
        add(note)
        add("")

    add("## 1. How a flag is declared")
    add("")
    add("A flag is one line of a macro list in a header. The macro name says which "
        "builds have it, the first two arguments are its type and its name, and what "
        "follows is the default, any attribute that restricts who may set it, the "
        "documentation string the VM prints for `-XX:+PrintFlagsFinal`, and sometimes a "
        "range and a constraint function.")
    add("")
    add("| macro | which builds | declared |")
    add("|---|---|---:|")
    counted = {macro: 0 for macro in MACROS}
    for entries in data["declared"].values():
        counted[entries[0]["macro"]] = counted.get(entries[0]["macro"], 0) + 1
    for macro, meaning in MACROS.items():
        add(f"| `{macro}` | {meaning} | {commas(counted.get(macro, 0))} |")
    add("")
    example = anatomy(data, joined)
    if example:
        add(f"One declaration with every optional part in it, at "
            f"{example['file']}:{example['line']}@{data['tag']}")
        add("")
        add("```")
        add(f"{example['macro']}({example['type']}, {example['name']}, "
            f"{example['default']},")
        add(f"        \"{example['documentation']}\")")
        add(f"        range({example['range'][0]}, {example['range'][1]})")
        add(f"        constraint({', '.join(example['constraint'])})")
        add("```")
        add("")
        add("The range is checked before the VM starts and the constraint is checked "
            "after, which is why setting this flag to a number inside its range can "
            "still be refused. Both refusals are measured in section 4.")
        add("")

    add("## 2. What survives into a product build")
    add("")
    add("A header is not a build. These are the same flags counted twice: once as the "
        "source declares them for a platform, and once as a JVM on that platform reports "
        "them.")
    add("")
    add("| platform | declared for it | reported by it | declared and absent | reported "
        "and undeclared |")
    add("|---|---:|---:|---:|---:|")
    for platform in platforms:
        view = joined[platform]
        add(f"| `{platform}` | {commas(len(view['declared']))} | "
            f"{commas(len(view['result']['flags']))} | {commas(len(view['absent']))} | 0 |")
    add("")
    add("The last column is zero and that is a check rather than a claim. Every flag "
        "every measured VM reports is declared in one of the headers listed in section "
        "9, and a flag that was not would stop this generator, because it would mean the "
        "list is missing a header and every other number here is counted over the wrong "
        "set.")
    add("")
    reasons: dict[str, dict[str, list[str]]] = {}
    for platform in platforms:
        view = joined[platform]
        for name in view["absent"]:
            reason = missing_reason(view["declared"][name][0], view["orphaned"])
            reasons.setdefault(reason, {}).setdefault(platform, []).append(name)
    add("Why the declared ones are not in it:")
    add("")
    add("| reason | " + " | ".join(f"`{p}`" for p in platforms) + " | for example |")
    add("|---|" + "---:|" * len(platforms) + "---|")
    for reason, seen in sorted(reasons.items(),
                               key=lambda pair: -max(len(n) for n in pair[1].values())):
        cells = [commas(len(seen.get(platform, []))) for platform in platforms]
        example = sorted(name for names in seen.values() for name in names)[0]
        add(f"| {reason} | " + " | ".join(cells) + f" | `{example}` |")
    add("")
    add("The develop flags are the group nobody expects to be able to use. The other "
        "groups are the interesting ones, because a header that this build did compile "
        "can declare a flag for a component that it did not, and the VM will report that "
        "flag as though it were usable. Section 4 has the case where that happens.")
    add("")

    types: dict[str, int] = {}
    for entries in data["declared"].values():
        types[entries[0]["type"]] = types.get(entries[0]["type"], 0) + 1
    add("## 3. Types")
    add("")
    add(f"Every flag has one of {len(types)} C++ types, and the type decides how the "
        f"value is parsed on the command line and printed by `-XX:+PrintFlagsFinal`. The "
        f"header and the VM agree about the type of every flag measured here, which is "
        f"checked rather than assumed.")
    add("")
    header = "| type | declared | " + " | ".join(f"`{p}`" for p in platforms) + " |"
    add(header)
    add("|---|---:|" + "---:|" * len(platforms))
    for kind in sorted(types):
        cells = []
        for platform in platforms:
            reported = joined[platform]["result"]["counts"]["by_type"]
            cells.append(commas(reported.get(kind, 0)))
        add(f"| `{kind}` | {commas(types[kind])} | " + " | ".join(cells) + " |")
    add("")

    add("## 4. What you are allowed to set")
    add("")
    add("Three of the words the VM prints in its kind column are gates. A `diagnostic` "
        "flag needs `-XX:+UnlockDiagnosticVMOptions` before it on the command line, an "
        "`experimental` flag needs `-XX:+UnlockExperimentalVMOptions`, and a "
        "`manageable` flag is the opposite of a gate: it can be changed while the VM is "
        "running. The rest of the words say which compiler or which architecture the "
        "flag belongs to.")
    add("")
    add("| kind word | " + " | ".join(f"`{p}`" for p in platforms) + " |")
    add("|---|" + "---:|" * len(platforms))
    words = sorted({word for platform in platforms
                    for word in joined[platform]["result"]["counts"]["by_kind"]})
    for word in words:
        cells = [commas(joined[p]["result"]["counts"]["by_kind"].get(word, 0))
                 for p in platforms]
        add(f"| `{word}` | " + " | ".join(cells) + " |")
    add("")
    first = joined[platforms[0]]["result"]["counts"]
    add(f"Unlocking changes nothing about what is listed. The same VM reports "
        f"{commas(first['reported'])} flags with no unlock option and "
        f"{commas(first['reported_when_unlocked'])} with both of them, so a flag being "
        f"invisible is not what the gate does. What it does is refuse to start.")
    add("")
    add("| what was on the command line | exit | what the VM said |")
    add("|---|---:|---|")
    gates = joined[platforms[0]]["result"]["gates"]
    for gate in gates:
        options = " ".join(gate["options"])
        said = gate["message"] or "nothing, the VM started"
        add(f"| `{options}` | {gate['exit']} | {said} |")
    add("")
    refusals = [gate for gate in gates if gate["exit"] != 0]
    add(f"{commas(len(refusals))} refusals and {commas(len({g['message'] for g in refusals}))} "
        f"different sentences, which is more than most tools manage. "
        "The one worth reading twice is the refusal for a collector. `UseShenandoahGC` is a "
        "`product` flag, it is in the table this page counts, `-XX:+PrintFlagsFinal` "
        "prints it with a default of false, and the VM will not start with it on. The "
        "flag is declared in the shared collector header, which every build compiles, "
        "and the collector it turns on is in a header this build left out. That is the "
        "difference between the two smaller groups in section 2 made visible from the "
        "command line.")
    add("")

    add("## 5. Origins, and where the origin column is not the truth")
    add("")
    add("`-XX:+PrintFlagsFinal` prints an origin in the second pair of braces. There are "
        "three of them here: `default` means nothing overrode the declared value, "
        "`ergonomic` means the VM chose the value from the machine it is on, and "
        "`command line` means it was asked for.")
    add("")
    add("| origin | " + " | ".join(f"`{p}`" for p in platforms) + " |")
    add("|---|" + "---:|" * len(platforms))
    for origin in ("default", "ergonomic", "command line"):
        cells = [commas(joined[p]["result"]["counts"]["by_origin"].get(origin, 0))
                 for p in platforms]
        add(f"| `{origin}` | " + " | ".join(cells) + " |")
    add("")
    add("The one flag at `command line` in every run is `PrintFlagsFinal` itself, which "
        "is a fair reminder that the measurement is part of what is measured.")
    add("")
    moved = {platform: dict((name, (want, got))
                            for name, want, got in defaults_that_moved(joined[platform]))
             for platform in platforms}
    counted = {platform: comparable_defaults(joined[platform]) for platform in platforms}
    sentences = "; ".join(
        f"on `{platform}` {commas(counted[platform][1])} of {commas(counted[platform][0])}"
        for platform in platforms)
    add(f"A flag at origin `default` should be holding the value its header declares. "
        f"Counting the ones where the default is a literal this generator can evaluate: "
        f"{sentences}.")
    add("")
    everywhere = sorted(set().union(*(set(m) for m in moved.values())))
    add(f"**{commas(len(everywhere))} flags say `default` and are not at their declared "
        f"default.** The declared value is the same on both platforms because it is one "
        f"line of one header. The reported value is not.")
    add("")
    add("| flag | declared default | "
        + " | ".join(f"reported on `{p}`" for p in platforms) + " |")
    add("|---|---|" + "---|" * len(platforms))
    for name in everywhere:
        want = next(m[name][0] for m in moved.values() if name in m)
        cells = []
        for platform in platforms:
            seen = joined[platform]["result"]["flags"].get(name)
            if name in moved[platform]:
                cells.append(f"`{moved[platform][name][1]}`")
            elif seen is None:
                cells.append("not in this build")
            else:
                cells.append(f"`{seen['value']}`, which is the declared value")
        add(f"| `{name}` | `{want}` | " + " | ".join(cells) + " |")
    add("")
    add("Those are not a misreading of the header. HotSpot has more than one way to "
        "change a flag from inside the VM and they do not all record that it happened, "
        "so the processor feature detection and a few startup decisions write a value "
        "and leave the origin saying `default`. The report next to this page names the "
        "two functions and cites them. What the table above is for is the consequence: a "
        "reader who takes `default` to mean the value in the source gets the wrong answer "
        "for every row of it, with no warning printed anywhere.")
    add("")

    add("## 6. What one option moves")
    add("")
    add("Setting one flag on the command line does not set one flag. Each row is one "
        "option, added to a run that is otherwise identical, and the count is how many "
        "other flags came out with a different value.")
    add("")
    add("| option | " + " | ".join(f"flags moved on `{p}`" for p in platforms) + " |")
    add("|---|" + "---:|" * len(platforms))
    scenarios = joined[platforms[0]]["result"]["scenarios"]
    for index, scenario in enumerate(scenarios):
        cells = []
        for platform in platforms:
            other = joined[platform]["result"]["scenarios"][index]
            cells.append(commas(len(other["changed"])) if other.get("supported")
                         else "not in this build")
        add(f"| `{' '.join(scenario['options'])}` | " + " | ".join(cells) + " |")
    add("")
    interpreter = next((s for s in scenarios if s["options"] == ["-Xint"]), None)
    if interpreter and interpreter.get("supported"):
        add(f"`-Xint` is the one to read. It is documented as turning the compilers off, "
            f"and it moves {commas(len(interpreter['changed']))} flags on "
            f"`{platforms[0]}`:")
        add("")
        add("| flag | without `-Xint` | with it |")
        add("|---|---|---|")
        for name, change in sorted(interpreter["changed"].items()):
            add(f"| `{name}` | `{change['from']}` | `{change['to']}` |")
        add("")
        add("None of those appear on the command line and all of them appear in a "
            "support ticket eventually.")
        add("")

    add("## 7. Ranges and constraints")
    add("")
    add("A declared range is checked before the VM starts. A constraint is a function, "
        "checked at a named point in startup, and a flag can have either, both or "
        "neither.")
    add("")
    add("| | " + " | ".join(f"`{p}`" for p in platforms) + " |")
    add("|---|" + "---:|" * len(platforms))
    declared_ranges = {}
    declared_constraints = {}
    for platform in platforms:
        view = joined[platform]
        reported = view["result"]["flags"]
        declared_ranges[platform] = sum(
            1 for name, entries in view["declared"].items()
            if entries[0]["range"] and name in reported)
        declared_constraints[platform] = sum(
            1 for name, entries in view["declared"].items()
            if entries[0]["constraint"] and name in reported)
    add("| reported flags with a range in the header | "
        + " | ".join(commas(declared_ranges[p]) for p in platforms) + " |")
    add("| reported flags with a constraint in the header | "
        + " | ".join(commas(declared_constraints[p]) for p in platforms) + " |")
    add("| flags the VM prints bounds for | "
        + " | ".join(commas(joined[p]["result"]["counts"]["with_a_range"])
                     for p in platforms) + " |")
    checked = []
    for platform in platforms:
        compared, unreadable, _ = range_check(joined[platform])
        checked.append((compared, unreadable))
    add("| declared bounds compared against enforced bounds | "
        + " | ".join(commas(c) for c, _ in checked) + " |")
    add("| declared bounds written as code this cannot evaluate | "
        + " | ".join(commas(u) for _, u in checked) + " |")
    add("")
    add("Every bound that could be compared matched, which is the check that says the "
        "header is being read the way the compiler reads it. A single disagreement stops "
        "this generator.")
    add("")

    if len(platforms) > 1:
        add("## 8. The same JDK, two platforms")
        add("")
        sets = {p: set(joined[p]["result"]["flags"]) for p in platforms}
        shared = set.intersection(*sets.values())
        add(f"Both platforms run build {data['java_build']} on the same processor "
            f"architecture. They do not have the same flags. "
            f"{commas(len(shared))} are on both.")
        add("")
        add("| platform | only there | for example |")
        add("|---|---:|---|")
        for platform in platforms:
            only = sorted(sets[platform] - shared)
            add(f"| `{platform}` | {commas(len(only))} | "
                + (", ".join(f"`{name}`" for name in only[:4]) or "none") + " |")
        add("")
        differing = []
        for name in sorted(shared):
            values = {joined[p]["result"]["flags"][name]["value"] for p in platforms}
            origins = {joined[p]["result"]["flags"][name]["origin"] for p in platforms}
            if origins == {"default"} and len(values) > 1:
                supply = {}
                for platform in platforms:
                    got = joined[platform]["supplied"].get(name)
                    if got and len(got) == 1:
                        supply[platform] = got[0]
                differing.append((name, supply))
        sourced = [pair for pair in differing if len(pair[1]) == len(platforms)]
        add(f"{commas(len(differing))} flags say `default` on both platforms and hold a "
            f"different value on each. {commas(len(sourced))} of them have a "
            f"`define_pd_global` per platform, which is where a `product_pd` flag gets "
            f"the default the shared header does not give it, so the two numbers are in "
            f"the source and can be pointed at. The rest are section 5 again: nothing in "
            f"the source says these numbers, the VM worked them out from the machine and "
            f"then called the result the default.")
        add("")
        add("| flag | " + " | ".join(f"`{p}`" for p in platforms) + " | declared where |")
        add("|---|" + "---|" * len(platforms) + "---|")
        for name, supply in differing:
            cells = [f"`{joined[p]['result']['flags'][name]['value']}`" for p in platforms]
            if len(supply) == len(platforms):
                where = ", ".join(
                    f"{supply[p]['file']}:{supply[p]['line']}@{data['tag']}"
                    for p in platforms)
            else:
                where = "nowhere, so the VM decided it"
            add(f"| `{name}` | " + " | ".join(cells) + f" | {where} |")
        add("")
        unstable = sorted({name for p in platforms
                           for name in joined[p]["result"]["unstable_between_runs"]})
        if unstable:
            add(f"One more value is not stable even on one machine. Running the same "
                f"command three times in a row gives three different values for "
                + ", ".join(f"`{name}`" for name in unstable)
                + ", at origin `default` every time. The probe runs the baseline three "
                  "times so that it can say so rather than record whichever number came "
                  "up first.")
            add("")

    add("## 9. Provenance")
    add("")
    add("A hash of each header as it was read, for the same reason every other generated "
        "page here carries one: a source that was rewritten underneath a table should be "
        "an event somebody looks at rather than a table that quietly says something else "
        "next Tuesday.")
    add("")
    add("| header | declarations | `define_pd_global` | sha256 |")
    add("|---|---:|---:|---|")
    for path in data["headers"]:
        count = sum(1 for entries in data["declared"].values()
                    for entry in entries if entry["file"] == path)
        supplied = sum(1 for entries in data["supplied"].values()
                       for entry in entries if entry["file"] == path)
        add(f"| `{path}` | {commas(count)} | {commas(supplied)} | "
            f"`{data['hashes'][path]}` |")
    add("")
    add("A header with no declarations and a column of `define_pd_global` in it is a "
        "platform header: it declares no flag of its own and supplies the defaults for "
        "the ones the shared headers declared as `product_pd`.")
    add("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed page is stale")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    data = build(root)
    joined = per_platform(data)
    notes = verify(data, joined)
    text = render(data, joined, notes)
    target = root / OUTPUT

    if args.check:
        if not target.is_file():
            print(f"{OUTPUT} is not committed, run tools/gen_flags.py", file=sys.stderr)
            return 1
        if target.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT} is stale. Run tools/gen_flags.py and read the diff: it is "
                  f"either a header that changed at {data['tag']} or a probe result that "
                  f"was replaced.", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is current at {data['tag']}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} from {len(data['headers'])} headers at {data['tag']} and "
          f"{len(data['results'])} measured environments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
