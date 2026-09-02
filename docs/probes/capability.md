# What a machine can actually do, asked rather than assumed

A lesson that says `env: E0` is making a claim about a computer somebody else owns. This is the thing that checks the claim. It asks 119 questions of a JDK and the box under it, and writes the answers to a file with a date and a platform on it.

The full table is [the capability matrix](../generated/capability-matrix.md), which is generated from the results and cannot drift from them. This page is the part worth reading: what was measured, what came back surprising, and which open issues now have an answer.

## Where it ran

Four environments, all on the pinned java 27+35-2325, all measured on 2026-09-02.

| name | platform | cores | notes |
|---|---|---|---|
| `osx-arm64` | darwin-arm64 | 10 | an ordinary user account |
| `linux-x64-root` | linux-x86_64 | 8 | root |
| `linux-x64-user` | linux-x86_64 | 4 | an ordinary user, `ptrace_scope` at 1 |
| `win-x64` | windows-amd64 | 32 | Windows 11, a real shell and not WSL |

A fifth machine, a second Linux box, gave the same answer as `linux-x64-root` on every capability check and is not published separately because a duplicate column teaches nothing. The two Linux rows are here because they differ in the one way that changes answers, which is whether the account is root.

## The headline

**103 of the 119 checks give the same answer on all four.** Of the 16 that differ, every single one is either how big the machine is, which native tools somebody installed on it, how fast it is, or whether the account is privileged. Not one is a difference in what the JVM can do.

That is the answer to a question this project has been carrying since the first spec document: whether the curriculum has to fork by platform. On the evidence here it does not. A lesson that works on a Mac works on Linux and on Windows, and the exceptions are things a lesson can ask about at runtime rather than things an author has to guess.

The three checks where the platforms genuinely disagree about the JVM are all the same fact from three angles: Linux has three diagnostic commands the other two do not, `Compiler.perfmap`, `System.native_heap_info` and `System.trim_native_heap`, which is why the dcmd count is 54 there and 51 elsewhere. All three are about the operating system rather than about the runtime.

## What came back no everywhere

These eight are worth naming because each of them is somebody's assumption.

**`PrintFieldLayout` does not exist.** Not on any of the four. It is a develop flag, so it is compiled out of a product build, and every JDK anybody downloads is a product build. This is the blocker on issue #5, which wanted to check JOL's answer against HotSpot's own printout of the same layout, and it is now measured rather than suspected. That comparison needs a debug build, which means it is an E1 exercise or it does not happen.

**There is no disassembler.** `-XX:+PrintAssembly` is accepted, the VM turns on `DebugNonSafepoints`, prints an nmethod header for every compile, and then says `Loading hsdis library failed` and prints no instructions. That is issue #8 and it is the same on all four platforms. Worth knowing precisely: the flag working and the disassembly appearing are two different things, and a probe that only checked the exit status would have reported success.

**Shenandoah is not in these builds.** ZGC, G1, Parallel, Serial and Epsilon all start. `-XX:+UseShenandoahGC` is rejected on all four, because the GA binaries from jdk.java.net are not built with it. A garbage collection lesson that wants Shenandoah needs a different JDK, and should say so rather than letting the reader discover it.

**`GC.class_stats` is gone.** Confirmed on four platforms now, which was previously a one machine finding from the JShell noise probe.

**`GC.heap_dump` is not an MBean operation.** It is in `jcmd`, but it is not published on the DiagnosticCommand MBean, so a program that wants to dump its own heap has to go through `HotSpotDiagnosticMXBean.dumpHeap` instead. There is no warning, only a `ReflectionException` that names nothing useful.

**You cannot attach to yourself.** `com.sun.tools.attach.VirtualMachine.attach` on your own pid fails on all four, and it fails as an `IOException` rather than as anything that explains itself. It needs `-Djdk.attach.allowAttachSelf=true`, which has to be on the command line, which means a notebook cell cannot turn it on after the fact.

**java.base is closed.** Reflecting into `java.lang` fails everywhere by default, which is the documented behaviour since 16. The useful half is the other check: `--add-opens java.base/java.lang=ALL-UNNAMED` works on all four, so it is closed rather than forbidden. A lesson that needs it can have it, in a subprocess, and cannot have it in a kernel that was started without it.

**The Vector API is not loaded.** Present in the image on all four, and not on the module graph unless the run asks for `--add-modules jdk.incubator.vector`. Which is exactly what an incubating module is supposed to do, and is the sort of thing a lesson forgets until a reader hits it.

## Two things that changed a committed file

