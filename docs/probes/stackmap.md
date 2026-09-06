# Two hundred thousand stack map frames, and not one of them a byte too long

M2's exit criterion says BP-STACKMAP section 2 is generated rather than written. Section 2 is the attribute itself: the nine verification types, the seven kinds of frame, the range of first bytes each kind occupies, and what a real attribute is made of. The generated tables are [BP-STACKMAP section 2, the stack map table](../generated/stackmap.md). This page is the reading.

Four sources, joined so they check each other. JVMS 4.7.4@SE25 for what the attribute is. `classfile_constants.h.template` at `jdk-27+35` for the item numbers as numbers rather than as comments on a structure. `stackMapTable.hpp` and `verificationType.hpp` at the same tag for the frame type ranges and for the same byte as the verifier holds it, which is a wider thing than the format allows. And `java.lang.classfile` read off the pinned JDK, because two of the nine types cannot be found by looking for them.

The counts come from a walk of `java.base` on two platforms: 14,911 class files, 48,605 of 124,849 methods with code carrying a `StackMapTable`, 205,580 frames in 1,390,250 bytes. Only 39% of methods with code have any frames at all, which is the first thing worth knowing about this attribute: a method with no branches needs no proof beyond its entry state, and most methods have no branches.

## The headline

**Every one of the 205,580 frames in `java.base` is written in the smallest form the format allows, and nothing anywhere requires that.**

JVMS 4.7.4@SE25 defines seven frame kinds and six of them are a compression of the seventh. A `full_frame` may appear anywhere. A `same_frame_extended` may be used where a `same_frame` would do, and a `full_frame` may be used where either would. Such a file is legal, verifies, loads and runs. The specification has no canonical form, no shortest form rule, and no sentence on the subject.

The probe works out, for each frame, what the smallest legal first byte would have been given the frame before it, and compares that against what was written. The answer differs for none of them. Written entirely as `full_frame` the same frames would take 4,198,203 bytes instead of 1,390,250, so the compressed forms are saving 2,807,953 bytes, and every byte of that saving is being taken.

That has a consequence worth stating plainly: two compilers that agree about the types at every branch target will emit byte identical `StackMapTable` attributes. A difference in these bytes is a difference in what was proved and not a difference in how it was written down. Anybody diffing class files, checking a build for reproducibility, or comparing `javac` against a second implementation gets that for free, and gets it from a property no document promises.

The zero is the reason this probe has a self check in it. A zero out of a function nobody has ever watched return anything else is not a measurement. So `probes/stackmap/Frames.java` runs six frames whose smallest encoding can be worked out on paper, one per kind, and `probes/stackmap/run.py` refuses to write a results file if the encoder gets any of them wrong. The six expected first bytes are in the results as `encoder_self_check` and the unit test asserts them.

The byte counts have the same problem and a different answer. `attribute_bytes` and the hypothetical `minimal_bytes` are both computed from one set of width rules, so a wrong rule cancels out of the comparison between them. A second reader in the same probe walks the class files as bytes and takes `attribute_length` straight out of the attribute header, which is a number nothing else in the probe computed. The two agree at 1,390,250 on both platforms, and the generator will not write a page where they do not.

## The hundred and nineteen bytes that mean nothing

A frame's first byte has 256 possible values and 119 of them are reserved. JVMS 4.7.4@SE25 says so in one sentence of prose, in no table, and gives the range no name. HotSpot names it: `RESERVED_START` and `RESERVED_END` at src/hotspot/share/classfile/stackMapTable.hpp:156@jdk-27+35, which is the only place either boundary is a constant rather than a sentence.

That is 46% of the encoding space, and the other 54% is fully used. Every one of the 137 legal first bytes occurs at least once in `java.base`. There is no legal value a parser can fail to handle and still pass a test suite built from real class files.

