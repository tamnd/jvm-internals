# Contributing

The whole point of this project is that a reader can check everything, so most of the rules below exist to keep that true. Read them before writing a lesson, because a pull request that ignores them gets sent back and that wastes your afternoon as much as ours.

## The pin

Everything is written against one JDK tag and one specification edition, both recorded in `docs/pin.json`. Nothing anywhere else names a version.

The tag is `jdk-27+35`. It is the release candidate, it is the newest tag in the 27 line on `openjdk/jdk`, and the GA binaries published at jdk.java.net report the same commit it resolves to. The pin is a tag that exists rather than a name for a stage, because a citation is only checkable if the thing it names can be fetched. When `jdk-27-ga` is tagged the pin moves to it, and the first task of that move is confirming the two tags point at the same commit.

Getting that JDK onto your machine is one command, and it is the same command CI runs.

```
export JAVA_HOME=$(python tools/fetch_jdk.py)
```

It reads the URL and the SHA256 out of `docs/pin.json`, checks the download against the hash before unpacking anything, installs into `~/.cache/jvx` and prints the path. A hash that does not match is a hard stop with nothing unpacked. Running it again when the right build is already there does nothing and still prints the path, so it is safe in a script. Use `--dir` to put it somewhere else and `--where` to ask where it would go without installing.

Citations come in two forms and CI resolves both.

```
src/hotspot/share/oops/markWord.hpp:48@jdk-27+35
JVMS §5.4.3.1@SE25
```

A source citation is resolved against the pinned tree and the surrounding lines are hashed, so a citation that still points at a line whose content changed is caught. That is the failure mode that matters, because a line number that drifts is obvious and a line number that still exists and now says something different is not. A specification citation is resolved against a stored section index and the quoted fragment has to appear in the section, which catches the most common error in JVM writing: attributing a claim to the specification that the specification does not make.

