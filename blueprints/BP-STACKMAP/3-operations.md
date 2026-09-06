# 3. Operations

There are two public operations and the first exists because the second cannot start without it. The attribute is a chain of differences, every link of which is a difference against the link before, and the first link is a difference against a frame that is not in the file. So an implementation computes that frame from the method's own declaration and then reads the attribute against it.

**initial(class, method) returns the frame the first entry is a delta against.** It reads nothing from the attribute. Its inputs are the method's descriptor, its access flags, the class it is declared in and its `max_locals`, and its output is a locals array and an empty operand stack. This is the frame that applies at bytecode offset 0, and it is the one frame of every method that no class file contains.

**read(bytes, method, initial) returns the frames in order, or throws.** `bytes` is the attribute's contents positioned at `number_of_entries` and bounded by `attribute_length`. `method` supplies `max_locals`, `max_stack`, the code array and the constant pool. The result is a sequence of frames, each carrying a bytecode offset, a locals array and an operand stack, in increasing order of offset with no two at the same offset. `read` is the whole of this blueprint's contract and it establishes nothing about whether the frames are correct, only that they are frames: a method whose every frame says the wrong thing passes all of it and fails in BP-VERIFY.

The steps of `initial` run in the order given.

**3.1 decide whether there is a `this`.** A static method has no `this` and its locals begin with the first argument. An instance method other than `<init>` begins with the declaring class. An `<init>` method other than `Object`'s begins with the verification type `uninitializedThis` and sets a flag that stays with the frame until a constructor is chained. `Object`'s own `<init>` begins with `Object` itself.

**3.2 expand the argument types.** Each parameter of the descriptor contributes its verification type, and a parameter of type `long` or `double` contributes that type followed by `top`, because the second of its two locations is a location and has a type. A `boolean`, a `byte`, a `short` and a `char` all contribute `int`.

**3.3 pad to the declared width.** Append `top` until the locals array is `max_locals` long. Fails if the types from 3.1 and 3.2 are already longer than `max_locals`, which is a malformed method rather than a malformed attribute and is reported as one.

The steps of `read` run in the order given. Each may assume every earlier step completed and may assume nothing about a later one. Steps 3.5 to 3.11 repeat once per entry.

**3.4 read the count.** Consume two bytes. A count of zero is legal and describes a method whose only frame is the one 3.1 to 3.3 produced. There is no upper bound to check here beyond the one `attribute_length` already imposes.

**3.5 read the frame type.** Consume one byte. That byte alone decides the kind of the frame, and therefore how many bytes follow it and what they mean. A byte in the reserved range is refused here, and it is refused rather than skipped, because a frame carries no length and there is no way to find the next one past it.

**3.6 find the offset delta.** For a `same_frame` the delta is the frame type itself and no further bytes are consumed. For a `same_locals_1_stack_item_frame` it is the frame type minus 64 and no further bytes are consumed. For every other kind it is a `u2` consumed here. An implementation that reads those two bytes before it has decided the kind has read past the end of a one byte frame.

**3.7 apply the offset arithmetic.** If this is the first entry, the frame's bytecode offset is the delta. Otherwise it is the previous frame's offset plus the delta plus one. The exception applies to exactly one entry per method and to the first one, and an implementation that has not written a separate branch for it is wrong by one on every frame of every method.

**3.8 build the locals array.** A `same_frame`, a `same_locals_1_stack_item_frame` and either extended form take the previous frame's locals unchanged. A `chop_frame` removes the last k local variables, where a local variable of type `long` or `double` counts as one variable and occupies two locations. An `append_frame` adds k local variables to the end, read as `verification_type_info` items, each of which occupies one location or two. A `full_frame` reads a `u2` count and then that many items, and replaces the array. Fails if a chop would leave fewer than zero locals, or if the result is wider than `max_locals`.

**3.9 build the operand stack.** A `same_frame`, a `same_frame_extended`, a `chop_frame` and an `append_frame` all have an empty stack. The two `same_locals_1_stack_item` forms have one `verification_type_info` item, which occupies one location or two. A `full_frame` reads a `u2` count and then that many items. Fails if the result is deeper than `max_stack`.

**3.10 check every verification type as it is read.** An item whose tag is above the largest defined one is refused. An `Object_variable_info`'s index has to be in range and has to name a `CONSTANT_Class_info`. An `Uninitialized_variable_info`'s offset has to be inside the code array and has to be the offset of a `new` instruction. Fails on any of the three.

**3.11 check the offset.** The frame's bytecode offset has to be inside the code array and has to be the offset at which an instruction starts. A frame in the middle of an instruction is not a frame that could ever apply, and it is refused here rather than left for the type checker to trip over.

**3.12 check the count.** When the declared number of entries has been read, the attribute has to be exhausted, and when the attribute is exhausted the declared number has to have been read. Those are two failures and an implementation checks both, because a count that is too small leaves trailing bytes and a count that is too large runs off the end.

Steps 3.4 to 3.12 are all the checking this attribute gets before verification. They establish that the frames are well formed, in order, one per instruction offset, and within the widths the method declared. They establish nothing whatever about whether any frame describes the state the code actually reaches, which is the entire subject of BP-VERIFY and none of this blueprint's.
