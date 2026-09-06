# The verifier cannot tell `java.lang.Object` apart from an interface

M2's exit criterion says BP-VERIFY section 2 is generated rather than written. Section 2 is the type system the verifier has: which verification type may stand in for which other, what kind of relation that turns out to be, and where the guarantee it gives runs out. The generated tables are [BP-VERIFY section 2, the verifier's type system](../generated/verify.md). This page is the reading.

Four sources, joined so they check each other. JVMS 4.10.1.2@SE25 for the relation as Prolog. src/hotspot/share/classfile/verificationType.cpp:74@jdk-27+35 for the same relation as C++, which is the only one of the two anybody has ever run. src/hotspot/share/classfile/verifier.hpp:40@jdk-27+35 and src/hotspot/share/classfile/verifier.cpp:66@jdk-27+35 for the two class file versions that decide which verifier answers. And a grid of 256 class files handed to the pinned JVM, which is the only source that is an answer rather than a description of how to get one.

The grid is one class file per ordered pair of sixteen verification types. Each one produces a value of the row's type into a local and declares the column's type for that local in a stack map frame. If the JVM loads it, the row type is assignable to the column type. If it throws `VerifyError`, it is not. 65 of the 256 loaded, 191 were refused, and no cell failed for any other reason. Two environments measured the same 256 cells and agreed on every one.

## The headline

**To the verifier's type system, `java.lang.Object` and any interface are the same type. There are exactly six mutually assignable pairs among the sixteen types measured, and they close into one group: `Object`, `Runnable`, `Cloneable`, `Serializable`.**

A relation in which two distinct things are each assignable to the other is not antisymmetric, so it is not a partial order. It is not transitive either. Nine triples have `a` assignable to `b` and `b` assignable to `c` and `a` not assignable to `c`, and every one of them is an array type reaching `Runnable` through something that can. It is reflexive on all sixteen. So the thing every account of the verifier calls a type hierarchy is a preorder, and the quotient by its mutual pairs collapses `Object` and three interfaces into a single point.

HotSpot says so outright. The comment at src/hotspot/share/classfile/verificationType.cpp:74@jdk-27+35 reads "Otherwise, we treat interfaces as java.lang.Object", four lines above the `return` that does it. The specification says the same thing in a rule head rather than a sentence: `isWideningReference(class(_, _), class(To, L)) :- loadedClass(To, L, ToClass), classIsInterface(ToClass)` has an unbound first argument, so any class widens to any interface, unconditionally. JVMS 4.10.1.2@SE25 then states the consequence in prose, in one sentence, with no emphasis and no example: "There is no requirement that a reference stored by a variable of an interface type refers to an object that actually implements that interface."

## Why a grid and not a paraphrase

JVMS 4.10.1.2@SE25 gives the relation as 22 Prolog clauses over fourteen type terms. That is small enough to paraphrase, and paraphrasing it is what every account of the verifier does, which is how the interface hole stays invisible: the rule that opens it looks like the eight rules around it. The clauses are also not the thing that runs. HotSpot is the thing that runs, and the two are separate artifacts maintained by separate people.

So the probe does not read either one. It builds `verification_type_info` structures directly through `java.lang.classfile`, which lets a stack map frame be written that no compiler would emit, and asks the JVM. Four of the fourteen terms cannot be asked about at all: `oneWord`, `twoWord`, `reference` and the abstract `uninitialized` exist to make the rules shorter and no `verification_type_info` can name them. The remaining ten writable forms are the eight atoms plus `class(N, L)` and `arrayOf(X)`, and the grid instantiates the last two six ways: a class at the top, a final class, three interfaces chosen because two of them are the ones arrays reach, an array of references, an array of a narrower reference, and an array of a primitive.

The grid is 256 answers from a machine, and nothing in it would notice if the harness built the same class 256 times. Twelve of the cells can be settled on paper from 4.10.1.2 by anybody willing to follow the clauses: reflexivity, `int` to `top` yes, `int` to `float` no, `null` to `String` yes, `String` to `null` no, `String[]` to `Object[]` yes, `Object[]` to `String[]` no, `int[]` to `Object[]` no, `int[]` to `Object` yes. `probes/verify/run.py` writes no results file at all when a measured answer differs from a worked one, and the unit test asserts the count of disagreements is zero.

