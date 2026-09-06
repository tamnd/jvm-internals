# 9. Provenance

Section 2 is generated from [the class file structure page](../../docs/generated/classfile.md), which `tools/gen_classfile.py` builds from the pinned specification edition and the pinned JDK's own reader. It is not written here and editing it here has no effect.

Every clause in 4a quotes JVMS SE25 and every clause in 4b names a file and a line in `jdk-27+35`. `tools/refcheck.py` resolves both against `docs/citations.json` and fails the build when a line has moved, so a clause that has drifted off its evidence is a broken build rather than a stale sentence.

Three things in this blueprint are measured rather than read. The counts in section 6.1, the 27 named mutations and the 40 single bit flips that load without complaint, come from `probes/classfile-fuzz` against one 368 byte class file, and a different class file would give different numbers. The copy and no copy finding in 6.3 was read out of `ClassLoader.c` and confirmed by following the direct buffer path to the parser, and it has not been demonstrated by observing a race. The message wordings quoted in 4b were taken from the source rather than from a run.

Two questions this blueprint raises are open. The pinned JDK writes class file major version 71 and the pinned specification edition stops at 69, so 4a describes a format slightly older than the one section 2 is generated from, and the gap closes when `docs/pin.json` moves to an edition that covers 71. And 8.2.11 is an item the pinned build fails, which is a finding about HotSpot rather than a defect in the checklist, and it stays in the list until somebody establishes which of the two documents is wrong.

The reviews field in the front matter is null. Nothing in this blueprint has been read by anybody with production experience of a class file parser, and until it has, the conformance list is one person's reading of the specification and not a standard.
