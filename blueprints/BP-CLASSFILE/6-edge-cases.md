# 6. Edge cases

## 6.1 Malformed input

Malformed input is the normal case for a parser of this kind, not the exception, because the input is attacker controlled in every deployment that loads a class from anywhere but its own build. An implementation is required to terminate on every input, to produce an error rather than an internal failure, and never to read outside the buffer it was given. HotSpot enforces the last of these in one place, a bounds check inside the stream that every read goes through, and the error it produces is a `ClassFormatError` whose message is `Truncated class file` {[HOTSPOT src/hotspot/share/classfile/classFileStream.cpp:30@jdk-27+35]}.

Three shapes of malformed input behave differently and an implementation has to handle all three. A truncated file runs out of bytes in the middle of a structure. A file with a plausible structure and an impossible value, such as a constant pool index pointing at nothing or a `max_stack` that cannot hold what the code pushes, gets all the way to a semantic check. A file with extra bytes after the last attribute is well formed as far as any reader can tell and is refused anyway {[JVMS §4.8@SE25]}, which means an implementation cannot stop reading when it has what it needs.

The measured behaviour of the third shape is not what a reader expects, and it is recorded rather than assumed. `probes/classfile-fuzz` breaks one valid class file 27 named ways and 256 random ways, and [the resulting page](../../docs/generated/classfile-fuzz.md) shows that two of the 27 produce an error class the specification does not name for them, and that 12 of the 27 produce a message that uses none of the words the rule uses. The two mismatches are both constraints from JVMS 4.9, which 5.4.1 protects with a `VerifyError`, and both arrive as a `ClassFormatError`.

Single bit corruption is the case an implementation is most likely to meet in production and the one it is least able to detect. 40 of 256 single bit flips of a 368 byte class file load and link with nothing said, because most of the bytes in a class file are either payload that nothing validates or a value whose neighbouring values are equally legal. An implementation that reports success on those 40 is conforming. A reader who concludes from that that the format is checked is wrong, and the number is here so the conclusion is available without running anything.

## 6.2 Resource exhaustion

Every count in a class file is read before the thing it counts, which is the property that makes exhaustion possible: a two byte field can ask for 65535 constant pool entries, 65535 fields or 65535 methods before a single one of them has been read {[JVMS §4.11@SE25]}. An implementation that allocates on the declared count is allocating on attacker controlled input. HotSpot does allocate the constant pool at the declared size before parsing a single entry {[HOTSPOT src/hotspot/share/classfile/classFileParser.cpp:5530@jdk-27+35]}, which is a defensible choice because the pool is bounded at 65535 entries by the width of the field, and it is a choice an implementation with a wider field would not be able to copy.

The bounded fields are what makes the format safe to parse eagerly, and the boundedness is the whole of the safety argument. The constant pool, the fields, the methods and the direct superinterfaces are each capped at 65535 by a `u2`. The operand stack and the local variable array of one frame are capped at 65535 the same way. The code array of one method is capped at 65535 by a check rather than by a field, because the field is a `u4` and the limit is smaller than the field, so an implementation that trusts the width gets a method body of up to four gigabytes {[HOTSPOT src/hotspot/share/classfile/classFileParser.cpp:2293@jdk-27+35]}.

That check is one of the ones HotSpot runs only when it is verifying. A class file loaded by the boot loader in a default configuration is not format checked to that depth, so the code length bound is not enforced on it, and the parser instead meets the end of the buffer and reports a truncated file. Neither outcome is a crash, and the difference between them is the difference between a diagnosable error and one that names the wrong thing.

An implementation is permitted to run out of memory here and `OutOfMemoryError` is not a `ClassFormatError`. A conforming machine may throw it at any allocation during derivation, so a harness that asserts on the exact error for a large input is asserting about the machine's heap and not about the class file.

## 6.3 The concurrent case

Parsing is a pure function of the bytes, so there is no concurrency inside it, and every concurrent hazard here is about the buffer rather than about the parser. There is exactly one, and it is decided by which overload of `defineClass` the caller reached for.

The `byte[]` overload copies. The native side allocates its own buffer and copies the array out of the Java heap before the machine sees it {[HOTSPOT src/java.base/share/native/libjava/ClassLoader.c:115@jdk-27+35]}, so a second thread writing to that array while the class is being defined cannot change the class that comes out. The class is derived from the snapshot.

The direct `ByteBuffer` overload does not copy. The address of the buffer's own memory is handed to the machine {[HOTSPOT src/java.base/share/native/libjava/ClassLoader.c:176@jdk-27+35]} and the parser reads it in place {[HOTSPOT src/hotspot/share/prims/jvm.cpp:887@jdk-27+35]}. A second thread writing to that buffer during the call is racing with the parser, and the resulting class is derived from bytes that were never all present at the same instant. The platform's own class loaders take this route, and everybody else's buffer is copied into a confined arena first, so the race is reachable from Java only through a loader that is a `BuiltinClassLoader`, and it is reachable from JNI by anybody.

An implementation is therefore required to specify which of those it does, and a specification that is silent about it is a specification with a security hole in it. The requirement this blueprint states is the weaker and checkable one: the class an implementation derives has to be a class that some single snapshot of the buffer would have derived. Copying first satisfies it. Reading in place does not, and HotSpot does not claim it does.

The other concurrency question people expect here belongs elsewhere. Two threads defining the same name through the same loader, and which of them wins, is a property of loading rather than of parsing, and it is BP-LOAD's to specify.
