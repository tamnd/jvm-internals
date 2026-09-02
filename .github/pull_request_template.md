## What this changes

<!-- One or two sentences. What a reader gets that they did not have before. -->

## Checks

- [ ] `python tools/prosecheck.py .` is clean
- [ ] No number in the prose was typed by hand
- [ ] Every source citation is `path:line@jdk-27+35` and resolves at its recorded hash
- [ ] Every specification citation names a section, not a chapter, and the quoted text is in it
- [ ] Every claim carries a `[JVMS]` or a `[HOTSPOT]` marker, and none of them is guessed
- [ ] Any claim about what is default, deprecated, preview or in progress carries a date

## For a lesson

- [ ] Notebook regenerated from the lesson source, not hand edited
- [ ] Runs cold, top to bottom, from the Colab badge, under six minutes
- [ ] Every measurement JShell would distort was taken in a `jvx.run` subprocess
- [ ] Every nondeterministic cell is tagged `bake` and shows its marker
- [ ] Every `env=E1` cell has a replay recording
- [ ] Every widget has a working static fallback
- [ ] Boss fight has a grader and the failure message names the input
- [ ] Beginner review
- [ ] Expert review

## For a blueprint

- [ ] All nine sections, no reference to the chapter
- [ ] Section 4 split into JVMS mandated, HotSpot chosen and ordering obligations
- [ ] Section 6, the edge cases, written before section 3
- [ ] Generated sections generated, with nothing hand written duplicating them
- [ ] `bpc check` passes

## For a benchmark

- [ ] JVM version and build, flags, hardware, forks and iterations all stated
- [ ] The warmup curve is shown rather than assumed
- [ ] No number compared across machines
