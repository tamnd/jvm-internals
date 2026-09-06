# What is actually in fourteen thousand class files

M2's exit criterion says BP-CLASSFILE section 2 is generated rather than written. Section 2 is the shape of the file: the header, the constant pool, the attributes and the access flags. That is four tables, and those four tables are the most transcribed artifact in this subject after the opcode list, which is how so many parser tutorials still tell a reader that 0x0020 means `ACC_SUPER` without saying that the same bit on a method means `synchronized`.

So this generates them, from the same three kinds of source the opcode table uses, and then it counts. The tables are [BP-CLASSFILE section 2, the structure of a class file](../generated/classfile.md). This page is the reading.

The three sources are JVMS SE25 chapter 4 for what the format is, `classfile_constants.h.template` at `jdk-27+35` for the tag numbers and flag masks as numbers rather than prose, and the pinned JDK's own `java.lang.reflect.AccessFlag` and `java.lang.classfile.Attributes` for what the platform will accept. The fourth thing is a census: every class file in `java.base` on two platforms, which is 14,911 class files, 52,538 fields, 134,330 methods and 2,139,699 constant pool entries.

## The headline

**Four classes in `java.base` set a bit in their top-level `access_flags` that JVMS 4.1@SE25 reserves and says should be zero.** They are `java/lang/invoke/DelegatingMethodHandle$Holder`, `DirectMethodHandle$Holder`, `Invokers$Holder` and `LambdaForm$Holder`, and the bit is 0x0002, which is `ACC_PRIVATE` on a field or a method and nothing at all on a class.

The reason is one line of the JDK: src/java.base/share/classes/java/lang/invoke/GenerateJLIClassesHelper.java:638@jdk-27+35 builds those four with `withFlags(ACC_PRIVATE | ACC_FINAL | ACC_SUPER)`. Member flags applied to a top-level class. Nothing enforces it, because the specification's own advice for a reserved bit is that a JVM should ignore it, and HotSpot does.

That is the finding a hand written table cannot produce and a census produces on its first run. It matters for a lesson because the first thing a reader does after seeing the flag table is write a parser that rejects what the table does not list, and their first real input is the runtime image they are running on.

## One class file in the module is from Java 8

14,909 of the 14,911 class files declare version 71.0. The other two are one per image, and both are `jdk/internal/module/SystemModulesMap` at version **52.0**, which is Java 8.

It is not a leftover. That class does not exist in the JDK source at all, it is generated at link time, and src/jdk.jlink/share/classes/jdk/tools/jlink/internal/plugins/SystemModulesPlugin.java:1290@jdk-27+35 writes it with `.withVersion(52, 0)`. The same file, at line 613, writes the other classes it generates with `ClassFileFormatVersion.latest().major()`. One generator asks the platform what version to write and the other has a literal, and they are 700 lines apart in one file.

A reader who dumps every class in `java.base` and expects one version number is going to find this and wonder what they did wrong. Now the page tells them before they look.

## The constant pool is mostly a string table

**Seventy per cent of every constant pool in `java.base` is `CONSTANT_Utf8` and `CONSTANT_NameAndType`.** Utf8 alone is 56.84% of 2,139,699 entries.

That is the number that explains the format. Every name of every class, field, method, attribute and signature is a Utf8 entry, and every reference to any of them spends a `NameAndType` pointing at two more. The pool is not a table of constants with some strings in it, it is a table of strings with some constants in it, and the largest one in the scanned code is 6,022 slots belonging to `sun/util/resources/cldr/LocaleNames_en`, which is a class whose entire job is to hold strings.

`CONSTANT_Dynamic`, tag 17, does not occur once. It is the newest pool entry the pinned specification defines and nothing in `java.base` uses it. That is a fact about one module rather than about the format, and it is the reason the generated table has a measured column instead of a claim that every tag is equally worth teaching.

## A flag table is only true at a version

18 of the 23 access flags this JDK knows do not mean the same thing at every class file version. `AccessFlag` carries that per version, which no document in this project did before, and the generated page prints each flag's history folded into ranges.

`ACC_STRICT` is the one that proves the point. JVMS table 4.6-A still lists 0x0800 for methods, worded as applying to versions 46 through 60. `AccessFlag` says `[METHOD]` for releases 2 through 16 and `[]` from release 17 on. The census says the bit is set on zero of the 134,330 methods scanned. Three sources, three shapes of the same fact, and the only one that says "you will never see this" is the count.

This is also the check that had to be loosened. The generator's first version required that every flag location the specification lists is a location this JDK's `AccessFlag` allows, and `ACC_STRICT` failed it immediately. Requiring agreement at the latest version is requiring the specification to forget its own history, so the check now asks whether any class file version allows it, and a flag that no version allows anywhere is still a hard stop.

