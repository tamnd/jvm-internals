# 8. Conformance

## 8.1 What conformance means here

An implementation conforms to this blueprint if, for every method whose code array satisfies the constraints of section 4a, executing it produces the same observable effects as any other conforming implementation, and if it performs the steps of section 3 in an order consistent with every clause in 4c. Observable means what the program can see: the values it computes, the objects it creates, the exceptions it throws, the locks it holds and the order in which other threads may see its writes. It does not mean what the machine's own diagnostic tools print.

Two limits on that obligation matter more here than in any other blueprint. The first is that it says nothing about a code array that does not satisfy 4a, because for those the specification places no obligation on the machine at all {[JVMS §6.1@SE25]}, and an implementation may return a wrong value, throw anything, or fail in a way no Java program can catch. A conformance item that asserts on the behaviour of an unverified method is therefore not a conformance item, and the one below that touches this asks a different question: whether the implementation documents where its own boundary is.

The second is that the entire contents of section 4b are unobservable by design. The code array a method holds after linking, the layout of a frame, the width of the field that stores a code size and which fused instruction an `aload_0` became are implementation state, and a conforming implementation may make every one of those different. What it may not do is let the difference reach a program, and 8.2.14 and 8.2.17 are the items that test the boundary rather than the choice.

Section 4b clauses are recorded so that a reader comparing two implementations can tell a choice from a requirement. An implementation that departs from one states which clause and what it does instead, in one line. The capstone harness reports against 8.2 and against nothing else, with an item passed when a test exists that would fail if the behaviour changed, skipped when the harness cannot construct the case, and failed otherwise.

## 8.2 Checklist

- **8.2.1** 4a.1, 4a.8: decodes a code array from index 0 by instruction length, reads multi byte operands big endian, pads `tableswitch` and `lookupswitch` operands to a four byte boundary measured from the start of the array, and ends the last instruction at `code_length - 1`.
- **8.2.2** 4a.2, 4c.2: creates one frame per invocation with its own locals, its own operand stack and its own constant pool reference, and destroys it before the invoker resumes, which is checked by walking the stack from inside a recursion.
- **8.2.3** 4a.3, 4a.10: addresses a `long` or a `double` in the local variable array by the lesser of its two indices, at both even and odd indices, and neither requires nor assumes alignment of the pair.
- **8.2.4** 4a.4, 4a.9: starts every frame with an empty operand stack and executes correctly a method whose stack reaches exactly `max_stack` on some path, including a `long` or `double` value at the top of it.
- **8.2.5** 4a.5: gives each thread its own program counter, so two threads executing the same method are at independent points in it, which is checked with two threads in one loop rather than by reading any diagnostic.
- **8.2.6** 4a.6: executes a method whose code array is one byte long and one whose code array is 65535 bytes long, and refuses a class file offering an empty one.
- **8.2.7** 4a.7: executes every opcode the specification documents and refuses, before execution, any code array containing an opcode it does not document, including a machine's own internal codes if it has any.
- **8.2.8** 4a.11: documents which of its own checks are performed at run time and which it relies on verification for, since the specification permits either and a reader cannot tell them apart by testing a machine that is correct.
- **8.2.9** 4a.12, 4c.3: releases the monitor of a `synchronized` method before an exception raised inside it becomes visible to the invoker, checked with `Thread.holdsLock` in the invoker's handler rather than after the stack has unwound further.
- **8.2.10** 4a.13: pushes the returned value onto the invoker's operand stack on normal completion and pushes nothing on abrupt completion, checked for each of the five return instructions and for a `void` method.
- **8.2.11** 4a.14: documents whether it enforces structured locking, and behaves the way it documents for a method that exits a monitor it did not enter and for one that exits two monitors in entry order.
- **8.2.12** 4b.1, 4b.2, 4b.3: documents any limit its own representation of a method imposes beyond the limits section 4a states, and imposes none that is smaller.
- **8.2.13** 4b.4, 4b.5, 4b.6: passes arguments to an invoked method so that they arrive as its first local variables in index order, whatever the direction its stacks grow, checked with a method of eight parameters of mixed widths.
- **8.2.14** 4b.7, 4b.8, 4b.12: produces identical observable behaviour for every program with each of its code rewriting options on and off, which is the item that makes rewriting an implementation detail rather than a semantic.
- **8.2.15** 4b.9, 4c.6: keeps any rewritten operand meaningful only inside the machine, so that reflection, the class file API and any agent see the constant pool index the class file carried and not the rewritten form, and performs no rewrite that depends on a structure it has not yet built.
- **8.2.16** 4b.10: loads and executes every class file that satisfies section 4a, or documents each internal limit that makes it refuse one, since a refusal here is a refusal of a legal program.
- **8.2.17** 4b.11, 4b.13, 4b.14: modifies a code array while threads execute it only by single byte stores after which the instruction sequence beginning at that byte has the same effect and the same end index as before, and reports the original instruction, not the rewritten one, through every interface a program can reach.
- **8.2.18** 4c.1: determines the length of an instruction from its opcode alone, so that no operand of any instruction is fetched before the opcode that gives it meaning.
- **8.2.19** 4c.4, 4c.5: verifies a class's methods before transforming them in any way, and makes the class available to other threads only after both have finished, so no thread can execute a partly transformed method.
