# 3. Operations

There are two public operations. The first turns bytes into a table and is finished before the class exists. The second is how everything else in the machine touches that table for the rest of the class's life, and it exists as an operation rather than as a field access because the answer it gives changes over time and the rules for that change are most of section 4.

**read(bytes, version) returns a pool, or throws.** `bytes` is positioned at `constant_pool_count` and has a known end. `version` is the major and minor version already read by the enclosing parse, and it is an input rather than a constant because which entry kinds are legal is a function of it. The result is a table indexed from 1 whose entries are internally consistent, or a `ClassFormatError`. `read` consumes exactly the bytes the pool occupies and leaves the stream positioned at `access_flags`, which is what makes it callable as a step of BP-CLASSFILE's `derive` rather than as a parser of its own.

**tag_at(pool, index) returns the kind an entry currently has.** The kind is not fixed for the life of the pool. An entry that arrived as a `CONSTANT_Class` may be unresolved, resolved or in error, and an implementation is free to represent those as three states of one entry or as three separate fields, but it is not free to let a reader see a state that never existed. `tag_at` is the operation every clause about ordering and concurrency is written against.

The steps of `read` run in the order given. Each may assume every earlier step completed and may assume nothing about a later one.

**3.1 read the count.** Consume two bytes. A count of zero is refused, because the table has `count - 1` entries and there is no such thing as a table of minus one. A count of one is a pool with no entries and is legal.

**3.2 read the entries.** For each index from 1 up to `count - 1`, consume one tag byte, decide the structure from it, and consume exactly the bytes that structure occupies. Fails with `ClassFormatError` on a tag no version defines, on a tag this version does not allow, or on an entry that runs off the end. No index inside any entry is followed, checked or dereferenced in this step.

**3.3 skip the second slot.** After a `CONSTANT_Long_info` or a `CONSTANT_Double_info`, advance the index by one more. The skipped index counts against the declared count and holds nothing, and an implementation records it as a state distinguishable from every legal entry kind, because a later index that names it is an error rather than a lookup that quietly succeeds. Fails if the skipped index would be past the end, since an eight byte constant cannot be the last entry.

**3.4 stop early if the pool cannot be cross checked.** If step 3.2 met a `CONSTANT_Module` or a `CONSTANT_Package` and recorded it rather than failing, `read` returns here with the pool marked. The enclosing parse needs the access flags to decide which error this is, and the second pass would fail first on something less specific.

**3.5 check every index.** For each entry in turn, resolve each index it holds against the table, and check that the entry it names is of the kind this entry requires: a `CONSTANT_Class` names a Utf8, a `CONSTANT_Fieldref` names a class and a `NameAndType`, a `CONSTANT_MethodHandle`'s `reference_index` names one of a small set decided by its `reference_kind`. Fails with `ClassFormatError` on an index out of range, on an index naming the wrong kind, and on an index naming the second slot of an eight byte constant. This is a separate pass from 3.2 because an entry may name an entry that comes after it, and every class file a compiler has ever produced contains such a reference.

**3.6 check the contents that need a second entry to read.** A name is an unqualified name or `<init>` or `<clinit>` depending on the descriptor its `NameAndType` pairs it with, and a descriptor is well formed or it is not, so both checks need the Utf8 the index names and therefore cannot happen in 3.2. Fails with `ClassFormatError`.

**3.7 rewrite what the implementation stores.** An implementation is permitted to replace an entry's parsed form with a form of its own, and this step exists so section 4 can say when. What it may not do is change which kind an entry is, or make an index that was valid in 3.5 invalid afterwards.

Steps 3.1 to 3.7 are the pool's share of format checking as the specification uses the term {[JVMS §4.8@SE25]}. They establish that the table is internally consistent and they establish nothing at all about whether anything it names exists. A `CONSTANT_Class` naming a class that was deleted last year passes every one of them.

The steps of `tag_at` are not sequential and are stated as three obligations.

**3.8 an entry has one kind at a time.** Every read returns a kind the entry has actually been in, and never a mixture of two, whatever the implementation stores alongside the kind.

**3.9 the kind is what publishes the entry.** A reader that observes an entry in a new state observes everything the transition into that state wrote. This is the obligation that makes the tag a memory barrier rather than a byte.

**3.10 the transition into an error state happens once.** Whichever thread records the failure, every later read sees the error state, and no read after that sees the entry unresolved again.