The three structural claims get the same treatment from the other direction. `Types.java` counts the reflexive cells, the non transitive triples and the mutual pairs in Java over the map it built as it went; `tools/gen_verify.py` counts them again in Python over the cells parsed back out of a results file. The two readers share no code, and the generator refuses to write a page where they disagree.

## The one refusal sentence

Every one of the 191 refusals came back in the same shape: `Type 'X' (current frame, locals[N]) is not assignable to 'Y' (stack map, locals[N])`. One shape, 191 times, with only the two type names changing.

That is worth knowing before reading anybody's bug report about a `VerifyError`. The message names the two types and the slot and nothing else, and in particular it does not name the rule that failed, because there is no rule object to name: src/hotspot/share/classfile/verificationType.cpp:104@jdk-27+35 onwards is a chain of `if` statements ending in `return false`, and the false has no provenance. A reader who has seen this sentence once has seen every refusal the type checker knows how to make.

## The class that verifies and then throws

A class that stores a `java.lang.String` into local 1 and declares that local as `java.lang.Runnable` in its stack map frame verifies. So does the same class with an `invokeinterface` of `Runnable.run()` on that local. Running it throws `java.lang.IncompatibleClassChangeError: Class java.lang.String does not implement the requested interface java.lang.Runnable`.

The control matters more than the finding. Change `Runnable` to `java.lang.Thread`, a class rather than an interface, and nothing else, and the same class file is refused with `Type 'java/lang/String' (current frame, locals[1]) is not assignable to 'java/lang/Thread' (stack map, locals[1])`. One word of difference in a constant pool entry decides whether the verifier looks at the class hierarchy or waves the frame through, and the word is whether the target happens to carry `ACC_INTERFACE`.

This is not a hole in the JVM's safety. It is a hole in the verifier's, moved elsewhere on purpose: `invokeinterface` checks the receiver at dispatch time, every time, and that check is why the design is sound. What the grid measures is where the boundary sits, and the boundary is not where a reader of "the bytecode verifier proves type safety" would put it.

## Twenty two clauses, one of which does not parse

JVMS 4.10.1.2@SE25 gives the relation as Prolog and the rule for arrays does not parse:

```prolog
isWideningReference(arrayOf(_), class(ClassName, L)) :-
    (ClassName = 'java/lang/Object' ;
     ClassName = 'java/lang/Cloneable' ;
     ClassName = 'java/io/Serializable')
    loadedClass(ClassName, L, LoadedClass),
    classDefiningLoader(LoadedClass, BL),
    isBootstrapLoader(BL).
```

Prolog separates the goals of a clause body with commas. There is none between the closing parenthesis of the disjunction and `loadedClass`, so a Prolog system asked to consult the section as written rejects the clause. The generator finds this by walking the body and checking that the only thing allowed between the end of one goal and the start of the next is a comma or a semicolon, which is a check that costs eleven lines and would have caught it at any point in the last twenty years.

Nothing depends on the text being executable, and that is exactly how a defect survives in a normative document for that long. The Prolog in chapter 4 is a specification language, not a program: nobody consults it, nobody tests it, and a reader who works through the rule by hand supplies the comma without noticing. The generator reports the count rather than asserting it, so a future edition that fixes the comma produces a page that stops mentioning it rather than a build that fails.

## Two verifiers, and the version where a refusal is not final

BP-STACKMAP left an open item asking what happens to a wrong frame at class file version 50. The answer is that it loads, and nothing is reported to the program.

Two constants decide it. `STACKMAP_ATTRIBUTE_MAJOR_VERSION` is 50 at src/hotspot/share/classfile/verifier.hpp:40@jdk-27+35, and `NOFAILOVER_MAJOR_VERSION` is 51 at src/hotspot/share/classfile/verifier.cpp:66@jdk-27+35. Below 50 the split verifier never runs, so the attribute is never read. At 50 the split verifier runs, refuses, and src/hotspot/share/classfile/verifier.cpp:230@jdk-27+35 hands the class to the old inference verifier, which infers its own types and accepts it. At 51 and above the refusal stands.