The specification edition is `SE25`, and it does not describe the class files the pinned `javac` writes. That is measured rather than assumed. The pinned compiler writes class file major version 71, JVMS SE25 covers 45 through 69, and the newest published edition, SE26 from March 2026, covers 45 through 70. No published edition describes a JDK 27 class file, because that edition is published with JDK 27 itself. So the pin carries two numbers, `jdk_class_file_major` and `jvms_class_file_major`, to keep the gap visible instead of averaging it into one field, and a lesson that reads the four bytes after the magic number cannot yet cite an edition that lists what it finds there. [Issue #34](https://github.com/tamnd/jvm-internals/issues/34) tracks the move, which happens with the pin move to `jdk-27-ga` rather than before it, because the edition to move to does not exist yet. The one specification claim in the repository today is JVMS §2.7 on the representation of objects, and its number, its title and the sentence quoted from it are the same in SE25 and in SE26, which is the available evidence that the move will be a re resolve rather than a rewrite.

## Rule 7, the one that is specific to this project

Every claim carries a marker. Either `[JVMS]` with the section, or `[HOTSPOT]` with the source line. There is no third option and there is no unmarked claim.

```markdown
The class must be initialized before its first static field access {[JVMS §5.5]}.
The initialization lock is per class and is implemented as a recursive monitor on the mirror {[HOTSPOT src/hotspot/share/oops/instanceKlass.cpp:1084@jdk-27+35]}.
```

Three rules about the markers. When in doubt it is `[HOTSPOT]`, because claiming something is guaranteed when it is not is the failure that harms readers, and claiming something is an implementation detail when it is actually specified merely makes them cautious. A `[JVMS]` marker names the section and not the chapter, so `[JVMS §5.4.3.1]` and never `[JVMS §5]`. And where the two disagree in an interesting way, that disagreement is the lesson, because the best paragraphs in this project are the ones that say the specification permits this, HotSpot does that, OpenJ9 does something else, and here is why it matters to you.

A claim marked `[JVMS]` whose section does not say what the claim says is a P1 bug, treated more seriously than a wrong number, because it is the one error that will cause somebody to write production code on a guarantee that does not exist.

## Lessons are Python, notebooks are build output

You edit `lessons/<id>/lesson.py`, which is percent format Python with one cell per `# %%`. `build.py` turns it into `notebooks/<id>/lesson.ipynb`. The notebook is committed, because Colab can only open a file that exists at a URL, but it is generated and it is marked as generated so it never shows up in a diff.

This means two things in practice. Notebook JSON is never reviewed, so review stays sane. And hidden execution state is unrepresentable, because cells are emitted in source order with no execution counts.

```
python tools/build.py new O02     # scaffold
python tools/build.py notebooks   # regenerate
python tools/build.py check       # what CI runs
python tools/build.py list        # the lesson index
python tools/build.py run O01     # execute, if a Jupyter runtime is here
```

Prototype in Colab as much as you like, that is where you notice the bug, then port the fix back into the `.py`. Editing a generated notebook loses the change on the next build and `build.py check` catches you either way, by name: it tells you which cell differs and whether its id moved.

`docs/lesson-format.md` is the full reference for the front matter, the directives and the tags. Install the hook with `git config core.hooksPath tools/hooks` and you find out about an out of sync notebook in a second rather than six minutes later from a red check.

One rule about generated output is worth stating here rather than only in the format document. A cell tagged `bake` reads its output from `lessons/<id>/baked/<cell-id>.json`, recorded by CI on the pinned JDK. When that recording does not exist, the notebook says so in the cell. Nobody writes a plausible looking output by hand to fill the gap. A project whose first rule is that nothing is asserted the reader cannot watch happen does not get to invent the thing they were supposed to watch.

## The JShell trap

The kernel is JShell, which wraps every snippet in a synthetic class with a generated name. Those classes are real. They get loaded, linked, initialized and compiled, they appear in class histograms and heap dumps, and they show up in JFR recordings and in `-XX:+PrintCompilation` output.

So any observation that a synthetic class would contaminate is taken in a `jvx.run` subprocess with a declared flag set, and never in the kernel. That covers class histograms, compilation logs, JFR recordings, heap dumps and anything derived from them. The kernel is for analysis of the results. Taking one of those measurements in the kernel is a review finding, not a style preference.

Three more things about the kernel that are not JShell's doing and cost an afternoon each. They are enforced by `tools/test_jvx_ui.py` rather than remembered.

**The kernel imports less than a terminal does.** A `jshell` you start yourself imports `java.nio.file.*`, `java.util.stream.*` and `java.util.function.*`. JJava sets its own list and those three are not on it. A helper that names `Path` loads perfectly in a terminal and fails in the kernel, and the error a reader sees is `cannot find symbol: variable jvx`, which points at the last snippet in the cell rather than the one that broke. Import explicitly in `jvx/00-imports.jsh`.

**No `System.out.printf` in anything the kernel runs.** The kernel turns every write on `System.out` into its own stream message and `java.util.Formatter` writes each padding space separately, so one `printf("%-28s...")` reaches the reader as thirty messages and the notebook puts each on its own line. `String.format` first, then `println`. Inside a `jvx.run` text block it is fine, because that output is captured from a subprocess and printed in one go.

**Only `jvx` calls `display`.** It returns the id it assigned, JShell prints the value of the last expression, and the reader gets a line of hex under every widget. `Ui.html` swallows it and is the only caller.

## Probes

A probe is a script under `probes/` that answers one question by measuring rather than by reasoning, writes a JSON file per machine into `probes/<name>/results/`, and gets read by a `tools/gen_*.py` that turns those files into a table and a picture. The report in `docs/probes/` is the prose. Nothing in that chain is written by hand twice, which is why a number in a report cannot drift away from the measurement that produced it.

Most probes need only the pinned JDK. The widget one needs a Jupyter stack and a headless browser as well, because it asks what a front end does to your output and that cannot be answered by reading a file.

```
python -m venv .venv
.venv/bin/pip install jupyterlab nbclient nbconvert jjava playwright
.venv/bin/playwright install chromium
```

`jjava` is the kernel, a JShell behind the Jupyter protocol, and installing the wheel registers it as `java` with no further setup. Add `--with-deps` to the playwright line on a fresh Linux box, and drop it again if your apt sources are in a state that makes it fail, because the headless shell runs without them often enough to try.

Probes are not in CI. Their generators and the generators' tests are, so a stale table or a picture that disagrees with the results is caught on every push, and the measurement itself is rerun by a person on a named machine and committed with the date on it.

## Prose rules

These are checked by `tools/prosecheck.py` where they can be, and by a human where they cannot.

**No em dashes.** Use a comma, a full stop, or brackets. The checker rejects them.

**One paragraph is one line.** A sentence never gets broken across two lines, because a hard wrapped paragraph produces a diff where one word change rewraps six lines and the review becomes unreadable.

**No "simply", "just", "obviously", "of course" or "trivially".** <!-- prose-ok --> Somebody will find every single thing in this material hard and each of those words is a small insult to them.

**Never write a number from memory.** If a paragraph says the default G1 region size is some value, that value is interpolated from generated JSON rather than typed.

**Never present a timing, a size or an address as a fact when it is a specimen.** Write that on the machine that produced this page the call took 1.4 ms, and never that the call takes 1.4 ms.

**Date anything that is in motion.** Any claim about what is default, what is experimental, what is deprecated and what is in progress carries a date and the command that establishes it. The JVMCI removal is the worked example: a lesson that says as of September 2026 this is unresolved, and here is who objected and why, stays useful when the situation changes. A lesson hedged into surviving any outcome does not.

**Be exact about names.** `InstanceKlass` is not `instanceKlass.cpp` is not an instance class. Getting it wrong costs a reader an hour when they try to grep.

**Never quote a generated file as if it were source.** Show `ad_x86.cpp` next to the `.ad` rule it came from and say which is which. Same for the JVMTI header and `jvmti.xml`. This is the single most common way JVM material confuses people.

**Use "they" for people** whose pronouns you do not know.

## Limits

| | Cap |
|---|---|
| Hook | 150 words |
| Tour | 1500 words |
| Whole lesson prose | 2500 words |
| Animation | 90 seconds |
| Cells in the E0 experiment | 24 |
| New flags introduced | 3 |
| New terms defined | 4 |
| Blueprint | no cap |

A lesson that wants to be longer is two lessons. There is no exception for the interesting ones, because "this one is special" is how caps die.

## Claims

Every behavioural claim in a lesson is an entry in `lessons/<id>/CLAIMS.md` with a marker, an evidence pointer, a citation, a build configuration and a date. A claim without a configuration is a claim about nothing, because almost everything in this material is configuration dependent. A claim not re verified against the pin within ninety days is marked stale on the site automatically.

At most two claims per lesson may be `unobservable`, and each of those states its reason. Everything else names a cell id whose output supports it. Give an id to any cell a claim depends on and then never change it, because renaming one silently breaks the ledger entry.

## Baked cells

Almost every observation here is nondeterministic. Timings, addresses, thread interleavings and the moment the JIT decides to compile are not stable and never will be.

So nondeterminism is declared rather than discovered. A cell whose output cannot be stable is tagged `bake`, its output is a recorded canonical run, and the page carries a visible marker saying so. This project has more baked cells than any other in the series and hiding that would be a lie.

## Benchmarks

Every number carries its JVM version and build, its flags, its hardware, and its JMH parameters including forks and iterations. Warmup is never skipped and never assumed, and a benchmark that does not show its warmup curve is not published, because the whole point of the JIT part is that the first ten thousand iterations are a different program. No number is compared across hardware. A speedup claim states its baseline and the baseline is reproducible.

## Boss fights

Every boss fight has a grader at `lessons/<id>/grade.py` that exits 0 or 1. The failure message names the input. "Your class file's stack map frame at offset 14 declares int where the verifier expects Object" teaches something. "Incorrect" does not.

Where an external oracle exists, use it. A boss fight graded by jcstress, by the class file fuzzer, by `-XX:+VerifyBeforeGC` or by a jtreg test is worth more than one graded by a bespoke script, because the reader can keep using the oracle afterwards.

## jcstress

If a lesson uses jcstress, it says what the hardware was, because outcome distributions are a property of the machine as much as of the code. A forbidden outcome is a bug in the JVM or in your understanding, and the lesson has to say which one it thinks it is. Never describe a set of observed outcomes as the set of possible outcomes.

## Review

Every lesson needs two reviewers, one at or below the target reader's level and one at or above the level of somebody who has contributed to OpenJDK.

The beginner reviewer answers: where did you get lost, what word was used before it was defined, what did you have to read twice, did the cells run first time, and could you do the boss fight.

The expert reviewer answers: is anything wrong, is anything true of an older JDK, is any citation misleading, is any claim marked `[JVMS]` that is actually HotSpot's choice, is the measurement methodology sound, and does the Blueprint actually specify the thing.

The beginner review is the one that gets skipped under deadline pressure and it must not be. The site says on each page which lessons have had an expert review and which have not, because a reader deserves to know when they are reading something one person checked twice.

## Licence

By contributing you agree that code is licensed Apache-2.0 and content is CC BY 4.0. Anything derived from OpenJDK source is GPLv2 with the Classpath Exception, lives in `patches/` or `vendor/`, and carries a header saying so. A GPL header anywhere else fails CI.
