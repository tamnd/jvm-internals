# Two rules the specification protects with a VerifyError, and HotSpot with something else

M2's last gate asks for a case where HotSpot's rejection message differs from what the specification names. `probes/classfile-fuzz` builds one valid class file, breaks it 27 named ways and 256 random ways, and records where the JVM stopped and in what words. The other half of the comparison is 23 sentences quoted out of JVMS SE25, one per rule, checked against the chapter on every run. The tables are [where HotSpot and the specification disagree about a broken class file](../generated/classfile-fuzz.md). This page is the reading.

Two cases answer the gate, and they are the same case twice.

## The headline

**A class file that breaks a constraint from JVMS 4.9 gets a `ClassFormatError`, and 5.4.1 says it should get a `VerifyError`.** Set `max_stack` to zero under a body that pushes two values and the JVM says `StackMapTable format error: bad type array size in method 'int Target.go()'`. Set `max_locals` below the argument count and it says `Arguments can't fit into locals in class file Target`. JVMS 4.9.2@SE25 states the first rule as a structural constraint and JVMS 4.9.1@SE25 states the second as a static constraint, and JVMS 5.4.1@SE25 says a class file that does not satisfy the constraints in 4.9 throws a VerifyError. Neither of those does.

The wording of the first one is the part worth stopping on. A reader who takes `bad type array size` to the specification is looking for a rule about arrays of types. There is no such rule. The file has a `max_stack` that is too small, which is four sentences into 4.9.2 and is one of the first things anybody is taught to break, and the message names neither `max_stack` nor a stack of any kind.

## The stack map reader answers before the verifier does

HotSpot validates the stack map frames while reading them, against the `max_stack` the same attribute declared:

```
  void check_verification_type_array_size(
      int32_t size, int32_t max_size, TRAPS) {
    if (size < 0 || size > max_size) {
      // Since this error could be caused someone rewriting the method
      // but not knowing to update the stackmap data, we call the
      // verifier's error method, which may not throw an exception and
      // failover to the old verifier instead.
      _verifier->class_format_error(
        "StackMapTable format error: bad type array size");
    }
  }
```

That is src/hotspot/share/classfile/stackMapTable.hpp:139@jdk-27+35, called with the frame's stack size and the method's `max_stack` at src/hotspot/share/classfile/stackMapTable.cpp:298@jdk-27+35. The comment explains the choice and it is not an accident. `ClassVerifier::class_format_error` sets `_exception_type` to `java_lang_ClassFormatError` at src/hotspot/share/classfile/verifier.cpp:2073@jdk-27+35, and that error class is what lets the failover to the old type inferring verifier happen instead of a hard failure. The message is worded for the case the comment describes, which is a tool that rewrote a method and left the stack maps behind, rather than for the case that produced it here.

There is a second reason it cannot be a VerifyError, which is that the message arrives from the wrong side of a decision the JVM has already made. JVMS 4.10.1@SE25 permits failover to verification by type inference only for a class file of version 50.0. The seed class here is version 71, so the failover the comment protects can never run for it, and the error class chosen to allow that failover is the error class it gets anyway.

`max_locals` has a plainer story: src/hotspot/share/classfile/classFileParser.cpp:2290@jdk-27+35 checks `args_size <= max_locals` in the parser, immediately after reading the two numbers and before the code array. The parser has one error to throw. A constraint that JVMS 4.9.1@SE25 files under the verifier is checked here because it is cheap here, and the error follows the place rather than the rule.

Neither of these is a bug in HotSpot. Both are the specification's map of the process and HotSpot's map of the process being different maps, and the error class is the visible edge of the difference. A reader who has been taught that ClassFormatError means malformed bytes and VerifyError means malformed logic has been taught something that fails on the first `max_stack` they get wrong.

## Twelve messages that do not name the thing

The second table asks a smaller question of each case: does the message use any of the words the rule uses. The words are taken from the quoted sentence rather than chosen, so this measures whether the two texts are talking about the same item in the same language. 12 of the 27 use none of them.

Four of those are the access flags. `ACC_FINAL` with `ACC_ABSTRACT` on a class, `ACC_INTERFACE` without `ACC_ABSTRACT`, `ACC_FINAL` with `ACC_VOLATILE` on a field, and `ACC_ABSTRACT` with `ACC_FINAL` on a method all produce a message of the same shape: `Illegal class modifiers in class Target: 0x411`. The hex is the whole file's answer. The specification states each of these as a named pair of flags, the VM has the pair in hand at the moment it refuses, and what it prints is the sum. Working out which bit is the problem means decoding 0x411 by hand against Table 4.1-B.

Two more are worth naming. A `CONSTANT_Class` whose `name_index` points at another `CONSTANT_Class` rather than at a Utf8 is refused with `Invalid constant pool index 2`, for an index that is valid and merely holds the wrong kind of entry, which sends a reader to look for an off by one that is not there. A `constant_pool_count` larger than the entries that follow it is refused with `Unknown constant tag 0`, which is the truth about the byte the parser reached and says nothing about the count that sent it there.

