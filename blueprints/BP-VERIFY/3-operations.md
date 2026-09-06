# 3. Operations

There are three public operations. The first is the relation itself, the second lifts it to a whole frame, and the third decides whether either of them is consulted for a given class. The third is listed here rather than left to a configuration note because a machine that answers it wrongly is a machine whose answers to the other two never reach anybody.

**assignable(from, to, context) returns yes or no, or fails.** `from` and `to` are verification types. `context` supplies the class being verified, which is where the loader comes from, and access to class loading. The result is whether a value described by `from` may occupy a location declared to hold `to`. It is not a pure function of its first two arguments: for a pair of distinct reference types it may load a class, and loading a class may fail, in which case the operation does not return an answer at all.

**frameAssignable(from, to) returns yes or no, or fails.** Both arguments are frames, each a locals array, an operand stack and a flag saying whether any local holds `uninitializedThis`. The result is whether the state described by `from` satisfies everything `to` requires. It calls `assignable` once per position and inherits its failure mode.

**verifierFor(major) returns which verifier answers, and whether its refusal is final.** Its input is the class file major version and the configuration. Its output decides whether `assignable` is consulted for the class at all, and whether a refusal from it ends the load. There is no per class file version behaviour anywhere else in this document, and all of it is here.

The steps of `assignable` run in the order given. Each may assume every earlier step did not return.

**3.1 identical, or the target is `top`.** If the two types are equal, the answer is yes, which is where reflexivity comes from. If `to` is `top`, the answer is yes, and these are the only two rules that need neither type to be a reference nor either class to be loaded.

**3.2 one is a primitive.** If either type is `int`, `float`, `long` or `double` and 3.1 did not fire, the answer is no. There is no widening between primitive verification types, so `int` to `long` is refused and so is `float` to `double`, and an implementation that reuses a language level widening table here is wrong on four pairs.

**3.3 either is uninitialized.** `uninitializedThis` and `uninitialized(Offset)` reach `top` by 3.1 and nothing else. In particular neither is assignable to `java.lang.Object`, so an implementation that treats them as references in this operation accepts a frame that reads an unconstructed object as a constructed one.

**3.4 the source is `null`.** If `from` is `null` and `to` is a class type or an array type, the answer is yes. This is the only rule that crosses from an atom into the reference hierarchy.

**3.5 the target is `null`.** If `to` is `null` and 3.1 did not fire, the answer is no. Nothing widens to `null`.

**3.6 both are references, or the answer is no.** If either type is not a class type or an array type, everything above has already decided it, and the answer here is no. Everything below assumes two distinct reference types.

**3.7 the same name.** If the two names are equal, the answer is yes. Whether the two loaders are also compared is 4a.6 and 4b.7, and it is the one place in this operation where the specification and the pinned implementation are describing different objects.

**3.8 the target is `java.lang.Object`.** The answer is yes for every reference type, including every array type. This is decided by name and loads nothing.

**3.9 the target is an interface.** Load the target. If it is an interface, then the answer is yes when `from` is a class type, whatever class it is, and yes when `from` is an array type only if the target is `java.lang.Cloneable` or `java.io.Serializable`. The source is never loaded and its hierarchy is never consulted, and 4a.4 is why. An implementation that checks whether `from` implements the interface here is stricter than this document and does not conform.

**3.10 both are class types.** Load both and answer whether the target is one of the source's superclasses. This is the only step that walks a hierarchy and the only one whose cost is unbounded by the two types alone.

**3.11 both are array types.** Take the component of each and ask `assignable` about the components, with one change: two primitive components are assignable only when identical, so an array of `byte` does not satisfy an array of `int` even though `byte` is described by the verification type `int`. A component that cannot be parsed out of the signature is not an error and makes the answer no.

**3.12 anything else.** The answer is no. An array is not assignable to a class type other than the three of 3.8 and 3.9, a class type is not assignable to an array type, and there is no case left.

The steps of `frameAssignable` run in the order given, and 3.13 and 3.14 both run before any type is compared.

**3.13 compare the widths.** The two locals arrays have to be the same length. This is an equality and not a bound, because the length is fixed for a method by construction, and an implementation that accepts a target with fewer locals has accepted a frame that says nothing about the rest.

**3.14 compare the stack depths.** The two operand stacks have to be the same depth. This is the check the specification states explicitly, on the grounds that the stack is the one part of a frame whose length is not fixed in advance.

**3.15 compare the locals pointwise.** For each position, `assignable` from the source's type to the target's type. Fails at the first position that refuses, and the position it reports is the location index rather than the local variable index, so the second half of a `long` counts.

**3.16 compare the stack pointwise.** The same, over the operand stack.

**3.17 compare the flags.** The source's flags have to be a subset of the target's. A frame in which `this` is still unconstructed is not assignable to one in which it is constructed, and the reverse is allowed, which is what makes a constructor's merge points work.

The steps of `verifierFor` run in the order given.

**3.18 decide whether to verify at all.** A class whose defining loader the configuration excludes is not verified, and none of 3.1 to 3.17 runs for it. This is a configuration question and section 7 is where the answer lives.

**3.19 choose a verifier by class file version.** At major version 50 and above, the type checking verifier described by this document answers. Below 50, it never runs and the inference verifier of JVMS 4.10.2 answers instead, so nothing in this document applies to those files.

**3.20 decide whether a refusal is final.** At major version 51 and above, a refusal ends the load. At 50 exactly, a refusal of kind `VerifyError` or `ClassFormatError` sends the class to the inference verifier, and if that verifier accepts it the class loads and nothing is reported. An implementation with one verifier refuses at 50 as well, which is a difference from the pinned machine that 8.2.19 requires it to state rather than to remove.

Steps 3.1 to 3.17 are the whole of what this document specifies about accepting or refusing. They establish that a value of one type may occupy a location declared to hold another, and they establish nothing about whether the object at run time is of the type the location claims, which is 6.1's subject and the reason `invokeinterface`, `checkcast` and `aastore` all check again.
