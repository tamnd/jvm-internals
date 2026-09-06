# Fifty three flags say they are at their default and are not

M2's second gate says BP-FLAGS is generated from `globals.hpp` with types, defaults and origins. Types and defaults are in the header and can be parsed. Origins are not in any header, because an origin is a fact about a VM that has already started, so this needed two sources and a join. The tables are [BP-FLAGS, every flag a product JVM has and who set it](../generated/flags.md). This page is the reading.

The first source is the 22 HotSpot globals headers at `jdk-27+35`, which declare 1,262 flags between them. The second is a JVM, run about a dozen times with different options and asked each time what flags it has: `probes/vmflags/run.py` on macOS arm64 and inside a Linux container, both running build 27+35-2325 on the same processor architecture. The join is the point. A header says what a flag is meant to be and a running VM says what it is, and the interesting output of this probe is entirely in the gap between those two.

## The headline

**53 flags report origin `default` while holding a value their header does not declare.** 52 of them on Linux, and the two lists overlap almost exactly. `MaxGCPauseMillis` is declared as the largest `uintx` there is and reports 200. `UseCRC32` is declared `false` and reports `true`. `ParallelGCThreads` is declared 0 and reports 9 on a ten processor Mac and 4 in a four processor container. In all 53 cases `-XX:+PrintFlagsFinal` prints `{default}` next to the value, which is the VM saying that nothing overrode the source.

Something did override the source. HotSpot has several ways to set a flag from inside the VM and they do not all record that it happened.

```
#define FLAG_SET_DEFAULT(name, value) ((name) = (value))

#define FLAG_SET_ERGO(name, value)     (void)(FLAG_MEMBER_SETTER(name)((value), JVMFlagOrigin::ERGONOMIC))
```

Those are src/hotspot/share/runtime/globals_extension.hpp:82@jdk-27+35 and src/hotspot/share/runtime/globals_extension.hpp:90@jdk-27+35. The second one goes through the flag's setter and stamps an origin, which is why 32 flags on this Mac say `ergonomic`. The first one is an assignment to a global variable. It changes the value and there is nowhere in it for an origin to be written, so the flag keeps saying `default` because as far as the bookkeeping is concerned nothing was set.

Both are ordinary and both are used deliberately. What makes the result confusing is the guard that usually precedes the assignment:

```
  if (FLAG_IS_DEFAULT(AllocatePrefetchDistance))
    FLAG_SET_DEFAULT(AllocatePrefetchDistance, MIN2(512, 3*dcache_line));
```

That is src/hotspot/cpu/aarch64/vm_version_aarch64.cpp:125@jdk-27+35, and the question it asks is still answered yes after the line below it runs. The idiom reads as "if the user did not ask for this, pick one", and it is correct for that purpose, because a second pass would pick the same value. The cost is that the flag it wrote now reports a machine specific number under a label that means "from the source". That one file uses `FLAG_SET_DEFAULT` 80 times and `FLAG_SET_ERGO` not once, which is where most of the 53 come from. src/hotspot/share/runtime/arguments.cpp:3508@jdk-27+35 does the same thing for `UseSecondarySupersTable` in a file that uses `FLAG_SET_DEFAULT` 18 times and `FLAG_SET_ERGO` 10 times, so the choice is per call site rather than per file.

The practical form of this is short. `-XX:+PrintFlagsFinal` will tell you a flag's value and it will tell you whether you set it. It will not reliably tell you whether the VM set it, and 53 flags out of 805 is too many to treat as an edge case when someone is reading that output to work out why two machines behave differently.

## A flag the VM lists, documents and refuses

`-XX:+UseShenandoahGC` exits 1 with `Option -XX:+UseShenandoahGC not supported`. The same VM lists `UseShenandoahGC` in `-XX:+PrintFlagsFinal` as a `product bool` with a default of `false` and a documentation string about running the collector.

Both of those are true because the flag and the collector live in different headers. `UseShenandoahGC` is declared in the shared collector header that every build compiles, and the 75 `Shenandoah*` tuning flags are declared in the Shenandoah header that this build did not, which is why they are missing from the reported table and it is not. A build can therefore advertise a switch for a component it does not contain, and the only way to find out is to try it.

That is one of seven refusals the probe measures, and they are seven different sentences. A develop flag, a diagnostic flag, an experimental flag, a name that does not exist, a value outside a declared range and a value inside the range that a constraint function rejects all produce distinct messages with distinct amounts of help in them. The range refusal prints the range. The constraint refusal (`ObjectAlignmentInBytes (9) must be power of 2`) does not print the constraint, because a constraint is a C++ function and there is nothing to print. Unlocking is about none of this: the VM reports the same 805 flags with and without both unlock options, so the gates control what you may set and never what you may see.

## A header is not a build

1,248 flags are declared for macOS and 805 are reported. Every one of the 443 that are declared and absent has a reason, and the generator refuses to run if any of them does not:

351 are `develop` flags, which a product build compiles out. 75 are the Shenandoah flags above. 9 are in the `#else` of `#ifndef ASSERT`, which is the debug only corner of `debug_globals.hpp`. 8 are inside `#if ALLOCATION_FAILURE_INJECTOR`. Nothing is left over, and in the other direction nothing is unexplained either: **zero reported flags are undeclared**, on both platforms.

