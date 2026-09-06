# Five probes that need a machine this project does not have

Every other probe in M0 was run and its numbers are committed. These five were not, and the reason in each case is the same shape: the measurement needs something no automated pipeline here can supply. Four of them need a browser signed in to a Google account, because the environment being measured is Google Colab and there is no way to ask Colab a question except by opening it. The fifth needs hardware that is doing nothing else, and every machine available to this project is shared.

The options were to guess, to leave the issues open with nothing behind them, or to write the measurement down as something a person can run. This is the third one. Each probe below is a notebook or a script, committed, that produces a JSON block at the end. Paste that block back and it becomes a results file and then a report, the same way every other probe in this milestone did.

Nothing here needs you to know what it is measuring. Open it, run every cell top to bottom, answer the handful of questions that need a pair of eyes, and copy the block between the two rule lines.

## What to run, in what order

The order matters for exactly one reason: the first one answers whether the notebook format works at all, and if it does not then the format changes and two of the others get rewritten before they are worth running.

| | probe | issue | where | how long |
|---|---|---|---|---|
| 1 | the bootstrap | [#1](https://github.com/tamnd/jvm-internals/issues/1) | Colab, free tier | 5 minutes, three times |
| 2 | the capability matrix | [#11](https://github.com/tamnd/jvm-internals/issues/11) | Colab, free tier | 5 minutes |
| 3 | the widgets | [#4](https://github.com/tamnd/jvm-internals/issues/4) | Colab, free tier | 10 minutes |
| 4 | the build | [#6](https://github.com/tamnd/jvm-internals/issues/6) | Colab, free tier | up to 2 hours, twice |
| 5 | jcstress | [#9](https://github.com/tamnd/jvm-internals/issues/9) | a quiet machine | 10 minutes |

The four Colab notebooks are built from readable sources by `tools/gen_handoff.py` and checked in CI, so the notebook and the source cannot drift apart. Edit the source, never the notebook.

Every one of them starts the same way and it is not a formality: **Runtime, then Disconnect and delete runtime, then reconnect.** A warm runtime with half the work already done measures nothing, because the whole question in three of the four is what a cold start costs.

## 1. Can a cold runtime become a pinned JDK 27 kernel in 90 seconds

Open [`probes/bootstrap/colab.ipynb`](../../probes/bootstrap/colab.ipynb) in Colab and run it. [Issue #1](https://github.com/tamnd/jvm-internals/issues/1).

This is the one everything else rests on. Every lesson opens with a bootstrap cell, and the promise that a reader needs no install is exactly the promise that this cell works on a free runtime. Under 40 seconds is fine, over 90 seconds and beginners start skipping lessons, and the notebook decides which of those happened rather than leaving it to be argued about afterwards.

It measures two routes. The documented one is JBang installing the `jupyter-java` kernel and asking for Java 27. The fallback is fetching the pinned tarball, hash checked against `docs/pin.json`, and registering a kernel against that. Both are timed, because if the first resolves some other build of 27 then the second is the cost a reader actually pays.

Three questions at the end need you rather than the notebook. Whether Runtime, then Change runtime type lists Java at all. Whether a Java cell then prints 2. Whether deleting the runtime and running the whole thing again comes back clean rather than half installed. Answer them from what the browser shows, not from what you expect.

Run it three times, in three fresh runtimes, changing `ATTEMPT` at the top each time. The issue asks for ten and three is what fits in one sitting, which is a deviation the report will state rather than hide.

## 2. What a free runtime can actually do

Open [`probes/capability/colab.ipynb`](../../probes/capability/colab.ipynb) in Colab and run it. [Issue #11](https://github.com/tamnd/jvm-internals/issues/11).

Nothing new is measured here. `probes/capability/run.py` already asks 119 questions of a JDK and the box under it, and [the matrix](../generated/capability-matrix.md) has answers from four machines. Colab is the environment every lesson declares as E0 and the only one nobody has asked, which makes the empty column the one that matters most.

There is nothing to watch. It clones the project, fetches the pinned JDK, runs the existing probe, and prints the answers. Two questions at the end ask which runtime type you got and whether you are on the free tier, because the answers change with both.

## 3. Do the widgets survive the output sandbox

Open [`probes/widgets/colab.ipynb`](../../probes/widgets/colab.ipynb) in Colab and run it. [Issue #4](https://github.com/tamnd/jvm-internals/issues/4).

Twelve payloads, already measured in four other places and recorded in [the widget delivery grid](../generated/widget-delivery.md). Colab is the fifth place and the one that decides how every widget in the project gets built, because the prediction gate appears in every lesson and a gate that renders as a static picture is not a gate.

This one needs your eyes throughout. The notebook shows the twelve payloads, tells you what each one looks like when it works, and then asks you what you saw. Click the details arrow, click the button, select the radio. Answer from the first block of output, before the widget manager is enabled, because that is what a reader gets.

There is a second half that costs you the session and is worth it. Save a copy to Drive, delete the runtime, reload the page, and look at the saved output with no kernel running. That is a reader who clicked a link and has not run anything, and in JupyterLab only four of the twelve survived it. Then come back, rerun, and answer the four questions about what you saw.

## 4. Does an OpenJDK build fit in a free session

Open [`probes/colabbuild/colab.ipynb`](../../probes/colabbuild/colab.ipynb) in Colab and run it. [Issue #6](https://github.com/tamnd/jvm-internals/issues/6).

This is the long one and the one with the most riding on it. X09 and much of the third pass assume a reader can compile HotSpot themselves. The comparable project in this series got a flat no to the same question. If the answer here is also no, a lesson changes shape and a claim comes out of the README, and both are much cheaper to do now than after twelve lessons depend on it.

Set `CONFIG` at the top, run it, and leave the tab open and in front of you. Colab reclaims idle runtimes, and a build that died because you went to another tab for forty minutes has measured your browsing. The notebook samples disk and memory once a minute while `make` runs and stops at a two hour cap, so a build that does not finish still produces an answer about what ran out and when.

Run it twice in two fresh runtimes: once with `CONFIG = "release"`, which is what the threshold is about, and once with `CONFIG = "slowdebug"`, which is what would let a reader use the assertions and the develop flags. The likely answer is that the first fits and the second does not, and that is a fine result as long as the lesson says so.

## 5. Does jcstress produce meaningful outcomes

Run [`probes/jcstress/run.py`](../../probes/jcstress/run.py) on a machine that is doing nothing else. [Issue #9](https://github.com/tamnd/jvm-internals/issues/9).

```
python probes/jcstress/run.py --label quiet-x64
```

It needs a JDK and four jars, and the jars come off Maven Central on the first run, are checked against the hashes in the script, and are cached. Add `--jdk` if `JAVA_HOME` is not the pinned build. The default `quick` mode is a few minutes and `--mode default` is the one worth an evening.

The whole concurrency part is graded by jcstress and the Race Playground publishes its outcome tables. On a shared runner the interesting interleavings do not appear, and they do not fail to appear at random: there is systematically no second core free to race on. Publishing "we never observed this reordering" when the reason is the runner rather than the memory model would be worse than publishing nothing.

So this is not a pass or fail run. It counts how often each interesting outcome appeared, next to the core count and the load average of the machine it appeared on. Run it on x86-64 and on aarch64 if you have both, with a different `--label` each time, because the two architectures are the point: a reordering that is common on one and unobservable on the other is the lesson. Run it on a busy machine too if you have one, with a label that says so, because the comparison the issue asks for needs both halves.

The five tests are in `probes/jcstress/tests`, starting with [the Dekker one](../../probes/jcstress/tests/jvx/jcstress/StoreBuffering.java), rather than taken from jcstress itself, because the uber jar of jcstress's own corpus is not published to Maven Central and building it needs a Maven build of jcstress from source. Three of the five carry an interesting outcome and two are controls whose forbidden outcome must never fire, which is what makes a run of all zeroes distinguishable from a run that did nothing.

It writes a results file, a console log and jcstress's own binary result blob, all three next to each other. Keep all three. The blob can be re-parsed with `-p` without occupying the machine for another hour, which is the only recovery available if the console parse comes back thin.

The harness itself is not in question. It was compiled and run in `sanity` mode on the pinned JDK 27 on osx-arm64 while this was written, so a failure on your machine is a fact about your machine rather than about jcstress 0.16 being three years older than the JDK it is running on. What was not run is any real measurement, because every machine available here was loaded, which is the whole reason this page exists.

## What to paste back

The last cell of each notebook prints a block between two lines of dashes. Copy the whole block, dashes not included, and paste it back. For the jcstress script the file is written for you at `probes/jcstress/results/<label>.json`.

That block is the deliverable and a screenshot is not a substitute, because the reports in this project are generated from the numbers rather than written around them. A run that failed is still worth pasting: a failed step is a result, and every notebook here keeps going after one so that the failure arrives with its output attached.
