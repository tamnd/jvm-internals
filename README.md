# jvm-internals

A complete visual teardown of the JVM, taught from zero, where you run a real JDK 27 from a notebook cell and the notebook is running on the machine you are studying.

Pinned to `jdk-27+35`, the release candidate, moving to `jdk-27-ga` when that tag lands on 15 September 2026.

[![ci](https://github.com/tamnd/jvm-internals/actions/workflows/ci.yml/badge.svg)](https://github.com/tamnd/jvm-internals/actions/workflows/ci.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/tamnd/jvm-internals/badge)](https://scorecard.dev/viewer/?uri=github.com/tamnd/jvm-internals)

## Why this exists

There are five kinds of JVM material in the world and none of them get you from "I have typed `-Xmx4g` a thousand times" to "I can read a deoptimization trace, write a JVMTI agent, and open a pull request against `openjdk/jdk`".

The Java Virtual Machine Specification is a real normative document and it is excellent. Chapter 4 defines the class file byte for byte, Chapter 5 defines loading and linking as a state machine, Chapter 6 defines all 202 opcodes. It also describes an abstract machine that does not exist. There is no JIT in it, no garbage collector, no object header, no safepoint, no deoptimization, so a reader who masters it still cannot explain why their benchmark got faster after ten thousand iterations.

The books are good and they are behind. The only book that ever really taught HotSpot's execution engine has a second edition from 1999, which is before C2, before tiered compilation, and before every collector currently shipping. Shipilev's JVM Anatomy Quarks are the best writing about the JVM that exists, and they are deliberately short, deliberately non linear, mostly undated, and they stop where the interesting part starts. Conference talks and blog posts are enormous in volume, undated by convention, and overwhelmingly about G1 tuning flags. And then there is the source: about 1.7 million lines of C++ in `src/hotspot`, two compilers, five collectors, an interpreter that is generated at runtime as machine code, and tens of thousands of jtreg tests.

This project is the missing path. Every claim points at a line of HotSpot with a version tag on it, every behaviour is something you watch happen rather than something you are told, and every claim is marked as either something the specification requires or something HotSpot chose.

## What is different about this one

**A real JDK 27, in a notebook, in about thirty seconds, for free, and the notebook is running on the JVM you are studying.** Every lesson is a Jupyter notebook with an Open in Colab badge. Colab is an Ubuntu VM with root, not a sandbox, so you run the actual `java`, the actual `javac`, the actual `jcmd`. The kernel is JJava, a JShell backed Java kernel, so your REPL is a live HotSpot instance and `ManagementFactory`, JOL, `Thread.ofVirtual` and the whole `jdk.internal` surface apply to the very VM executing the cell. When a lesson says "watch this method get compiled", the method being compiled is the one you typed.

**Modifying the runtime is a beginner activity, and there are three supported ways to do it without building a JVM.** A `java.lang.instrument` agent is one Java file and about two seconds of `javac`, and it rewrites bytecode at class load time inside the real VM. `-XX:CompileCommand` lets you forbid an inline, force a compilation or print the assembly for one method without touching a compiler. A JVMTI agent is a shared library against a stable documented interface with hundreds of functions and events. All three are supported API rather than a private test module.

**Building OpenJDK fits inside a Colab session.** Roughly twenty five minutes with a boot JDK and no cache. That is the difference between "patch the template interpreter and watch your opcode run" being a lesson and being a wish, and it is the thing that the LLVM project in this series could not offer.

**The Class File API is standard and in the JDK.** JEP 484 finalised `java.lang.classfile` in JDK 24, so every bytecode lesson gets a machine checkable constructor as well as a reader. You do not merely disassemble `javac` output, you build a class file by hand in a cell, load it, run it, break it, and watch the verifier explain which rule you violated. No ASM, no ByteBuddy, no third party version skew.

**HotSpot ships a machine readable description of its own memory layout, and the tool that reads it.** `vmStructs.cpp` names every VM struct, every field, its type and its offset, exported so an external process can walk a live JVM or a core file. So the data structures section of every Blueprint is generated rather than transcribed. Alongside it, `globals.hpp` generates the flag reference, `bytecodes.cpp` generates the opcode tables, `jvmti.xml` generates the JVMTI reference, JFR metadata generates the event reference, and the `.ad` files generate the C2 instruction selection reference per architecture.

**There is a real specification, it is not ours, and separating it from the implementation is the whole point.** Every claim in every lesson and every clause in every Blueprint carries a marker: `[JVMS]` with a section reference, or `[HOTSPOT]` with a source citation. That distinction is the difference between "Java guarantees this" and "this happens to work on my laptop", and it is exactly the distinction that talks and blog posts never make.

**There is a machine checkable oracle for concurrency and it is OpenJDK's own.** jcstress runs a two actor test billions of times and reports the observed outcomes against the outcomes the Java Memory Model permits, classifying each as acceptable, interesting or forbidden. It makes the memory model observable rather than merely assertable, it grades the boss fights for the whole concurrency part, and a forbidden outcome is a bug in the JVM or in your understanding.

## Shape of the thing

112 lessons in twelve parts, three passes over the whole runtime at increasing depth. Part 0 is orientation, including the lesson that teaches you to tell a guarantee from an implementation detail before anything else happens. Parts I through V take you from a class file in a hex editor to an opcode you added to the interpreter yourself. Parts VI through VIII are the engine room: the JIT, garbage collection, threads and the memory model. Parts IX and X are serviceability and the native boundary, and Part XI is changing the runtime and opening a pull request.

Alongside the lessons there is a Blueprint set, which is the normative half. A Blueprint is a specification with nine fixed sections that may not reference the chapter it accompanies, so somebody can implement from it cold. There are 58 of them. Section 4 is split three ways into what the JVMS mandates, what HotSpot chose, and the ordering obligations, because if the Blueprint blurs that line the project has failed at the thing it exists to do.

Then three capstones. Track A is a class file interpreter in Rust that runs real, unmodified `java.base` code. Track B is a garbage collector built inside HotSpot on the real `CollectedHeap` and `BarrierSet` interfaces. Track C is a baseline JIT with working deoptimization. Their purpose is to test the Blueprints rather than to produce a runtime, and every defect a track finds is filed against the Blueprint and fixed there.

## Where you can run it

| | Environment | What you get |
|---|---|---|
| E0 | Colab, one badge click | A real JDK 27 with a JShell kernel, plus instrumentation agents, JVMTI agents, `jcmd`, JFR, and an OpenJDK build in one long cell |
| E1 | Devcontainer or your own machine | The above, plus a full build tree, `gdb`, `hsdis`, `-XX:+PrintAssembly` and honest benchmarks |
| E2 | Browser, no account at all | Pre executed pages with every output visible, plus the recorded transcript corpus |

Every page on the site is complete on first paint. The output you read was produced by a real run of the pinned JDK in CI, so there is no runtime to start and no account needed to read anything. The badge is how you go from reading to running.

E2 is the honest weak spot and this project will not dress it up. There is no Pyodide for Java. CheerpJ targets an old Java level and cannot be pinned, TeaVM is not a JVM, so a reader with no Google account reads results rather than producing them, and the page tells them so.

## Conformance

This project will never claim TCK conformance and says so on the first page of the conformance chapter rather than in a footnote. The Java Compatibility Kit is the only thing that can call something a Java Virtual Machine in the certification sense, and its licence covers implementations derived from OpenJDK and OpenJDK participants. It is not available to an independent implementation, so no artifact here will ever say "passes the TCK".

What replaces it is open, in tree and adversarial: jtreg, jcstress, JMH, differential execution against HotSpot, OpenJ9, GraalVM and ART, and class file fuzzing. The scorecard publishes a number with its failures classified, and the disclaimer sits on the same page as the numbers.

## Status

Specification stage. Nothing is built yet.

The current milestone is M0, which exists to measure the assumptions this design rests on, and it is the one milestone allowed to fail. Can a free Colab runtime become a pinned JDK 27 kernel in under 90 seconds. How much do JShell's synthetic per snippet classes distort a class histogram, a compilation log or a JFR recording. Can `bpc` pull struct layouts out of the Serviceability Agent's type database. Do the widgets survive Colab's output sandbox. If the answers come back wrong, M0 ends with a written re plan rather than with M1, and finding that out in week one is the point.

The second of those is answered. [How much does JShell distort what a lesson can observe](docs/probes/jshell-noise.md) measures the same workload four ways and draws the line: anything about one object you are holding is safe in the notebook kernel, anything that counts, totals or names classes has to run in a subprocess.

So is the question of whether the curriculum has to fork by platform. [What a machine can actually do](docs/probes/capability.md) asks a JDK and the box under it 119 questions on macOS, on Linux as root, on Linux as an ordinary user and on Windows, and 103 of the answers are the same on all four. Every one of the sixteen that differ is machine size, privilege, timing or a native tool somebody installed. None of them is a difference in what the JVM can do.

The verification lessons have their raw material. [Six class files that are wrong on purpose](docs/probes/classfile-malformed.md) tries to build each malformation B11 needs and records where the JVM notices, and four of the six come straight out of `java.lang.classfile` while the other two need about five lines of byte patching. The one with a constant pool index past the end of the pool is worth the trip on its own: with the verifier turned off it kills the VM ten times out of ten on Linux, three times out of ten on Windows and once out of ten on a Mac, which is what undefined behaviour looks like when you measure it instead of describing it.

The widget question came back with an answer to a different question. [What a Java kernel can put on the screen](docs/probes/widgets.md) tries twelve ways of getting something interactive in front of a reader and checks each one in four places, and the thing that decides the answer turns out not to be Colab's sandbox at all. It is Jupyter's trust model: a saved notebook nobody has run gets its style tags removed, its ids renamed, its form controls disabled and its scripts dropped, so only four of the twelve survive the state most readers will meet the page in. That is a smaller budget than the design assumed and it is enough, because `<details>` and an SVG in an `<img>` are both on the list that always works.

See the [milestone issues](https://github.com/tamnd/jvm-internals/issues?q=is%3Aissue+label%3Akind%2Fmilestone) for the plan and [ROADMAP.md](ROADMAP.md) for the short version.

## Licence

Prose, diagrams and animations are CC BY 4.0. Code is Apache-2.0. Anything derived from OpenJDK source is GPLv2 with the Classpath Exception and lives in `patches/` and `vendor/`, isolated and badged, and nowhere else. The Classpath Exception is why ordinary lesson code that merely uses the JDK carries no obligation at all. See [LICENSE.md](LICENSE.md).

## Security

This project asks you to run things: a bootstrap cell, a kernel from Maven Central, a JVMTI agent as a shared library, a container image, and eventually a JDK you built yourself. [SECURITY.md](SECURITY.md) says what is pinned, what is verified, what is published, and how to report it when one of those turns out to be a way to run code a reader did not ask for.

## Related

Part of a series that shares its pedagogy, its Blueprint format and its conformance machinery.

- [cpython-internals](https://github.com/tamnd/cpython-internals), CPython 3.15
- [gcc-internals](https://github.com/tamnd/gcc-internals), GCC 16
- [linux-kernel-internals](https://github.com/tamnd/linux-kernel-internals), Linux 7.2
- [llvm-internals](https://github.com/tamnd/llvm-internals), LLVM 23
