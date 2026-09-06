# 3. Operations

There is one public operation and it has one signature. Everything else in this section is a step inside it, named so that section 4 can say when each step happens and what it may assume about the steps before it.

**derive(bytes, name, loader) returns a class, or throws.** `bytes` is a finite sequence with a known length. `name` is the binary name the caller expects, or absent. `loader` identifies the defining loader, and it is an input rather than a decoration because it selects how much checking is done. The result is an internal class, or one of `ClassFormatError`, `UnsupportedClassVersionError`, `NoClassDefFoundError`, `OutOfMemoryError` or a `LinkageError` raised by the steps that precede parsing. `derive` never returns a partially built class, and an implementation that keeps one after a failure has to make it unreachable, because a class that failed to parse is not a class that failed to link.

The steps run in the order given. Each one may assume every earlier step completed and may assume nothing about a later one.

**3.1 read the header.** Consume four bytes of magic, then two of minor version, then two of major version. Fails with `ClassFormatError` if the magic is wrong, and with `UnsupportedClassVersionError` if the version is outside the range the implementation supports. Produces the version, which every later step may need, because the format is versioned and a structure that is legal at one version is not legal at another.

**3.2 read the constant pool.** Consume the count, then that many entries minus one, each tagged by its first byte. Produces a table indexed from 1 whose entries are not yet cross checked. Fails with `ClassFormatError` on a tag the version does not allow, on an entry that runs off the end, or on a count of zero. The individual entry kinds are BP-CONSTPOOL's, and this step's obligation is to consume exactly the bytes each kind occupies and to leave a table the next steps can index.

**3.3 check the pool.** Resolve every index an entry holds against the table, and check that what it points at is of the kind the entry requires. Produces nothing and consumes nothing. It is a separate step from 3.2 because an entry may refer to an entry that comes after it, so no single pass over the bytes can do both.

**3.4 read the class's own items.** Consume access flags, `this_class`, `super_class`, the interface count and that many interface indices. Fails with `ClassFormatError` on an index that is out of range or points at the wrong kind, and with `NoClassDefFoundError` if `this_class` names something other than the expected `name`, or if the access flags say this is a module rather than a class {[JVMS §5.3.5@SE25]}. This is the step where the error class stops being about bytes and starts being about identity.

**3.5 read the fields, then the methods.** Each is a count followed by that many structures, and each structure is flags, a name index, a descriptor index and its own attribute table. Fails with `ClassFormatError` on a duplicate name and descriptor pair, on flags that contradict each other, or on a descriptor that is not well formed. A method's `Code` attribute is read here, which means `max_stack`, `max_locals` and the code length are read here, and so are the constraints stated about them.

**3.6 read the class attributes.** The same shape as 3.5's attribute tables, attached to the class rather than to a member. An attribute whose name the implementation does not recognise is skipped by its declared length and must not be an error.

**3.7 finish.** Check that the stream is exhausted. Fails with `ClassFormatError` if any byte remains, which is the check that makes the class file a description of the whole input rather than of a prefix of it.

Steps 3.1 to 3.7 are format checking as the specification uses the term {[JVMS §4.8@SE25]}. They do not look at the code array beyond its length, they do not resolve any name to another class, and they do not type check anything. An implementation that passes them has a class file it can hold, not a class it can run, and the difference between those is the whole of BP-VERIFY.

One step deliberately has no place in this list. Nothing here reads the code array, walks the instructions or looks at the stack maps, because the machine that will run those bytes may not be the machine that parsed them, and separating the two is what lets a class file be checked once and executed many times. An implementation is permitted to fold verification into parsing for speed. It is not permitted to fold the errors together, and 4c is where that obligation is written down.
