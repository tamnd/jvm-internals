# Two million constant pool entries, and what all the strings are

M2's exit criterion says BP-CONSTPOOL section 2 is generated rather than written. Section 2 is the pool itself: the seventeen entry kinds, which of them `ldc` may load, what the tag byte means once a class is in memory, and what the several hundred thousand strings in a real pool are for. The generated tables are [BP-CONSTPOOL section 2, the constant pool](../generated/constpool.md). This page is the reading.

Four sources, joined so they check each other. JVMS SE25 chapter 4 for what the pool is. `classfile_constants.h.template` at `jdk-27+35` for the tag numbers and the method handle reference kinds as numbers rather than prose. `constantTag.hpp` and `constantTag.cpp` at the same tag for the same byte as HotSpot holds it, which is a wider thing than the format allows. And `java.lang.classfile` read off the pinned JDK, because two of the seventeen kinds are rare enough that a table built by looking for examples would have holes in it.

The counts come from a walk of `java.base` on two platforms: 14,911 class files, 2,139,699 pool entries, 2,675,920 uses of a `CONSTANT_Utf8`. The same tags are counted independently by `probes/classfile-census`, and the generator refuses to write a page where the two disagree.

## The headline

**Exactly one place in the JDK knows that loadability depends on the class file version, and it is a C file that no class file written this decade ever reaches.**

JVMS table 4.4-C@SE25 gives nine loadable kinds with a class file version against each. `CONSTANT_Class` is loadable from 49.0, `CONSTANT_MethodHandle` and `CONSTANT_MethodType` from 51.0, `CONSTANT_Dynamic` from 55.0, and the other five from 45.3. So "may `ldc` push this entry" is a question about a pair, the tag and the version, and not about the tag alone.

HotSpot answers it three times and gets the version into the answer once.

`constantTag::is_loadable_constant` is a comparison against the tag byte with no version anywhere near it, which is what the generated section 2.3 checks against table 4.4-C after subtracting the implementation tags. The type checking verifier is the same: src/hotspot/share/classfile/verifier.cpp:2144@jdk-27+35 builds a fixed mask of seven tags for `ldc` and `ldc_w`, with a comment saying the class file parser already checked the method handle constants. The parser did, at src/hotspot/share/classfile/classFileParser.cpp:230@jdk-27+35 and src/hotspot/share/classfile/classFileParser.cpp:253@jdk-27+35, but it checked whether the tag may *exist* in a pool at that version, which is table 4.4-B and a different question.

The only code that asks table 4.4-C's question is the old inference verifier, written in C: src/java.base/share/native/libverify/check_code.c:1237@jdk-27+35 adds `CONSTANT_Class` to the allowed set only at src/java.base/share/native/libverify/check_code.c:246@jdk-27+35, which is 49, and adds the two method handle kinds only at 51.

That verifier runs when `major_version` is below 50, from src/hotspot/share/classfile/verifier.cpp:219@jdk-27+35. Above 50 the type checking verifier runs and its mask has no version in it. The two thresholds are one apart, and that is the whole reason the arrangement is correct: the only versions where loadability differs from existence are 45 through 48 for `CONSTANT_Class`, and every one of those is below 50, so it lands in the C verifier that knows. A `CONSTANT_MethodHandle` in a version 50 file is rejected by the parser before either verifier sees it.

So the rule holds, and it holds by a coincidence of two numbers chosen twenty years apart. Nothing in the modern code path would notice if table 4.4-C moved a threshold above 50. That is worth a paragraph in a blueprint, because a reader who goes looking for the version check in the verifier they can actually reach will not find it and will conclude the specification is wrong.

## The tag byte in memory is a state machine

The format has seventeen legal tag values. HotSpot's `constantTag` has eight more that no class file may contain, all of them at 100 or above while the format stops at 20, so the two ranges cannot collide.

They are not decoration. `UnresolvedClass` is what a `CONSTANT_Class` is until something needs it, `ClassIndex` and `StringIndex` are states a pool is in while it is being built, and four of the eight are error tags. Four kinds can fail to resolve and each has its own error tag rather than a shared one, so `Dynamic` becomes `DynamicInError` and `MethodHandle` becomes `MethodHandleInError`. That is the specification's requirement that a second attempt throws the same exception rather than retrying, implemented in one byte.

`is_loadable_constant` accepting `UnresolvedClass` and `UnresolvedClassInError` is the reason section 2.3's three way check has a subtraction in it. An unresolved class is loadable, and it is not a tag, and both of those are true at once.

## Nothing in the pool is unexplained

`CONSTANT_Utf8` is 56.84% of every entry, and the format gives no way at all to tell one string from another. A class name, a method descriptor, the text of a literal and the name of an attribute are the same structure. The only way to know what a string is for is to ask what points at it.

So the probe asks, for every Utf8 entry in the module, and **every one of the 1,216,114 entries is accounted for by at least one of thirty roles**. That is the result this probe was written to get, and it took three passes to get it.

The last two roles to be found were the platform name in `ModuleTarget` and the hash algorithm name in `ModuleHashes`, two uses each, both in `java.base`'s module descriptor. Neither attribute is defined anywhere in the specification. A reader who walks a pool with a checklist built from JVMS chapter 4 alone will end with two strings they cannot name and no way to find out why.