That zero is the check that makes every other count on the page worth printing. Getting it took four attempts. Five headers left 153 flags reported by a VM that no header declared, which does not mean HotSpot invented them, it means the header list was wrong. Enumerating all 53 globals headers in the tree and picking the 22 that a macOS or Linux aarch64 build compiles took that to three, and the last three were `FlightRecorder`, `FlightRecorderOptions` and `StartFlightRecording`, which are wrapped in `JFR_ONLY(...)` and so are not at the start of a line where an anchored pattern was looking. The parser now finds a macro name anywhere and balances parentheses from there, which also handles the arguments that contain parentheses and commas inside string literals.

## One option is never one flag

`-Xint` is documented as running in interpreted mode. It moves 13 other flags, including `TieredCompilation`, `UseCompiler`, `UseOnStackReplacement`, `ProfileInterpreter`, `SegmentedCodeCache` and four code cache sizes, one of which drops from 251,674,624 to 50,331,648. `-XX:+UseZGC` moves 24. `-XX:ActiveProcessorCount=1` moves 9 on the Mac and 7 in the container, which is the same option producing different consequences on two machines because the arithmetic it feeds starts from a different processor count.

None of that is hidden. All of it is invisible unless you print the flags before and after, which is the argument for a page that prints them.

## Two platforms, one build, one cache line

Both environments run 27+35-2325 on aarch64 and they do not have the same flags. 804 are common, `StressWXHealing` is only on macOS, and 12 are only on Linux. That much is expected from the per platform headers.

11 flags say `default` on both and hold different values. Four of them come from a `define_pd_global` in a platform header, which is how a `product_pd` flag gets the default the shared header declined to give it, and the source can be pointed at: `ThreadStackSize` is 2048 at src/hotspot/os_cpu/bsd_aarch64/globals_bsd_aarch64.hpp:34@jdk-27+35 and 2040 at src/hotspot/os_cpu/linux_aarch64/globals_linux_aarch64.hpp:39@jdk-27+35.

The other seven are the headline again, and five of them are one number. `AllocatePrefetchDistance`, `PrefetchScanIntervalInBytes`, `PrefetchCopyIntervalInBytes` and `SoftwarePrefetchHintDistance` all report 384 on the Mac and 192 on Linux, and `AllocatePrefetchStepSize` reports 128 and 64. The code above sets four of them to `3*dcache_line` and one to `dcache_line`, so the two environments are reporting a 128 byte data cache line and a 64 byte one. Five flags, five differences, one measurement of the machine underneath, and the origin column says `default` for all ten values.

## A number that will not sit still

`SharedBaseAddress` reports a different value on every run of the identical command, at origin `default` every time. It is the address the class data sharing archive is asked to map at, and it is randomised.

This was going to be a defect. A probe that records one sample of that number produces a result file that differs from the last one for no reason, `--check` fails in CI on a Tuesday, and somebody spends an afternoon on it. So the probe runs the baseline three times, compares, records the names of any flags whose value moved, blanks their values, and excludes them from every scenario diff so that a randomised address does not show up as something `-Xmx256m` did. One flag qualifies today. The mechanism costs two extra JVM starts and it turns an instability into a measurement, which is the same trade the census page made with its two platform counts.

## What is checked rather than claimed

The generator stops rather than printing a table it cannot stand behind. It stops if a reported flag is undeclared, if a header and a VM disagree about a flag's type, if they disagree about whether it is diagnostic, experimental or manageable, if a flag is declared in two headers, if a kind word appears that is not in the known set, if a declared flag is absent with no reason from the list above, if a declared range is not the range the VM enforces, if the measured `java_build` is not the one in `docs/pin.json`, if a gate that should be refused is accepted, or if the two environments disagree about anything that is a property of the JDK rather than of the machine.

The range check is the one that says the header is being read the way a compiler reads it. 248 declared bounds on each platform are literal enough to evaluate and compare, and all 248 match. Getting there needed C++ integer division: `max_jint / 4` in a header is 536,870,911 and in Python it is 536,870,911.75, and six flags disagreed until the evaluator learned to truncate when the expression contains no decimal point. The 47 bounds it declines to evaluate are written as calls and platform conditionals, and declining is better than guessing, because a guess that happens to match would be indistinguishable from a check that passed.

## What this means for the lessons

This is the page a reader is sent to the first time a lesson says "and the VM decided that for you". Lessons B04 onward keep running into flags: the compiler ones, the collector ones and the code cache ones all shape what a reader is about to measure, and until now there was nowhere to point at that both listed them and said where the values came from.

It also settles how these lessons should teach `-XX:+PrintFlagsFinal`. Print it, read it, and do not trust the origin column beyond `command line`. That is a specific and checkable habit rather than general caution, and it has 53 named counterexamples behind it.

The M2 fuzzer gate wants a case where HotSpot's rejection message differs from what the specification names. That gate is about the class file verifier and this page is about the command line, but the shape is the same and this is a warm up in a place with much less code to read: seven refusals in the same VM, no two worded alike, and one of them for a flag the VM will list for you.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about twenty seconds, and it starts a JVM about a dozen times.

```
python probes/vmflags/run.py --out probes/vmflags/results/mymachine.json
python tools/gen_flags.py
```

There is no Java half. `-XX:+PrintFlagsFinal`, `-XX:+PrintFlagsRanges` and the exit code of a VM that refused to start are the whole measurement, so the probe is Python that runs `java` and parses two well defined line formats.

The generator reaches the network for the 22 headers at the pinned tag and hashes each one into the provenance table at the bottom of the generated page. `JVX_JDK_SRC` pointed at a local openjdk checkout takes them off the network, which is what the CI job does with its cache. `--check` regenerates and compares without writing, which is how CI notices that a header moved or that a result file was edited by hand.
