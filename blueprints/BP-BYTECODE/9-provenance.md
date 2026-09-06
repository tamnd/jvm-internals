# 9. Provenance

Section 3 is generated from [the instruction set page](../../docs/generated/opcodes.md), which `tools/gen_opcodes.py` builds from three sources that check each other: JVMS SE25 chapter 7, `src/hotspot/share/interpreter/bytecodes.cpp` at the pinned tag, and `java.lang.classfile.Opcode` read off the pinned JDK. It is not written here and editing it here has no effect. The counts on it come from `probes/opcodes`, over `java.base` on two platforms, and they are counts of what one compiler emitted for one module rather than facts about the instruction set.

Every clause in 4a quotes JVMS SE25 and every clause in 4b names a file and a line in `jdk-27+35`. `tools/refcheck.py` resolves both against `docs/citations.json` and fails the build when a line has moved.

Everything in this blueprint that concerns a frame's layout was read from the aarch64 port. The three clauses that name `frame_aarch64.hpp` are the ones a different port is most likely to contradict, and they are in 4b rather than in 4a for exactly that reason. Nobody has checked them against x64, and until somebody does, a reader on another architecture should treat 4b.4, 4b.5 and 4b.6 as facts about one port.

The rewriting clauses were read from the source and not demonstrated. The one measured fact here is the fuzzer's, that breaking a byte to `0xdd` produces a message naming `fast_iaccess_0`, which is what proves the internal opcode numbering is reachable from a diagnostic. Nobody has yet watched an `aload_0` turn into a fused instruction in a running process, which is item 5.16 and is the observation this blueprint most needs.

Two things are open. No lesson has been written against this blueprint, so the `lessons` field is empty rather than aspirational. And section 8.2 has an item, 8.2.14, that asserts a property of every program rather than of one input, which no finite harness can establish, so it will be reported as a bounded check over a fixed corpus and the bound will be stated where it runs.
