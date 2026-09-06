# Three answers to how many opcodes there are

M2's exit criterion says BP-BYTECODE section 3 is generated rather than written, and section 3 is the instruction set. An opcode table is the most copied artifact in this subject, and almost every copy of it is a transcription of a transcription, which is how so many of them still list `jsr` next to `iadd` with no hint that one of the two has not been emitted by a compiler since 1.4. The way to not write another one of those is to not write one at all.

So this generates it, from three sources that check each other, and it counts. The table is [BP-BYTECODE section 3, the instruction set](../generated/opcodes.md), which cannot drift from what the sources say because it is rebuilt from them on every push. This page is the reading.

## The headline

**The three places an opcode is defined give three different answers, and none of them is wrong.** JVMS SE25 chapter 7 lists 205. HotSpot's `bytecodes.cpp` at `jdk-27+35` defines 239. `java.lang.classfile.Opcode` on the pinned JDK has 213 constants.

Those numbers are not a contradiction, they are three questions. The specification is answering what a class file is allowed to contain. HotSpot is answering what its interpreter can execute, which is more, because the interpreter rewrites a method's bytecode the first time it links it and 36 of its codes are forms no class file may hold. The class file API is answering what its own reader and writer can name, which is neither of the other two, because it drops `wide` and `breakpoint` and adds a constant for each of the twelve instructions `wide` can be applied to.

A generated table gets to say all three and show the reader where each number comes from. A hand written one has to pick.

## The finding a lesson author needs most

**Nine instructions carry an operand that is not a constant pool index by the time a VM executes them, and every one of them is an instruction a reader will meet on their first day.** The nine are `getfield`, `getstatic`, `invokedynamic`, `invokeinterface`, `invokespecial`, `invokestatic`, `invokevirtual`, `putfield` and `putstatic`.

HotSpot's format strings record this in a way nothing else does. A lowercase letter is an operand as the class file carries it and an uppercase one is an operand in the machine's native byte order, and those nine have uppercase. What happens is that the Rewriter replaces the big endian constant pool index in the file with an index into HotSpot's own resolved cache, written in whatever order the machine uses. The bytes on disk and the bytes in memory are different bytes.

This is the difference between what `javap` prints and what a bytecode dump out of a running VM prints, and neither tool is lying. A lesson that shows a reader an `invokevirtual` in a hex editor and then the same `invokevirtual` through a debugger without explaining this has handed them a contradiction they cannot resolve. The generated table marks all nine and the paragraph above it says why, which is the whole reason the operands column is HotSpot's format string in words rather than the specification's prose.

## What a reader will actually meet

A table of 205 opcodes read top to bottom suggests they matter about equally. They do not, and the gap is not close.

Every instruction in every method body of `java.base`, on two platforms, is 3,902,996 instructions over 124,849 method bodies. 194 of the 213 the class file API can name occur at least once. **13 of them are half of all bytecode. 52 are ninety per cent. 110 are ninety nine.**

| | seen | share |
|---|---:|---:|
| `aload_0` | 292,455 | 7.49% |
| `dup` | 283,519 | 7.26% |
| `invokevirtual` | 240,622 | 6.17% |
| `bipush` | 176,217 | 4.51% |
| `getfield` | 148,904 | 3.82% |

The median instruction that occurs at all occurs 3,030 times, against the top one's 292,455. Two orders of magnitude separate the middle of the list from the top of it, and another two separate the bottom from the middle. A lesson that spends equal time on each opcode spends most of its time on instructions the reader will never see, and until this probe ran there was no number in this repository to say so.

The count is also the only one of the three sources that has to run. A specification chapter and a header file can be fetched. An enum has to be asked, and a frequency has to be measured.

## The wide forms, and a sentence I had to delete

The first draft of section 3.5 said that the eleven unused wide forms mean no method in the scanned code has more than 256 local variables. That reads well and it is the obvious thing to conclude. It is also contradicted by the data on the same page, because `iinc_w` occurs 66 times.

