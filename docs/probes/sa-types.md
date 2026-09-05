# Reading HotSpot's own struct layouts, and who is allowed to

Section 2 of every blueprint is supposed to be the VM's internal layout of the thing the lesson is about, generated rather than transcribed. The Serviceability Agent's type database is the right source for it, because it is what the VM itself believes about its own structs after every configuration decision has been made, rather than a reading of the C++ source. The question was never whether the database has the fields. It is who is allowed to open it, because [an earlier probe](capability.md) found that `jhsdb` is refused for an ordinary user both on macOS and on Linux with `ptrace_scope` at 1, and a section 2 that needs root is a section 2 this project cannot generate on a reader's machine.

So this probe tries four ways in, on three machines, and then asks every door that opened for the same four types and compares the answers. The full result is [the generated page](../generated/sa-types.md). This is what it means.

## The headline

**An ordinary user on Linux gets in three different ways, and one of them needs nothing from anybody.** `ptrace_scope` at 1 refuses an attach to a process you did not start, which is the route everyone tries first and the route the earlier probe measured. It permits an attach to your own descendant. So a tool that starts the JVM it is going to read is allowed, and `bpc` is exactly that shape: one command that starts a VM, reads its type database and stops it. The other two routes are the target opting in with `prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY)`, which is useful when the VM is somebody else's, and dumping the target's own core and reading the file, which involves no tracing at all and so cannot be refused by a tracing policy.

**On macOS every route was refused, for an ordinary user.** Both attaches fail in `task_for_pid` with `(os/kern) failure`, and so does the `jhsdb jstack` control, which is the same wall the earlier probe hit. The `prctl` route does not exist on macOS at all. The core route is the interesting refusal: the target raised its core limit, `/cores` is writable, the process died on `SIGABRT`, and no core appeared. Why is not measured here, so it is not stated here. macOS as root is also not measured, because this run had no non-interactive way to become root on that machine, and an unmeasured cell is left unmeasured rather than assumed to be a yes.

**The privileged machine had fewer routes than the unprivileged one.** Root gets all three attaches and the control, and the self core route fails there, because that machine's `core_pattern` pipes cores to a crash handler instead of writing a file. That is a distribution's choice about crash reporting rather than anything about permissions, and it is a good reminder that the routes are independent: privilege opens three of them and closes none, and a crash handler closes one of them for everybody on the box.

## What that means for `bpc`

Generating section 2 from the type database is possible without privileges on Linux, using a spawned target. It is not possible on macOS for an ordinary user by any of the four routes tried here. That is a real constraint on the tool rather than on the curriculum: `bpc` runs where the repository is built, and the generated section is committed, so a reader on a Mac reads a file rather than attaching to anything. The place it bites is a contributor on a Mac who wants to regenerate. They will need either a Linux machine, a container, or a route this probe did not find.

## The answers agree, which is the part worth checking

Every route that opened on a machine read the same numbers as every other route on that machine, and on both Linux machines the answer also matches what `jhsdb clhsdb` prints when a person types `type Klass` and `field Klass` at it by hand. The two Linux machines, one root and one not, produced type dumps that are identical field for field. A route that works and lies is worse than a route that is refused, and this is the check that would have caught it.

One disagreement did show up during the run, and it was the probe's fault rather than the VM's. `clhsdb` prints seven columns for a field, the last being the value the field holds right now, and the first version of the parser accepted six. Every field line was skipped, every type came back with no fields at all, and the comparison duly reported that the tool and the API disagreed. The parser now takes six or seven and the test for it is the run itself. It is worth writing down because the failure was silent in exactly the way this comparison exists to catch: the output looked like a finding.

## What the database actually holds, which is less than you would hope

**`markWord` has a size and no fields.** The type is there, it is 8 bytes, and the type database exports not one field of it. So the mark word's bit layout, which is the thing half the object layout lessons are about, cannot come from this source at all. That answers a question that was open: [`tools/gen_markword.py`](../../tools/gen_markword.py) parses `markWord.hpp` for the bit positions, and the plan was for the SA half of section 2 to check the header parsing half. On these four types they cannot check each other, because they do not overlap. The header parse is not redundant. It is the only source for those bits.

**`oopDesc` is 16 bytes here, and the object header on the same build is 8.** The database says `_mark` at 0 and `_compressed_klass` at 8. The measured first field offset of a real object on the same JDK is 8, because `UseCompactObjectHeaders` is on by default and the class pointer is in the mark word. Both are true. The type database describes the struct as the compiler laid it out, and whether a field in it is used is a runtime decision the struct does not record. A generated section 2 that prints `oopDesc` without saying that will tell a reader an object has a separate class pointer when it does not, which is the exact mistake the note in `docs/generated/markword.json` already warns about.

**What is exported is what `vmStructs` chose to export, not the struct.** `InstanceKlass` is 472 bytes and 29 fields come out of it, with unexplained ground between them: nothing between 240 and 272, nothing between 344 and 360, nothing between 376 and 400. Those are fields the VM did not publish, not padding, and nothing in the database distinguishes the two. A section 2 generated from this can say "these fields are at these offsets" and cannot say "this is what an `InstanceKlass` contains".

Two things in there are consistent in a way that is worth pointing at, because they are the sort of claim a lesson would otherwise have to assert. `Klass` is 200 bytes and the first field `InstanceKlass` declares for itself is at 200, so a subclass really does start where its superclass ends. And `Klass` exports `_primary_supers[0]` at offset 48 with the next field at 112, so the array the SA names by its first element occupies 64 bytes, which is eight pointers. The database does not record the length, so that arithmetic is the only evidence for the eight and the page prints the field as the database names it.

## Where it ran

Three environments, all on the pinned java 27+35-2325, all measured on 2026-09-05.

| name | platform | account | notes |
|---|---|---|---|
| `linux-x64` | linux-x86_64 | an ordinary user | `ptrace_scope` at 1, cores written to a file |
| `linux-x64-root` | linux-x86_64 | root | `ptrace_scope` at 1, cores piped to apport |
| `osx-arm64` | darwin-arm64 | an ordinary user | no ptrace policy to configure |

Windows is not measured. The Serviceability Agent works differently there, it has no `ptrace` and no core pattern, and the routes in this probe are the wrong questions to ask it. It needs its own probe rather than a fourth column of blanks.

## Running it

```
JAVA_HOME=/path/to/jdk-27 python probes/sa/run.py --out probes/sa/results/<name>.json
python tools/gen_sa_types.py
```

It needs a few hundred megabytes of scratch space for the core file and about ten minutes, most of which is the Serviceability Agent reading a 300MB core. It touches no network. Do not run it as root to make it pass: root is a separate measurement and it goes in its own file, because the interesting reader is the one without privileges.

## What is still open

The `bpc` side of this is not written. What the probe proves is that the source is reachable without privileges on the machine the repository is built on, which was the blocking question in issue #3. The shape of the generated section 2, and what it says about fields the database does not export, is the next decision rather than this file's.
