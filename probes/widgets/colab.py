"""Issue #4, the missing column: do the widget payloads survive Colab's output sandbox.

This is the source. The notebook is built from it by `tools/gen_handoff.py` and the
protocol for running it is `docs/probes/handoff.md`.

The twelve payloads are imported from `probes/widgets/run.py` rather than copied, so
this notebook and the measured columns cannot drift apart. If a technique is added
there, it appears here on the next run.

The verdicts come from a person looking at a screen, because that is what the question
is. Nothing in a page can reliably tell you whether a browser two frames up decided to
strip a style tag before painting.
"""

# %% [markdown]
# # Do the widget payloads survive Colab's output sandbox
#
# [Issue #4](https://github.com/tamnd/jvm-internals/issues/4). The prediction gate
# appears in every lesson and the Warmup Tape is the most valuable single artifact in
# the project. Colab renders output in a per output iframe with its own rules, and if
# those rules flatten a widget into static markup then two of the seven rules of this
# project lose most of their force in the environment the lessons are written for.
#
# The same twelve payloads have already been measured in four places: the Java kernel,
# static HTML from nbconvert, JupyterLab showing a saved notebook nobody ran, and
# JupyterLab one second after the kernel produced them. Colab is the fifth place and the
# one that decides how the widgets get built. The four measured columns are in
# [the widget delivery grid](https://github.com/tamnd/jvm-internals/blob/main/docs/generated/widget-delivery.md).
#
# **One thing to know before you read the results.** This runs on the Python kernel, not
# the Java kernel. That is not a shortcut. The output a kernel sends is a MIME bundle,
# the front end never learns which language produced it, and the already measured kernel
# column shows JJava emits all twelve. So what this measures is Colab's front end, which
# is the half nobody has measured.
#
# **Before you start.** Fresh runtime, and expect to spend about ten minutes, most of it
# looking at things and answering questions.

# %%
REPO = "https://github.com/tamnd/jvm-internals"
BRANCH = "main"

import datetime
import json
import pathlib
import platform
import subprocess
import sys

subprocess.run(f"git clone --depth 1 --branch {BRANCH} {REPO} /content/jvm-internals",
               shell=True, capture_output=True, text=True)
sys.path.insert(0, "/content/jvm-internals/probes/widgets")

import run as widgets  # noqa: E402

TECHNIQUES = widgets.TECHNIQUES
print(len(TECHNIQUES), "techniques:", ", ".join(name for name, _, _ in TECHNIQUES))

# What to look for, per technique, in the words of somebody watching rather than in the
# words of the payload. A verdict is only worth recording if the person giving it was
# told what the working case looks like.
LOOK_FOR = {
    "html_plain": "the words 'plain html, no css and no script'",
    "html_inline_style": "the words 'inline style' in bold red, not plain black",
    "html_style_tag": "the words 'a style tag' in green, not plain black",
    "html_details": "a 'click to open' arrow that opens and closes when you click it",
    "html_checked_css": "a radio button that turns orange when you select it",
    "html_inline_script": "'script ran', not 'script did not run'",
    "html_onclick": "a button whose label changes to 'onclick ran' when clicked",
    "html_iframe_srcdoc": "the words 'inside an iframe' in bold",
    "html_img_data_uri": "a small red dot",
    "markdown": "the words 'markdown bold' in bold",
    "svg_mime": "a small blue rectangle",
    "javascript_mime": "nothing visible, the check for this one is the cell after next",
}

# The scale. Deliberately coarse, because a person eyeballing an iframe can tell these
# four apart reliably and cannot reliably tell anything finer apart.
SCALE = {
    "worked": "it did the thing described above",
    "inert": "the markup is there and does nothing, for example unstyled or unclickable",
    "gone": "nothing appeared at all",
    "source": "the raw markup is shown as text",
    "skip": "you could not tell",
}

# %% [markdown]
# ## The twelve payloads
#
# Run the cell below and then look at its output. Take your time over it. Click the
# details arrow, click the button, select the radio. The next cell asks you what
# happened and your memory of the last one is what it is recording.

# %%
from IPython.display import display, publish_display_data  # noqa: E402

for i, (name, mime, payload) in enumerate(TECHNIQUES, 1):
    display({"text/html": f"<hr><b>{i}. {name}</b> &nbsp; "
                          f"<code>{mime}</code><br><small>look for: "
                          f"{LOOK_FOR.get(name, '')}</small>"}, raw=True)
    # The raw bundle rather than a helper class, so that what reaches the front end is
    # the MIME type under test and not whatever an IPython wrapper decided to send.
    publish_display_data({mime: payload})

