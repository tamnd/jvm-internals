# Emitting a section 2 that nobody transcribed

Every blueprint has a section 2 that states the VM's internal layout of the thing the blueprint is about, and the whole point of the format is that this section is generated. [The type database probe](sa-types.md) settled who is allowed to read that layout and proved the numbers are reachable without privileges on the machine this repository is built on. It ended on the question this document answers: what the generated section should say, and what it should say about the things the source does not know.

The answer is [`tools/gen_section2.py`](../../tools/gen_section2.py), which writes [the section](../generated/section2-object-header.md) for `markWord`, `oopDesc`, `Klass` and `InstanceKlass`. It is the last piece of the M0 gate.

## Three sources, because no single one has it

The obvious design is one source and one generator, and it does not work here. The type database knows the structs and knows nothing about the mark word's bits, because `markWord` is a type with a size and no exported fields. The header parse in [`tools/gen_markword.py`](../../tools/gen_markword.py) knows the bits and nothing about the structs that carry them. And both describe a layout that is contingent on flags neither of them records: with `UseCompactObjectHeaders` off, the top 22 bits of the mark word are unused and the class pointer is the separate field the `oopDesc` struct declares, and both of those tables would still print exactly as they print now.

So the generator reads three things: `probes/sa/results/*.json` for the structs, `docs/generated/markword.json` for the bits, and `probes/capability/results/*.json` for the configuration. The configuration comes first in the output for that reason. A reader who changes one of those settings is reading a different section 2, and the document should say so before it says anything else rather than in a footnote.

The four capability environments disagree about exactly one of those settings, which is the architecture, and the section prints both values with the environments that reported each. Grouping rather than reducing is deliberate. A section that printed one architecture would be a section about one machine, and the moment a second architecture is measured for the structs, that is the row that has to stop being a single value.

## What the checks are for

"Emits a correct section 2" is the gate, and the word correct only means something if the generator can tell when it is not. Three ways the inputs could be wrong would produce a document that looks entirely right, so each of them stops the generator rather than being written out.

The mark word fields must tile the word: sorted by shift, each field starts where the last one ended, and the last one ends at bit 64. They do, `2 + 1 + 4 + 4 + 31 + 22`, and a header change or a parser slip that left a hole would otherwise be published as a bit layout with a hole in it. Every non-static field must lie inside the type that declares it, and no two may share an offset. And a subclass's own fields must start at or after its superclass's size, which for `InstanceKlass` at 200 and `Klass` at 200 bytes is the one relationship in the whole document that two independent numbers agree on.

The fourth check is that all four named types are present. A build that lacks one produces an error rather than a section with three of them, because a gate met by printing less than it promised is a gate that was lowered.

## What the section says it does not know

The type database exports what `vmStructs` chose to export, which is less than the struct, and nothing in it distinguishes a field that is wide from a field that was not published. Section 2.4 lists the ground between consecutive exported offsets and interprets none of it. That table is the honest form of what the earlier report described in prose.

Two things about that table needed a decision. A subclass's fields start after its superclass, so measuring the first gap from offset 0 would report `InstanceKlass` as having 200 bytes of missing struct when those bytes are `Klass`. The gap is measured from the superclass's size when the superclass is one of the types here, and there is a test that the row starting at 0 never appears. `Klass` itself does start at 8 rather than 0, because it extends `Metadata`, which nothing asked the probe for, and the section says that in words rather than pretending the first 8 bytes are unaccounted for.

The `oopDesc` caveat is carried through verbatim from the sa-types finding, because it is the one sentence that stops a reader believing the struct. `oopDesc` is 16 bytes with a `_compressed_klass` field at 8, the header on this same build is 8 bytes, and both are true: the database describes the struct as the compiler laid it out, and whether a field in it is used is a runtime decision the struct does not record.

## The format, which is a proposal

`bpc` does not exist yet, and neither does the nine section blueprint skeleton it will fill. Rather than invent the whole format in order to write one section of it, this is built as another house generator: a numbered section with a stable heading, regenerated by a script, checked in CI with `--check`, and living in `docs/generated/` with everything else that is derived from a measurement. When `bpc` arrives this becomes its section 2 emitter and the output moves next to the blueprint. The numbering inside it, 2.1 through 2.4, is the part most likely to change, and none of it is prose anyone would have to rewrite.

## Running it

```
python tools/gen_section2.py
python tools/gen_section2.py --check
python tools/test_gen_section2.py
```

It reads files and nothing else. It needs no JDK, no network and no privileges, because everything it needs was measured by the probes and committed. The measurements themselves are not cheap to reproduce, and [the sa-types report](sa-types.md) says what it takes.

## What is still open

This is section 2 for four types on one platform's struct layout. The capability inputs cover four environments and the struct inputs cover two, both Linux, because macOS refused every route into the type database for an ordinary user. A macOS or Windows column for the structs needs a route this project does not have yet rather than another run of this generator.

The other eight sections of the blueprint are not written and are not this file's subject. What issue #3 asked for was whether the layout could be generated rather than transcribed, and for these four types it now is.