The reserved range is where this attribute differs from the rest of a class file. An unknown attribute can be skipped, because an attribute carries its own length. An unknown frame type cannot be skipped at all, because the length is a function of the type. HotSpot's decoder shows what that costs: src/hotspot/share/classfile/stackMapTable.cpp:310@jdk-27+35 reads the `u2 offset_delta` and only then does src/hotspot/share/classfile/stackMapTable.cpp:312@jdk-27+35 reject the reserved types. It has already consumed two bytes it had no business reading. That is harmless because it throws rather than continuing, and it is a neat demonstration of the point: there is no correct number of bytes to skip, so the code guesses the common one and errors.

There is a second `reserved frame type` error at src/hotspot/share/classfile/stackMapTable.cpp:474@jdk-27+35, at the bottom of the same function, and it is unreachable. The branches above it cover 0 to 63, 64 to 127, everything below 247, 247, 248 to 251, 252 to 254 and 255, which is every value a `u1` can hold. It is a defensive default in a function whose input is exhaustively partitioned, and reading the function top to bottom is the only way to see that.

## Two verification types that had to be built

`ITEM_Uninitialized` occurs 908 times in the scanned code and every one of them is on an operand stack. `ITEM_Null` occurs 68 times and every one is on an operand stack. `ITEM_Top` occurs 26,349 times and every one is in a locals array. None of those is a rule. The format allows all nine types in both places, and a table built by looking at real class files would have said otherwise three times.

So the probe stops looking and builds. `Frames.java` assembles a three method class through `java.lang.classfile`: one method that holds an uninitialized object on the stack across a branch, one that stores it into a local before calling its constructor, and a constructor that branches before chaining so `UninitializedThis` appears in both a locals array and on a stack. Then it hands the 316 bytes to the running JVM.

Defining a class does not verify it. Linking does, and initialising is the shortest way to insist on linking, so the probe calls `ensureInitialized` on the result of `defineClass` and records whether the JVM accepted it. It did, on both platforms. `run.py` refuses to write a results file where any of the four cases went missing or the class failed to verify, and the generator refuses to write a page where `built.verifies` is not 1.

The result is that the generated table can say those cells are what a compiler emits and not what a machine will take, and the difference between those two claims is the whole reason the built class exists.

## The implied `top`, three ways

`ITEM_Long` and `ITEM_Double` each describe two locations, and the second location has the verification type `top`. That second `top` is not written down. One item covers both, and a parser that allocates one slot per item will be off by one for every wide value it meets.

The counts confirm it without being told to. There are 41,507 `ITEM_Long` and `ITEM_Double` items in locals arrays and only 26,349 `ITEM_Top`. A format that wrote the implied second half would need at least as many of the second as of the first. So every `ITEM_Top` in `java.base` is a slot that is genuinely unreadable at that offset rather than the tail of a wide value.

The byte agreement is the second confirmation. If `java.lang.classfile` were handing back an expanded locals array with a synthetic `top` in it, the probe's width model would over count every `full_frame` and the raw reader would not have matched at 1,390,250.

The third is HotSpot doing the expansion in front of you. src/hotspot/share/classfile/stackMapTable.cpp:397@jdk-27+35 sizes an append frame's locals array at `appends*2`, two slots for every appended item, because it does not know until it reads them which are category 2. src/hotspot/share/classfile/verificationType.hpp:64@jdk-27+35 introduces the names the expansion needs, `ITEM_Long_2nd` and `ITEM_Double_2nd`, in an enum whose comment says these are not found in classfiles. Six values live there, 9 through 14, five of them written with no number at all so a reader has to count, and none of them can collide with a format item because the format stops at 8.

## The offset arithmetic, and the one method in every method that gets it wrong

A frame applies at the previous frame's offset plus `offset_delta` plus 1, except for the first frame in the attribute, where it applies at `offset_delta` exactly. The exception exists because the frame the first entry is a delta against is not in the file: JVMS 4.10.1.5@SE25 computes it from the method descriptor and the access flags. HotSpot writes the two cases four lines apart, at src/hotspot/share/classfile/stackMapTable.cpp:258@jdk-27+35 and src/hotspot/share/classfile/stackMapTable.cpp:265@jdk-27+35.