The probe writes the same wrong frame at majors 49, 50, 51, 52 and 71, with a correct frame control at each, and measures which load. 49 and 50 load, the other three throw `VerifyError`, and the control loads everywhere. `-Xlog:verification=info` is the only way to watch it happen: at 50 it prints "Fail over class verification to old verifier", and with logging off there is nothing to see. The generator predicts each row from the two constants and stops if a measured row disagrees, so the table is two independent sources agreeing rather than five numbers from one run.

That range is not historical trivia for anybody who accepts class files from elsewhere. A file claiming major 50 gets a verifier with different rules, and a tool that validates stack maps and then trusts them is checking something the JVM may decide not to require.

## What the accepted half rests on

Summed over `java.base` on two platforms: 14,911 classes, 124,849 methods with code, 3,902,996 instructions. 52,507 of them are `invokeinterface`, against 240,622 `invokevirtual`. There are also 121,076 `aastore`, 28,489 `checkcast` and 5,789 `instanceof`.

Every one of those carries a check made again at run time, and the `invokeinterface` count is the one this page is about: 52,507 sites in one module where the verifier let a reference through on the strength of a type it cannot distinguish from `Object`. The class count and the methods with code count are the same numbers BP-STACKMAP measured over the same two images, which is a small cross check between two probes that walk the same module by different routes.

The ratio is the interesting part. Interface calls are 18% of virtual calls, so the type the verifier does check covers most dispatch and the type it does not covers a large minority. A reader who wants a number for "how much of the runtime image rests on a check the verifier declined to make" now has one for one module.

## What this means for the lessons

B04 is the verification lesson and this settles what it opens with. Not the fourteen type terms, which are a diagram, but the grid, because the grid is a thing a reader can rebuild in an afternoon and the answer contradicts what they expect. The specific sequence is: draw the hierarchy, ask the reader to predict `String` to `Runnable`, show the measurement, then show `IncompatibleClassChangeError`.

B03 gets the failover. A reader who has met the class file version field as a number now has a reason to care which number, and the two constants give them a place in HotSpot to look rather than a claim to accept.

B12 gets the unparsable clause, in the lesson about reading specifications. It is the cleanest available demonstration that a normative document is a document: it has defects, they survive, and the way to find them is to do something with the text that nobody has done before.

BP-CLASSFILE's fuzzer inherits the grid's builder. `Types.java` writes stack map frames that no compiler emits, at chosen class file versions, with the expected verdict known in advance, which is the shape of input a fuzzer gate wants.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about ten seconds.

```
python probes/verify/run.py --out probes/verify/results/mymachine.json
python tools/gen_verify.py
```

The generator reaches the network for three JDK files at the pinned tag and for the specification chapter. `JVX_JDK_SRC` pointed at a local openjdk checkout takes the three off the network, which is what the CI job does with its cache. The chapter is hashed as it is read and compared against the hash `tools/gen_jvms_index.py` recorded, so a chapter Oracle republished in place stops the run and asks for a person to read the diff.

`probes/verify/Types.java` is the half that has to run inside a JVM. It builds and loads the 256 grid classes, the interface hole with its control, five versions of one wrong frame with five controls, and then walks `jrt:/modules/java.base` counting the instructions that carry a run time type check. Defining a class does not verify it, so every class is loaded through `defineClass` and then `ensureInitialized`, because linking is what verifies and initialising is the shortest way to insist on linking. It is one source file run by the source launcher, so there is nothing to compile.

`tools/test_gen_verify.py` is the offline half of the checking: the parsers against fixtures copied out of the real sources, twenty ways for the four sources to drift, the committed results against the shape they claim to have, and the committed page against the committed results including every one of the 256 cells. `python tools/gen_verify.py --check` is the online half and runs in CI.
