#!/usr/bin/env python3
"""What can a Java notebook kernel actually put on the screen, and where does it survive.

Issue #4. `PredictGate` appears in every lesson and `WarmupTape` is the most valuable single
artifact in the project, so before either one is written it is worth knowing what a Java
kernel can emit and what each front end does with it afterwards. The spec assumed anywidget,
which is a Python library and cannot run in a JShell kernel, so the real question is which
MIME payloads survive and how much of them is still there when a reader opens the notebook.

Twelve techniques, four places each.

  kernel      does the kernel emit the payload at all
  nbconvert   the static HTML built by `jupyter nbconvert --to html`
  lab_saved   JupyterLab showing a saved notebook the reader has not run
  lab_live    JupyterLab showing output the running kernel produced a moment ago

The last three are read out of a real browser rather than guessed from the HTML, because
the interesting failures are silent: an attribute the sanitizer drops leaves markup that
still looks right in the file and does nothing on the page.

  JAVA_HOME=... python probes/widgets/run.py --out probes/widgets/results/osx-arm64.json

It needs a virtual environment with jupyterlab, nbclient, nbconvert, the jjava kernel and
playwright with chromium. CONTRIBUTING.md has the four commands. Nothing here is in CI,
because a probe that needs a browser and a JDK is not a thing to run on every push.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent

# One payload per technique, in the order a widget author would reach for them. The name is
# the key everywhere downstream, so it is the thing to keep stable.
TECHNIQUES: list[tuple[str, str, str]] = [
    (
        "html_plain",
        "text/html",
        '<div id="wp_plain">plain html, no css and no script</div>',
    ),
    (
        "html_inline_style",
        "text/html",
        '<div class="wp_inline" style="color:#c92a2a;font-weight:700">inline style</div>',
    ),
    (
        "html_style_tag",
        "text/html",
        '<style>.wp_css{color:#2b8a3e}</style><div class="wp_css">a style tag</div>',
    ),
    (
        "html_details",
        "text/html",
        "<details><summary>click to open</summary><p>the hidden part</p></details>",
    ),
    (
        "html_checked_css",
        "text/html",
        '<style>.wp_radio label{background:#dee2e6}'
        '.wp_radio input:checked+label{background:#ffd8a8}</style>'
        '<span class="wp_radio"><input type="radio" id="wp_r" name="wp_g">'
        '<label for="wp_r">one</label></span>',
    ),
    (
        "html_inline_script",
        "text/html",
        '<div id="wp_script">script did not run</div>'
        '<script>document.getElementById("wp_script").textContent="script ran";</script>',
    ),
    (
        "html_onclick",
        "text/html",
        "<button onclick=\"this.textContent='onclick ran'\">click me</button>",
    ),
    (
        "html_iframe_srcdoc",
        "text/html",
        '<iframe srcdoc="&lt;b&gt;inside an iframe&lt;/b&gt;" '
        'style="border:0;height:30px;width:200px"></iframe>',
    ),
    (
        "html_img_data_uri",
        "text/html",
        '<img alt="a red dot" width="20" height="20" src="data:image/svg+xml;base64,'
        "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMCIgaGVpZ2h0"
        'PSIyMCI+PGNpcmNsZSBjeD0iMTAiIGN5PSIxMCIgcj0iOCIgZmlsbD0icmVkIi8+PC9zdmc+">',
    ),
    ("markdown", "text/markdown", "**markdown bold**"),
    (
        "svg_mime",
        "image/svg+xml",
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
        '<rect width="40" height="20" fill="#1971c2"/></svg>',
    ),
    (
        "javascript_mime",
        "application/javascript",
        'window.wp_js_ran = true;',
    ),
]

# The checks, one per technique, run against the output element in a real page. Each returns
# a short phrase rather than a boolean, because "the markup is there and does nothing" and
# "the markup is gone" are different failures and a widget author needs to tell them apart.
CHECKS = r"""
(root, name) => {
  const text = root.textContent || "";
  const q = (sel) => root.querySelector(sel);
  switch (name) {
    case "html_plain": {
      const div = q("div");
      if (!div) return "gone";
      if (div.id === "wp_plain") return "renders, id kept";
      // The interesting half is where the id went, not that it went. Read the new
      // attribute name back rather than writing down that something changed, because
      // a rename and a deletion need different fixes in a widget.
      const moved = [...div.attributes].find(a => a.value === "wp_plain");
      return moved ? "renders, id moved to " + moved.name : "renders, id dropped";
    }
    case "html_inline_style": {
      const div = q(".wp_inline");
      if (!div) return "gone";
      return getComputedStyle(div).color === "rgb(201, 42, 42)"
        ? "style attribute kept" : "style attribute dropped";
    }
    case "html_style_tag": {
      const div = q(".wp_css");
      if (!div) return "gone";
      return getComputedStyle(div).color === "rgb(43, 138, 62)"
        ? "css applied" : "style tag dropped";
    }
    case "html_details": {
      const d = q("details");
      if (!d) return "gone";
      d.open = true;
      const shown = d.open && d.querySelector("p").offsetHeight > 0;
      d.open = false;
      return shown ? "opens and closes" : "present but will not open";
    }
    case "html_checked_css": {
      const input = q("input[type=radio]");
      const label = q("label");
      if (!input || !label) return "gone";
      if (input.disabled) return "input disabled";
      input.click();
      const lit = getComputedStyle(label).backgroundColor === "rgb(255, 216, 168)";
      return lit ? "clicks and highlights" : "clicks, css dropped";
    }
    case "html_inline_script": {
      if (!q("div")) return "gone";
      return text.indexOf("script ran") >= 0 ? "script ran" : "script blocked";
    }
    case "html_onclick": {
      const b = q("button");
      if (!b) return "gone";
      b.click();
      return b.textContent === "onclick ran" ? "onclick ran" : "onclick stripped";
    }
    case "html_iframe_srcdoc": {
      return q("iframe") ? "iframe kept" : "iframe removed";
    }
    case "html_img_data_uri": {
      const img = q("img");
      if (!img) return "gone";
      return img.complete && img.naturalWidth > 0 ? "image renders" : "image broken";
    }
    case "markdown": {
      return q("strong") ? "rendered as markdown" : "shown as text";
    }
    case "svg_mime": {
      if (q("svg")) return "drawn inline";
      if (q("img")) return "drawn as an image";
      return text.indexOf("<svg") >= 0 ? "shown as source text" : "gone";
    }
    case "javascript_mime": {
      return window.wp_js_ran ? "executed" : "not executed";
    }
    default: return "no check";
  }
}
"""


def java_home() -> pathlib.Path:
    home = os.environ.get("JAVA_HOME")
    if home and (pathlib.Path(home) / "bin").is_dir():
        return pathlib.Path(home)
    sys.exit(
        "set JAVA_HOME to the pinned JDK first. tools/fetch_jdk.py will install it and "
        "print the path."
    )


def need(module: str, why: str):
    try:
        return __import__(module)
    except ImportError:
        sys.exit(f"this probe needs {module}, {why}. See CONTRIBUTING.md.")


def notebook() -> dict:
    """One cell per technique, each a single `display` call and nothing else."""
    cells = []
    for name, mime, payload in TECHNIQUES:
        literal = json.dumps(payload)
        cells.append({
            "cell_type": "code",
            "execution_count": None,
            "id": name.replace("_", "-"),
            "metadata": {"technique": name},
            "outputs": [],
            "source": f'display({literal}, "{mime}");\n',
        })
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Java", "language": "java", "name": "java"},
            "language_info": {"name": "java"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def execute(path: pathlib.Path) -> dict[str, str]:
    """Run the notebook on the Java kernel and record which MIME types came back.

    This is the only question a front end cannot answer, and it is the one the spec got
    wrong: whether the kernel can emit the payload at all.
    """
    nbformat = need("nbformat", "to read and write notebooks")
    nbclient = need("nbclient", "to run the notebook on the Java kernel")

    book = nbformat.read(str(path), as_version=4)
    nbclient.NotebookClient(
        book, kernel_name="java", timeout=300, allow_errors=True
    ).execute()
    nbformat.write(book, str(path))

    answers = {}
    for cell, (name, mime, _) in zip(book.cells, TECHNIQUES):
        emitted = [
            key
            for output in cell.outputs
            if output.output_type == "display_data"
            for key in output.get("data", {})
        ]
        if mime in emitted:
            answers[name] = "emitted"
        elif emitted:
            answers[name] = "downgraded to " + ", ".join(sorted(set(emitted) - {mime}))
        else:
            answers[name] = "nothing came back"
    return answers


def stray_display_ids(path: pathlib.Path) -> int:
    """How many cells left a bare UUID under their output.

    `display` returns the id it assigned, JShell prints the value of the last expression,
    and the reader gets a line of hex under every widget. Worth counting rather than
    describing, because the fix belongs in `jvx` and somebody has to know it is needed.
    """
    book = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for cell in book["cells"]:
        for output in cell.get("outputs", []):
            if output.get("output_type") != "execute_result":
                continue
            text = "".join(output.get("data", {}).get("text/plain", ""))
            if len(text.strip()) == 36 and text.count("-") == 4:
                count += 1
    return count


def convert(path: pathlib.Path, into: pathlib.Path) -> pathlib.Path:
    out = into / "static.html"
    subprocess.run(
        [
            sys.executable, "-m", "nbconvert", "--to", "html", "--template", "lab",
            "--output", out.name, "--output-dir", str(into), str(path),
        ],
        check=True, capture_output=True, text=True, timeout=600,
    )
    return out


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def jupyterlab(root: pathlib.Path):
    """A JupyterLab bound to the loopback address, with a token, for as long as needed."""
    port = free_port()
    token = secrets.token_hex(16)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "jupyterlab", "--no-browser", f"--port={port}",
            f"--ServerApp.token={token}", "--ServerApp.ip=127.0.0.1",
            "--ServerApp.open_browser=False", f"--ServerApp.root_dir={root}",
            # Two of the machines this was measured on are root shells, and Lab refuses to
            # start there without being told. It is a measurement box on loopback with a
            # token, so the thing the warning protects against does not apply.
            "--allow-root",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            time.sleep(1)
        else:
            raise SystemExit("jupyterlab did not start")
        yield f"http://127.0.0.1:{port}", token
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=20)


RUN_ALL = """
async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const cells = () => [...document.querySelectorAll('.jp-Cell')];
  const total = cells().length;
  document.querySelector('.jp-Cell .cm-content').click();
  for (let i = 0; i < total; i++) {
    const event = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter',
      keyCode: 13, which: 13, shiftKey: true, bubbles: true, cancelable: true});
    (document.activeElement || document).dispatchEvent(event);
    await wait(500);
  }
  // Wait for the kernel rather than for the clock. A cold JShell takes under a second on
  // one of these machines and the better part of half a minute on another, so a fixed
  // sleep either wastes a minute or reads an empty page and calls it a result.
  const deadline = Date.now() + 300000;
  while (Date.now() < deadline) {
    const done = cells().filter(c => c.querySelector('.jp-OutputArea-output')).length;
    if (done === total) return done;
    await wait(2000);
  }
  return -1;
}
"""

READ = """
([names, check]) => {
  const run = eval(check);
  const cells = [...document.querySelectorAll('.jp-Cell')];
  const answers = {};
  names.forEach((name, index) => {
    const cell = cells[index];
    if (!cell) { answers[name] = 'no cell'; return; }
    const outputs = [...cell.querySelectorAll('.jp-OutputArea-output')];
    // The first output is the display, the second is the id JShell echoed. Checking the
    // whole cell would let the echo satisfy a text check that the widget failed.
    const root = outputs[0];
    if (!root) { answers[name] = 'no output'; return; }
    try { answers[name] = run(root, name); }
    catch (error) { answers[name] = 'check failed: ' + error.message; }
  });
  return answers;
}
"""


def in_a_browser(path: pathlib.Path, static: pathlib.Path, root: pathlib.Path) -> dict:
    """Three readings out of one chromium: the static file, then Lab saved, then Lab live."""
    playwright = need("playwright", "to read the rendered page rather than the markup")
    from playwright.sync_api import sync_playwright  # noqa: F401

    names = [name for name, _, _ in TECHNIQUES]
    answers: dict[str, dict[str, str]] = {}

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(static.as_uri())
            page.wait_for_selector(".jp-Cell", timeout=60000)
            answers["nbconvert"] = page.evaluate(READ, [names, CHECKS])
            page.close()

            with jupyterlab(root) as (base, token):
                page = browser.new_page()
                page.goto(f"{base}/lab/tree/{path.name}?token={token}")
                page.wait_for_selector(".jp-OutputArea-output", timeout=120000)
                page.wait_for_timeout(5000)
                answers["lab_saved"] = page.evaluate(READ, [names, CHECKS])

                ran = page.evaluate(RUN_ALL)
                if ran != len(names):
                    sys.exit(
                        f"only {ran} of {len(names)} cells produced output in JupyterLab, "
                        "so the live column would be a measurement of the wait and not of "
                        "the front end"
                    )
                answers["lab_live"] = page.evaluate(READ, [names, CHECKS])
                page.close()
        finally:
            browser.close()
    return answers


def versions() -> dict[str, str]:
    found = {}
    for name in ["jupyterlab", "nbconvert", "nbclient", "jjava"]:
        try:
            import importlib.metadata as meta
            found[name] = meta.version(name)
        except Exception:
            found[name] = "not installed"
    return found


def describe(home: pathlib.Path) -> dict:
    done = subprocess.run(
        [str(home / "bin" / ("java.exe" if platform.system() == "Windows" else "java")),
         "-version"],
        capture_output=True, text=True, timeout=120,
    )
    text = done.stdout + done.stderr
    build = ""
    for line in text.splitlines():
        if "build" in line:
            build = line.split("build", 1)[1].strip(" )")
            break
    # No hostname and no home directory. Which machine this ran on is not what it measures.
    return {
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "java_build": build,
        "versions": versions(),
        "measured": datetime.date.today().isoformat(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, help="where to write the results")
    ap.add_argument("--keep", type=pathlib.Path, help="keep the notebook and html here")
    args = ap.parse_args(argv)

    home = java_home()
    found = describe(home)

    with tempfile.TemporaryDirectory() as raw:
        root = args.keep if args.keep else pathlib.Path(raw)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "widgets.ipynb"
        path.write_text(json.dumps(notebook(), indent=1), encoding="utf-8")

        print("running the notebook on the java kernel", file=sys.stderr)
        kernel = execute(path)
        found["display_ids_echoed"] = stray_display_ids(path)

        print("building the static html", file=sys.stderr)
        static = convert(path, root)

        print("reading all three in a browser", file=sys.stderr)
        seen = in_a_browser(path, static, root)

        found["techniques"] = {
            name: {
                "mime": mime,
                "kernel": kernel[name],
                "nbconvert": seen["nbconvert"][name],
                "lab_saved": seen["lab_saved"][name],
                "lab_live": seen["lab_live"][name],
            }
            for name, mime, _ in TECHNIQUES
        }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    if shutil.which("java") is None and not os.environ.get("JAVA_HOME"):
        sys.exit("no java on PATH and no JAVA_HOME")
    raise SystemExit(main(sys.argv[1:]))
