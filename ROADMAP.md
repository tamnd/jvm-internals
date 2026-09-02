# Roadmap

Thirteen milestones, 149 person weeks. Each milestone has an issue with its tasks, its exit gates and its kill criteria on it, linked below, and that issue is the thing that gets updated. This file is the map.

This is the largest project in the series and the two reasons are the capstones and the fact that HotSpot ships five garbage collectors and two compilers where CPython had one of each.

Four of these are real stopping points, meaning a place where the project could end and still have produced something worth having. They are marked below.

| | Milestone | Weeks | What it produces |
|---|---|---|---|
| [M0](https://github.com/tamnd/jvm-internals/issues/13) | Pilot and probes | 6 | The probe reports, `build.py`, `bpc` reading the SA type database, and the pilot lesson |
| [M1](https://github.com/tamnd/jvm-internals/issues/14) | Pass 1, public | 11 | Seventeen lessons, the site, six animations, the claim ledger. **Stopping point.** |
| [M2](https://github.com/tamnd/jvm-internals/issues/15) | The class file | 12 | Twelve lessons, six Blueprints, the Class File Playground, the fuzzer |
| [M3](https://github.com/tamnd/jvm-internals/issues/16) | Loading and linking | 9 | Ten lessons, seven Blueprints, and an initialization race demonstrated live |
| [M4](https://github.com/tamnd/jvm-internals/issues/17) | Objects and memory | 10 | Ten lessons, five Blueprints, the Layout Playground. **Stopping point.** |
| [M5](https://github.com/tamnd/jvm-internals/issues/18) | Execution | 11 | Ten lessons, five Blueprints, the build lesson, and an opcode you added yourself |
| [M6](https://github.com/tamnd/jvm-internals/issues/19) | The version bump | 11 | JDK 28 across the whole project, with the cost measured and published |
| [M7](https://github.com/tamnd/jvm-internals/issues/20) | The JIT completed | 9 | Seven lessons, five Blueprints, the Warmup Tape. **Stopping point.** |
| [M8](https://github.com/tamnd/jvm-internals/issues/21) | Garbage collection | 13 | Twelve lessons, eight Blueprints, the Collector Playground |
| [M9](https://github.com/tamnd/jvm-internals/issues/22) | Threads and the memory model | 13 | Twelve lessons, four Blueprints, the Race Playground on real hardware |
| [M10](https://github.com/tamnd/jvm-internals/issues/23) | Serviceability and native | 13 | Sixteen lessons, ten Blueprints, the jtreg classification. **Stopping point.** |
| [M11](https://github.com/tamnd/jvm-internals/issues/24) | The capstones | 24 | Nine lessons, three tracks, the scorecard, the Blueprint defect count |
| [M12](https://github.com/tamnd/jvm-internals/issues/25) | v1.0 | 7 | The coverage ledger, the errata process, the review record |

## Why the order is what it is

The class file comes before loading, and loading before objects, because that is the order the runtime itself encounters them and because each one is a prerequisite for watching the next. A reader who meets the constant pool for the first time inside a lesson about linking has learned two things badly instead of one thing well.

Execution comes before the JIT for the same reason. The template interpreter is the thing C1 and C2 are faster than, and the tiered compilation story makes no sense until you have watched the slow path it replaces.

M6 is scheduled rather than reactive. JDK 28 reaches GA in March 2027 and it carries JEP 401, value classes and objects in preview, which changes what an object is. A version bump lands mid project whatever the start date, so the point of doing it deliberately is to measure what it costs while the corpus is still small enough to fix. If the bump costs more than three person weeks, the generation ratio is too low, and the answer is to generate more rather than to write faster. That threshold is written down now so it cannot be rationalised later.

Garbage collection and the memory model come after the JIT because both are much easier to teach once the reader accepts that the code being executed is not the code they wrote. Escape analysis removing an allocation and a race that only appears after C2 compiles the loop are the same lesson told twice.

The capstones come last and they are the largest milestone by a wide margin, because they are how the Blueprints get tested. Every defect a track finds is filed against the Blueprint, with the clause number, and fixed there rather than worked around. If the total defect count comes in under twenty, that is investigated rather than celebrated, because it means either the Blueprints are unusually good or the tracks were not pushed hard enough, and the retrospective has to say which.

## What is deliberately not covered

Java the language, GC tuning as a discipline, application performance engineering, OpenJ9 and GraalVM as primary subjects rather than as controls, Android ART beyond one comparison chapter, and the JDK class libraries above the runtime boundary. Each of those is at least a part on its own and several are their own project. The first lesson says so on the first page rather than letting you find out in month three.

## Stopping points

The project is designed so that stopping is a decision rather than a failure. Each of the four marked above is a release with a version number and a written statement of what is and is not in it, and none of them is described as incomplete.

After M1 there is a seventeen lesson visual tour of the JVM that nothing else in the world matches, free, in a browser. After M4 there is everything about class files, loading and objects, which is most of what a working Java developer actually needs and never gets. After M7 the execution engine is complete including the JIT and the Warmup Tape, which is the point at which the project has delivered its distinctive thing. After M10 the whole runtime is taught with 58 Blueprints, and the capstones are the proof rather than the product.