Rather than reword around it, the probe was extended to measure the thing the sentence was guessing at: the highest local variable slot any instruction names, per opcode and overall. The answers are that the highest slot anywhere is 142, that `iinc_w`'s own highest slot is 16, and that the `iinc` constants in the scanned code run from -1,075 to 2,000. `wide` widens both operands of `iinc`, so what needed the extra bytes was the constant and not the slot.

That is now what the page says, and it says it with three measured numbers instead of one plausible clause. The general form of this is worth keeping: when a generated page is about to assert something, the question to ask is whether the probe is one field away from measuring it, and here it was one `EnumMap` away.

## Why three sources instead of one

Any one of the three would produce a table. Joining them produces a table that fails loudly.

The generator refuses to write anything when the sources disagree in a way it does not already understand and name. An opcode the specification lists and HotSpot lacks, a number the two call by different mnemonics, a wide form whose length in the class file API disagrees with HotSpot's wide format string, a code HotSpot numbers below the specification's boundary that the specification does not list, a format character nothing in the generator knows, a JVMS section that was renamed out from under a link, and a class file API that grows a constant for `wide` itself: each of those is a hard stop with a sentence saying what moved. `tools/test_gen_opcodes.py` breaks one of them at a time and checks that each still stops the run.

The specification half gets a second guard. The chapter is hashed as it is read and compared against the hash `tools/gen_jvms_index.py` recorded, so a chapter Oracle republished in place stops the generator and tells the reader to regenerate the index and read the diff, rather than quietly changing a row.

This is the same shape as `gen_markword.py`, which reads `markWord.hpp` and the SA type database and refuses to write when the two disagree about the object header. One source is a transcription. Two that check each other is a measurement.

## Two platforms, and why the census is summed

`java.base` is not the same file on every platform. The osx-arm64 image has 7,441 class files and the linux-aarch64 image has 7,470, from the same build of the same JDK.

That is why the census sums rather than averages. Each environment scanned its own copy, so a sum is a count of instructions actually seen and a mean would be a count of nothing. It is also why the enum is checked for agreement instead: an enum is a property of the JDK rather than of the machine, so two environments reporting different opcode values under one pin means two different JDKs, and the generator stops.

## What this means for the lessons

B04 through B08 are the bytecode lessons, and the ordering question they all face is which instructions to teach first. The coverage marks answer it: 13 instructions is a first lesson, 52 is the working set, and the remaining 143 are reference material a reader looks up rather than learns.

The 36 rewritten forms are a lesson of their own, and probably a short one in B08. A reader who dumps bytecode out of a live VM and finds `fast_agetfield` where their class file had `getfield` has met the interpreter's rewriter, and the generated table tells them exactly which code each rewritten form came from.

The four instructions no compiler emits, `jsr`, `jsr_w`, `ret` and `ret_w`, get a paragraph and not a section. They are the reason the verifier has two type checking schemes, which is BP-VERIFY's problem rather than BP-BYTECODE's, and the count is the evidence that the modern one is the only one a reader will ever exercise.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about ten seconds.

```
python probes/opcodes/run.py --out probes/opcodes/results/mymachine.json
python tools/gen_opcodes.py
```

The generator does reach the network, for HotSpot's two files at the pinned tag and the specification chapter. `JVX_JDK_SRC` pointed at a local openjdk checkout takes the first two off the network, which is what the CI job does with its cache.

`probes/opcodes/Opcodes.java` is the half that has to run inside a JVM, because an enum cannot be read off disk and `jrt:/` is the reader's own runtime image rather than a jar fetched from anywhere. It is a single source file run by the source launcher, so there is nothing to compile. A class inside the JDK that the JDK's own class file API cannot parse is recorded rather than skipped, and the tests fail if the count is not zero, because that would be the most interesting line in the output and swallowing it would be the way to never find out.