## An instruction that no class file may contain

Put `0xdd` where an opcode belongs and the verifier refuses it correctly, with a VerifyError, saying `Bad instruction: dd`. The disassembly under the message labels the location `Target.go()I @0: fast_iaccess_0`.

`fast_iaccess_0` is not an instruction. It is one of the 36 codes HotSpot writes into a method after parsing, which the [opcode table](../generated/opcodes.md) counts and JVMS chapter 7 does not list, so it can exist in memory and can never exist in a file. The generator checks that name against the committed opcode table rather than against a list typed into it, which means this row appears only while it is true. A reader looking `fast_iaccess_0` up in the specification is looking for something that was never allowed in the thing they are holding.

## Forty bit flips that nobody objected to

The random half flips one bit of one byte, 256 times, from a fixed seed, so the same mutants come back on any machine. 174 are refused at `defineClass`, 42 at linking, and **40 load and link with nothing said**.

Where they land explains most of it. 145 of the flips are in the constant pool because the pool is 207 of the file's 368 bytes, and 18 of those are accepted, mostly inside Utf8 bytes that name something nothing else refers to. 16 of the 85 flips in the methods region are accepted. Even the 10 byte header takes a flip and survives: a minor version has no rule attached to it. The whole seed file is 368 bytes and 40 single bit changes to it produce a class the JVM will load without comment, which is the practical form of a point the lessons keep making, that the class file format is checked thoroughly in the places it is checked and not at all elsewhere.

None of the 256 killed the JVM. The probe is built to survive one that does, running the flips in chunks and rerunning a chunk one mutant at a time when it does not come back, because a fuzzer that finds a way to kill the VM has found the most interesting thing on the page and must not lose the rest of the run doing it.

## The specification and the JDK are not describing the same file

The seed class is major version 71, which is what the pinned JDK writes. JVMS SE25 says the major version must be in the range 45 through 69. Every rule quoted on the generated page is quoted from a document that does not describe the file being broken, because no published edition describes it yet, and the pin records both numbers so the gap is a fact on the page rather than a surprise.

The two version cases show the same gap from the other end. A version below 45 is refused with `was compiled with an invalid major version`, and a version above what the JDK reads is refused with `class file version 72.0 ... only recognizes class file versions up to 71.0`. Same item, same VM, two vocabularies, and only the first one uses the specification's word for it.

## What is checked rather than claimed

The generator will not print a claim about the specification that it cannot find in the specification. For each of the 27 cases it fetches JVMS chapters 4 and 5 at the pinned edition, checks the hash of each chapter against the one `tools/gen_jvms_index.py` recorded, and then stops unless the cited section exists, every fragment quoted from it appears in it verbatim, the section named for the error contains the name of that error, and every word the message comparison looks for is a word the quotation itself uses. That last one is what keeps the second table honest: the column cannot be tuned by picking a friendlier word, because a word the quoted sentence does not use is a word the generator refuses.

Two more stops. A case whose rule is in 4.9 or 4.10 must expect a VerifyError and no other case may, which is 5.4.1 written as an assertion rather than as a sentence in a table. And if a JDK ever agrees with the specification on all 27, the generator stops rather than printing a page whose title is contradicted by the table under it.

The two environments agree on all 27 cases and all 256 flips, byte for byte, including every message. That is expected for a property of the class file format and it is worth measuring, because it is what makes each cell one answer rather than an average of two.

## What this means for the lessons

B11's boss fight has a reader build a class file by hand. They will get `max_stack` wrong, because everybody does, and the error they get will not mention it. That is now a page to send them to rather than a thing the author has to remember to warn about, and it turns twenty minutes of confusion into an example of something the lessons want to teach anyway: the error class tells you which part of the JVM was looking, not which rule you broke.

It also sets the standard for how this repository cites the specification. A claim marked `[JVMS]` whose section does not say what the claim says is a P1 bug by `CONTRIBUTING.md` rule 7. `tools/gen_jvms_index.py` made section numbers and titles checkable. This makes the sentences checkable, on 23 of them, and the mechanism is small enough to reuse anywhere a lesson wants to quote a rule and have the quotation stay true.

## Running it yourself

Point `JAVA_HOME` at the pinned JDK. No network, no root, about ten seconds, and it starts a JVM about a dozen times.

```
python probes/classfile-fuzz/run.py --out probes/classfile-fuzz/results/mymachine.json
python tools/gen_fuzz.py
```

`Fuzz.java` measures and says nothing about the specification. Everything about what JVMS names is in `tools/gen_fuzz.py`, next to the section it cites, where a reader can see both sides of the comparison at once. Adding a case means adding one entry there and one in the probe, and the generator will refuse both if the sentence it rests on is not in the chapter.

The generator reaches the network for the two chapters. `--check` regenerates and compares without writing, which is how CI notices that a chapter was republished or that a result file was edited by hand.