The shape of the answer is worth reading on its own. Attribute names are the largest single role at 553,568 uses, and local variable names and descriptors together are 758,196, which is more than a quarter of all Utf8 use and is entirely debug information. A build that strips `LocalVariableTable` is not trimming the edges of the pool, it is removing the largest thing in it after the attribute names that would go with it.

Uses are not entries. 2,675,920 uses against 1,216,114 entries, because a pool holds one copy of each distinct string and every use is an index. `V` is one entry in a class with two hundred `void` methods.

## A pool is supposed to be a set and is not required to be

127 entries in 89 classes repeat a constant the pool already holds. That is legal, loads and runs, and the format says nothing about it either way.

They fall into two families and both are the compiler being unable to see that two references are the same thing. 99 are a method `java.lang.Object` declares, called on a receiver whose static type is an interface, so the call site names `toString` or `equals` on the interface it is calling through and two different interfaces in one class produce two `CONSTANT_InterfaceMethodref` entries. 28 are signature polymorphic calls on `java/lang/invoke/VarHandle`, where the descriptor is the argument and two identical descriptors are two call sites that happen to agree.

The probe finds them by rendering each entry to the thing it means and comparing the renderings, which is a different test from comparing bytes. Comparing bytes would find nothing, because the entries point at different indexes.

## The arithmetic that says the counts are real

`constant_pool_count` is one more than the number of entries, and that sentence is where most explanations of the pool stop. It is checkable instead.

There is no entry at index 0, and a `CONSTANT_Long` or a `CONSTANT_Double` takes two slots with nothing usable in the second. Over the scanned code, 2,139,699 entries plus 14,951 second slots plus 14,911 missing slot zeroes is 2,169,561, and the sum of every `constant_pool_count` field read out of the same files is 2,169,561. The generator refuses to write the page if those differ, and the unit test checks the identity per environment as well as in total.

The second slot count is the same number twice: 12,088 `CONSTANT_Long` plus 2,863 `CONSTANT_Double` is 14,951, counted by walking entries, and 14,951 is what the pool index walk reported as unusable. `tools/test_gen_constpool.py` asserts that equality directly, because it is the one line of the probe most likely to be quietly wrong.

## What did not turn up

`CONSTANT_Dynamic` does not occur once in 2.1 million entries. It is the newest kind the pinned specification defines, and there are 4,500 `CONSTANT_InvokeDynamic` call sites in the same code. The compiler that built `java.base` emits one and not the other.

Four of the nine method handle reference kinds do not occur either: 2, 3, 4 and 7, which are the two field writes, the static field read and `invokeSpecial`. Reference kind 6 is the row worth reading, because it points at both a `MethodRefEntry` and an `InterfaceMethodRefEntry` in this module, and both are legal. Class file version 52 allowed a static or private interface method to be a handle target and the specification states it as a version condition on that one row. A parser that takes the constraint as `Methodref` alone rejects a class file every JVM accepts.

That is the same shape as the headline: a rule the specification states per version, implemented somewhere a reader would not look first.

## What this means for the lessons

B01 is the pool lesson and this settles what it opens with. Not the seventeen kinds, which are a table anybody can read, but the question of what a string is for, because that is the one thing the format does not encode and it is 56% of the file. The thirty roles are the lesson, and the two that no specification defines are the exercise.

B03 gets the version question. A reader who has the generated table in front of them can be asked where HotSpot enforces the 49.0 in table 4.4-C, and the answer takes them through three files and ends somewhere they did not expect. That is a better introduction to the verifier than a description of the verifier.

BP-VERIFY inherits the finding directly, and so does the fuzzer gate. A class file at version 48 with an `ldc` of a `CONSTANT_Class` is a case where the specification names a rule and only one of the two verifiers checks it, which is exactly the kind of difference the fuzzer is looking for and one where the expected message is known in advance.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about a second.

```
python probes/constpool/run.py --out probes/constpool/results/mymachine.json
python tools/gen_constpool.py
```

The generator reaches the network for three of its four sources: two HotSpot files and the header at the pinned tag, plus the specification chapter. `JVX_JDK_SRC` pointed at a local openjdk checkout takes the three JDK files off the network, which is what the CI job does with its cache. The chapter is hashed as it is read and compared against the hash `tools/gen_jvms_index.py` recorded, so a chapter Oracle republished in place stops the run and asks for a person to read the diff.

`probes/constpool/Pool.java` is the half that has to run inside a JVM. It builds one entry of every one of the seventeen kinds through `java.lang.classfile` and reads the platform's own answers back, which is how the table has a row for `CONSTANT_Dynamic` at all, and then it walks `jrt:/modules/java.base` for the counts. It is one source file run by the source launcher, so there is nothing to compile.

`tools/test_gen_constpool.py` is the offline half of the checking: the four parsers against fixtures copied out of the real sources, twenty ways for the four sources to drift, and the committed page against the committed results. `python tools/gen_constpool.py --check` is the online half and runs in CI.