## What the platform library says that the specification does not

The specification defines 30 attributes. The pinned JDK models all 30 and six more that no specification defines: `CharacterRangeTable`, `CompilationID`, `ModuleHashes`, `ModuleResolution`, `ModuleTarget` and `SourceID`. That is the format's extension mechanism being used rather than described, and it was also the generator's second hard stop to be relaxed, because an attribute the platform models and the specification does not is not two sources disagreeing.

Two facts per attribute come out of the API and out of nowhere else. Six of the 36 may appear more than once on the same element, which the specification states in prose in six separate subsections and a parser that keeps the last of each gets wrong for exactly those six. And every mapper declares what a rewrite may do with it: 25 hold constant pool references, five hold offsets into code, four can be copied byte for byte, and **two cannot be copied safely at all**.

The two are `RuntimeVisibleTypeAnnotations` and `RuntimeInvisibleTypeAnnotations`. They carry both pool references and code offsets, so there is no rule that keeps them correct across an edit, and the JDK's own library says so rather than preserving them and hoping. Anybody who writes a bytecode transformer in B12 will meet that, and the library saying it out loud is better documentation than the specification's silence.

## The arithmetic that says the counts are not nonsense

A census is only worth printing if it is checkable, so three of its numbers check each other.

`ACC_SUPER` is set on 13,096 classes. There are 14,911 class files, 1,813 of them set `ACC_INTERFACE`, and two set `ACC_MODULE`. 14,911 minus 1,813 minus 2 is 13,096 exactly. Every class file in the module is an interface, a module descriptor, or a class with `ACC_SUPER` set, with nothing left over.

The `Code` attribute occurs 124,849 times and the walk counted 124,849 method bodies, which are two different code paths arriving at the same number. The tag counts sum to the pool entry total in each environment separately, which is what makes it honest to sum the two environments afterwards. `StackMapTable` occurs on 48,605 of those 124,849 bodies, so 76,244 method bodies carry no stack map at all, which is the number BP-VERIFY needs to make its point: a frame is only written where control flow merges, and most methods never merge anything.

## Two platforms, and why every count is a sum

The osx-arm64 image has 7,441 class files and the linux-aarch64 image has 7,470, from the same build of the same JDK. So the page sums rather than averages, and says so in its second paragraph, because a class present in both images is counted twice on purpose and a reader who works out that 14,911 is not the size of `java.base` deserves to be told first.

Anything that is a property of the JDK rather than of the machine is checked for agreement instead, and two environments disagreeing about a flag mask or an attribute stability stops the generator with "Two JDKs, one pin". Example class names are the exception: they are scan data, the two images differ, and making them agree would be inventing a fact. That distinction cost one hard stop and one bug before it was drawn in the right place.

## What this means for the lessons

B01 through B03 are the class file lessons and this settles their running order. The pool comes first, because it is 70% of the bytes a reader will scroll past and every other structure is an index into it. The flags come second, with the bit collision table, because that is where a hand written parser goes wrong. The attributes come last, because the extension mechanism only makes sense once a reader has skipped one.

The Class File Playground gets two ready made exercises out of this: name a class in `java.base` whose version is not 71, and find the four classes with a bit set that the specification reserves. Both have a known answer, both take one pass over `jrt:/`, and neither can be answered by reading a table.

The fuzzer gate wants a case where HotSpot's rejection message differs from what the specification names. The stray flag measurement is the first hint at where to look: the reserved bits are a place where the specification says "should" and the implementation says nothing at all, and the gap between a should and an enforcement is where those two texts come apart.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about fifteen seconds.

```
python probes/classfile-census/run.py --out probes/classfile-census/results/mymachine.json
python tools/gen_classfile.py
```

The generator reaches the network, for the header at the pinned tag and for the specification chapter. `JVX_JDK_SRC` pointed at a local openjdk checkout takes the header off the network, which is what the CI job does with its cache. The chapter is hashed as it is read and compared against the hash `tools/gen_jvms_index.py` recorded, so a chapter Oracle republished in place stops the run and asks for a human to read the diff.

`probes/classfile-census/Census.java` is the half that has to run inside a JVM, because `AccessFlag` and `Attributes` cannot be read off disk and `jrt:/` is the reader's own runtime image. It is one source file run by the source launcher, so there is nothing to compile. A class inside the JDK that the JDK's own class file API cannot parse is recorded rather than skipped, and `tools/test_gen_classfile.py` fails if that count is not zero.