The `+ 1` is doing real work. It makes two frames at the same offset impossible to encode, so a verifier gets sortedness and uniqueness out of the encoding instead of checking for them. It is also the rule in this attribute most likely to be implemented wrongly, because the exception applies to exactly one frame per method and a test suite of small methods will not notice.

So the probe checks it rather than quoting it. 122,906 of the 205,580 frames carry their own delta in the first byte, which means the rule can be applied to that byte and compared against the bytecode offset the JDK resolved independently. They agree on all of them, and the generator will not write the page if they stop agreeing.

Two numbers make the extended frame kinds look less like an afterthought. 7,182 frames sit more than 63 bytes past the frame before them, so a one byte delta could not have expressed them, and the largest single gap is 19,995 bytes. `chop` and `append` are the other half of the same story: both count outwards from the byte that sits between their two ranges, one as `SAME_FRAME_EXTENDED - frame_type` at src/hotspot/share/classfile/stackMapTable.cpp:355@jdk-27+35 and the other as `frame_type - APPEND_FRAME_START + 1` at src/hotspot/share/classfile/stackMapTable.cpp:395@jdk-27+35, and neither can move more than three locals, so a branch target where four locals appear at once costs a `full_frame`.

## What the proof costs

1,390,250 bytes of stack map against 7,537,707 bytes of bytecode. A class file in `java.base` carries 18 bytes of proof for every hundred bytes of code, and the attribute is 2.4% of the whole file.

That is the price of verifying by type checking rather than by inference, paid on disk and in memory by every class ever loaded. It bought a verifier that is a single linear pass instead of a fixed point iteration, and it is the reason a class file written since version 50.0 is the shape it is. The comparison a reader wants is against the alternative, and the alternative is not zero: the inference verifier had to keep a work list and revisit basic blocks, and the time it spent doing that was paid at every class load rather than once at compile time.

The largest locals array in `java.base` is 35 wide, in `java.lang.FdLibm$Pow.compute`, and the deepest operand stack recorded in any frame is 11, in `java.util.DualPivotQuicksort$Sorter.onCompletion`. Both are worth knowing before writing a parser, because both are far below the `u2` the format allows and a reader who sizes buffers by the field width will allocate 65,535 entries for a method that needs 35.

## What this means for the lessons

B04 is the verification lesson and this settles what it opens with. Not the nine types, which are a table, but the canonicality finding, because it is the one claim on the page a reader can check themselves in five minutes with `javap -v` and cannot find in any specification.

B02 gets the reserved range. A reader who has the frame kind table in front of them can be asked what a parser should do with first byte 200, and the answer walks them through why an attribute that carries a length is a different kind of object from a frame that does not.

BP-VERIFY inherits the built class directly. It is a small, legal, hand assembled class file that exercises the two verification types nothing in the runtime image exercises, which is exactly the shape of input the fuzzer gate wants and one where the expected outcome is known in advance.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about a minute.

```
python probes/stackmap/run.py --out probes/stackmap/results/mymachine.json
python tools/gen_stackmap.py
```

The generator reaches the network for three of its four sources: the header and two HotSpot files at the pinned tag, plus the specification chapter. `JVX_JDK_SRC` pointed at a local openjdk checkout takes the three JDK files off the network, which is what the CI job does with its cache. The chapter is hashed as it is read and compared against the hash `tools/gen_jvms_index.py` recorded, so a chapter Oracle republished in place stops the run and asks for a person to read the diff.

`probes/stackmap/Frames.java` is the half that has to run inside a JVM. It asks `java.lang.classfile` for the tag of every verification type, builds and verifies the class described above, and then walks `jrt:/modules/java.base` twice: once through the class file API for the decoded frames, and once as raw bytes with an independent constant pool and attribute walker for the lengths. It is one source file run by the source launcher, so there is nothing to compile.

`tools/test_gen_stackmap.py` is the offline half of the checking: the parsers against fixtures copied out of the real sources, sixteen ways for the four sources to drift, the committed results against the shape they claim to have, and the committed page against the committed results. `python tools/gen_stackmap.py --check` is the online half and runs in CI.