# %% [markdown]
# ## Did the JavaScript one run
#
# `javascript_mime` sets a variable on `window` and shows nothing, so the only way to
# know is to ask the page afterwards. In Colab each output lives in its own iframe, so
# the honest expectation is that this reports false even if the script ran, because it
# ran in a different frame. That is itself the answer: an `application/javascript`
# payload cannot reach anything outside its own output.

# %%
display({"text/html":
         "<div id='wp_probe'>checking</div><script>"
         "document.getElementById('wp_probe').textContent = "
         "'window.wp_js_ran is ' + String(window.wp_js_ran) + "
         "', frame is ' + (window === window.top ? 'top' : 'nested');"
         "</script>"}, raw=True)

# %% [markdown]
# ## Now with the custom widget manager enabled
#
# Colab has historically required `output.enable_custom_widget_manager()` before third
# party widget JavaScript will run. The lessons cannot rely on a reader typing that, so
# what matters is whether anything above changes when it is on. Run this and look again.

# %%
WIDGET_MANAGER = {"available": False, "error": None}
try:
    from google.colab import output as colab_output

    colab_output.enable_custom_widget_manager()
    WIDGET_MANAGER["available"] = True
except Exception as problem:  # noqa: BLE001
    WIDGET_MANAGER["error"] = f"{type(problem).__name__}: {problem}"

print(json.dumps(WIDGET_MANAGER, indent=2))

for name, mime, payload in TECHNIQUES:
    if name in {"html_style_tag", "html_inline_script", "html_onclick",
                "html_iframe_srcdoc", "javascript_mime"}:
        display({"text/html": f"<hr><b>{name}</b>, second time"}, raw=True)
        publish_display_data({mime: payload})

# %% [markdown]
# ## What you saw
#
# Twelve questions. Answer from the first output block, before the widget manager was
# enabled, because that is what a reader gets. The block after it is the comparison and
# gets one question of its own at the end.

# %%
def verdict(name):
    print(f"\n{name}\n  look for: {LOOK_FOR.get(name, '')}")
    for key, meaning in SCALE.items():
        print(f"  {key:<7} {meaning}")
    while True:
        answer = input("  verdict: ").strip().lower()
        if answer in SCALE:
            return answer
        print("  one of:", ", ".join(SCALE))


LIVE = {name: verdict(name) for name, _, _ in TECHNIQUES}
print(json.dumps(LIVE, indent=2))

# %%
AFTER_WIDGET_MANAGER = input(
    "Did enabling the widget manager change any of the five it redisplayed? "
    "(name them, or type none) ").strip()
JS_FRAME = input(
    "What did the JavaScript check print? (paste the line) ").strip()

# %% [markdown]
# ## The reader who has not run anything
#
# This is the column that decided the design in JupyterLab, where four of twelve
# survived, and it is the one a reader hits first: they click a link and read a notebook
# somebody else ran. In Colab that means saved output, reloaded, with no kernel.
#
# Do this now, in order.
#
# 1. File, then Save a copy in Drive. The copy keeps the outputs.
# 2. In the copy, Runtime, then Disconnect and delete runtime.
# 3. Reload the page and do not run anything. Scroll to the twelve payloads.
#
# Then come back here, reconnect, run this notebook from the top again, and answer the
# four questions below from what you saw on the reloaded page. Four rather than twelve,
# because these are the four the sanitizer in JupyterLab treated differently and
# therefore the four that decide anything.

# %%
UNRUN = {
    "outputs_shown_at_all": input(
        "Were the saved outputs visible at all with no kernel? [y/n/skip] "
    ).strip().lower(),
    "html_style_tag": input("Was 'a style tag' still green? [y/n/skip] ").strip().lower(),
    "html_inline_script": input(
        "Did it say 'script ran'? [y/n/skip] ").strip().lower(),
    "html_onclick": input(
        "Did the button still change on click? [y/n/skip] ").strip().lower(),
    "html_iframe_srcdoc": input(
        "Was the iframe still there? [y/n/skip] ").strip().lower(),
}
print(json.dumps(UNRUN, indent=2))

# %% [markdown]
# ## The result
#
# Copy everything between the two rule lines and paste it back. It becomes the Colab
# column of the widget delivery grid.

# %%
RESULT = {
    "probe": "widgets",
    "issue": 4,
    "platform": "colab-free",
    "measured": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
    "front_end": "colab",
    "kernel_used": "python3, because the question is about the front end",
    "browser": platform.platform(),
    "widget_manager": WIDGET_MANAGER,
    "colab_live": LIVE,
    "colab_unrun": UNRUN,
    "after_widget_manager": AFTER_WIDGET_MANAGER,
    "javascript_frame_check": JS_FRAME,
    "scale": SCALE,
    "techniques": [name for name, _, _ in TECHNIQUES],
}

print("-" * 72)
print(json.dumps(RESULT, indent=1, sort_keys=True))
print("-" * 72)