**The class file version in `docs/pin.json` was wrong.** It said 69. The pinned `javac` writes 71, on all four platforms, which the probe measured and `javap` confirms. 69 is what JVMS SE25 describes.

The fix is two fields instead of one, `jdk_class_file_major` at 71 and `jvms_class_file_major` at 69, because a project whose entire subject is the difference between what the specification mandates and what the implementation does cannot have a single ambiguous field standing for both. There is now a test that reads the number out of the measurement and asserts the pin file agrees with it, so this cannot drift again quietly.

Underneath that is something bigger, and it is not fixed here. This repository cites the JVMS at edition SE25 and ships a JDK 27. A lesson that opens a class file written by the pinned compiler and reads major version 71 is looking at a class file that the edition it cites does not describe, because SE25 stops at 69. That is a real editorial gap and it is filed as its own issue rather than papered over in a probe report.

**`UseCompressedClassPointers` was removed in 27.0.** Passing it does not fail. The VM starts, prints `Ignoring option UseCompressedClassPointers; support was removed in 27.0`, and exits 0. Anything that checks a flag by running the VM and looking at the exit status will believe the flag worked. The probe looks for that warning specifically, which is why the matrix says `removed` rather than yes. `jvx` already knew about this one, which is a good sign for the rest of it.

## Attaching to a process, which is where privilege bites

Every serviceability tool is the same mechanism wearing a different hat, so this is one question with four answers.

| | `jcmd` | `jhsdb` |
|---|---|---|
| osx-arm64, ordinary user | works | refused |
| linux, root | works | works |
| linux, ordinary user, `ptrace_scope` 1 | works | refused |
| win-x64 | works | works |

`jcmd` works everywhere because it asks the target politely over a socket and the target answers. `jhsdb` stops the target and reads its memory, so it needs strictly more permission, and it is refused in two of the four with two different reasons: `ptrace(PTRACE_ATTACH, ..) failed: Operation not permitted` on Linux, and `Can't attach to the process. Could be caused by an incorrect pid or lack of privileges` on macOS.

This matters for issue #3, which wants `bpc` to pull struct layouts out of the Serviceability Agent's type database. On the evidence here that is a root or a Windows activity, or it needs the target to be a child of the tool. It is not something a reader on a Mac laptop can do without extra work, and any lesson that assumed otherwise needs rewriting.

## Timings, which are the other reason to measure rather than assume

JShell cold start, from launching the binary to a snippet having printed:

| | default provider | `--execution local` |
|---|---|---|
| osx-arm64 | 0.9s | 0.5s |
| win-x64 | 8.8s | 0.5s |
| linux-x64-user | 8.2s | 4.0s |
| linux-x64-root | 17.9s | 15.8s |

The slowest of these was measured three more times by hand and came back at 18.1, 21.9 and 20.9 seconds, so it is not a bad first run. The same command on the same JDK is twenty times slower on one ordinary machine than on another.

Issue #1 gives the Colab bootstrap a budget of 90 seconds cold, and a good part of that budget can go on starting JShell once. Flight Recorder is the same story in miniature: 16 milliseconds to start a recording on two of the four and around 250 on the other two. The JShell noise probe already ran into this from the other end, when a slow machine missed the default JDI handshake deadline and JShell reported that every execution provider had failed.

## What works everywhere and can be relied on

Worth stating positively, because a page of noes reads worse than the situation is. All four environments run the source launcher, both JShell providers, a `-javaagent` built from scratch with a real manifest, a JVMTI agent, Flight Recorder, and `jcmd` against another process. All sixteen JDK tools are present, all thirteen modules are in the image, and the class file API, the FFM linker, `ScopedValue` and `StructuredTaskScope` all load without a flag. Compact object headers are on by default and `ObjectAlignmentInBytes` is 8 on all four, which is the assumption lesson O01 rests on and it now has four platforms under it instead of two.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. Nothing here needs the network and nothing needs root, though two checks will answer differently if you have it.

```
python probes/capability/run.py --out probes/capability/results/mymachine.json
python tools/gen_capability_matrix.py
```

It takes about eight seconds on a fast machine and about a minute on a slow one, and it starts roughly thirty short JVMs to do it.

## What is not answered here

The column that matters most to the E0 tier, a free Colab runtime, is missing, because it cannot be measured over SSH. Getting the probe onto Colab and back is part of issue #1, and it is the reason issue #11 stays open with the rest of its checklist ticked. When that column lands it goes into the same table, next to these four, and the comparison is one column read rather than an argument.
