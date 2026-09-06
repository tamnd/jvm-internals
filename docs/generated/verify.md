# BP-VERIFY section 2. The verifier's type system

<!-- generated: tools/gen_verify.py from JVMS 4.10.1.2@SE25, `src/hotspot/share/classfile/verificationType.cpp`, `src/hotspot/share/classfile/verifier.hpp` and `src/hotspot/share/classfile/verifier.cpp` at `jdk-27+35`, and probes/verify/results -->

JVMS 4.10.1.2@SE25 defines which verification type may stand in for which other, and it defines it as 22 Prolog clauses over twelve type terms. This page does not paraphrase them. It reports one class file per ordered pair of sixteen types, built and handed to the pinned JVM, and the relation is which of the 256 loaded.

## The types the grid measures

| # | type | what it is | in the hierarchy |
| --- | --- | --- | --- |
| 1 | `top` | the type of a location the verifier will not let you read | an atom the diagram draws |
| 2 | `int` | an `int`, and every smaller integral type | an atom the diagram draws |
| 3 | `float` | a `float` | an atom the diagram draws |
| 4 | `long` | a `long`, in the first of its two locations | an atom the diagram draws |
| 5 | `double` | a `double`, in the first of its two locations | an atom the diagram draws |
| 6 | `null` | the `null` reference | an atom the diagram draws |
| 7 | `uninitializedThis` | `this`, in a constructor that has not chained yet | an atom the diagram draws |
| 8 | `uninitialized(Offset)` | an object the `new` at that offset made, unconstructed | an atom the diagram draws |
| 9 | `Object` | `java.lang.Object`, the top of the reference hierarchy | a `class(N, L)` |
| 10 | `String` | `java.lang.String`, a final class | a `class(N, L)` |
| 11 | `Runnable` | `java.lang.Runnable`, an interface `String` does not implement | a `class(N, L)` |
| 12 | `Cloneable` | `java.lang.Cloneable`, an interface arrays implement | a `class(N, L)` |
| 13 | `Serializable` | `java.io.Serializable`, the other interface arrays implement | a `class(N, L)` |
| 14 | `Object[]` | an array of references | an `arrayOf(X)` |
| 15 | `String[]` | an array of a narrower reference type | an `arrayOf(X)` |
| 16 | `int[]` | an array of a primitive | an `arrayOf(X)` |

The hierarchy draws twelve atoms and puts the two reference shapes, `class(N, L)` and `arrayOf(X)`, in a box rather than naming them, which is fourteen type terms in all. Ten of them can be written into a class file. `oneWord`, `twoWord`, `reference` and the abstract `uninitialized` exist to make the rules shorter, and no verification_type_info can name any of the four, which is why the grid measures the ones it does.

## The relation

`y` means a class file that produced the row's type and declared the column's type in a stack map frame was loaded. `.` means it was refused with `VerifyError`.

| from \ to | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1. `top` | y | . | . | . | . | . | . | . | . | . | . | . | . | . | . | . |
| 2. `int` | y | y | . | . | . | . | . | . | . | . | . | . | . | . | . | . |
| 3. `float` | y | . | y | . | . | . | . | . | . | . | . | . | . | . | . | . |
| 4. `long` | y | . | . | y | . | . | . | . | . | . | . | . | . | . | . | . |
| 5. `double` | y | . | . | . | y | . | . | . | . | . | . | . | . | . | . | . |
| 6. `null` | y | . | . | . | . | y | . | . | y | y | y | y | y | y | y | y |
| 7. `uninitializedThis` | y | . | . | . | . | . | y | . | . | . | . | . | . | . | . | . |
| 8. `uninitialized(Offset)` | y | . | . | . | . | . | . | y | . | . | . | . | . | . | . | . |
| 9. `Object` | y | . | . | . | . | . | . | . | y | . | y | y | y | . | . | . |
| 10. `String` | y | . | . | . | . | . | . | . | y | y | y | y | y | . | . | . |
| 11. `Runnable` | y | . | . | . | . | . | . | . | y | . | y | y | y | . | . | . |
| 12. `Cloneable` | y | . | . | . | . | . | . | . | y | . | y | y | y | . | . | . |
| 13. `Serializable` | y | . | . | . | . | . | . | . | y | . | y | y | y | . | . | . |
| 14. `Object[]` | y | . | . | . | . | . | . | . | y | . | . | y | y | y | . | . |
| 15. `String[]` | y | . | . | . | . | . | . | . | y | . | . | y | y | y | y | . |
| 16. `int[]` | y | . | . | . | . | . | . | . | y | . | . | y | y | . | . | y |

65 of 256 cells are assignable. Every one of the 191 refusals came back in one sentence shape:

- 191 times: `Type <type> (current frame, locals[N]) is not assignable to <type> (stack map, locals[N])`

## What kind of relation this is

Everybody who writes about a type hierarchy assumes an order, and an order is reflexive, transitive and antisymmetric. This one is reflexive on all sixteen types and it is neither of the other two.

It is not transitive. Nine triples have `a` assignable to `b` and `b` assignable to `c` and `a` not assignable to `c`:

- `Object[]` to `Object` to `Runnable`
- `Object[]` to `Cloneable` to `Runnable`
- `Object[]` to `Serializable` to `Runnable`
- `String[]` to `Object` to `Runnable`
- `String[]` to `Cloneable` to `Runnable`
- `String[]` to `Serializable` to `Runnable`
- `int[]` to `Object` to `Runnable`
- `int[]` to `Cloneable` to `Runnable`
- `int[]` to `Serializable` to `Runnable`

It is not antisymmetric. Six unordered pairs are assignable both ways, which closes into one group of types the verifier cannot tell apart:

- `Object`, `Runnable`, `Cloneable`, `Serializable`

So it is a preorder. The largest group has four members and it contains `java.lang.Object`, which is the finding: to the verifier's type system, `Object` and an interface are the same type.

## Where the implementation says the same thing

src/hotspot/share/classfile/verificationType.cpp:74@jdk-27+35 decides every reference to reference cell in the grid, and it says in a comment what the grid measures:

```c++
  if (is_intf && (!from_field_is_protected ||
      from_name != vmSymbols::java_lang_Object())) {
    // If we are not trying to access a protected field or method in
    // java.lang.Object then, for arrays, we only allow assignability
    // to interfaces java.lang.Cloneable and java.io.Serializable.
    // Otherwise, we treat interfaces as java.lang.Object.
    return !from_is_array ||
      target_klass == vmClasses::Cloneable_klass() ||
      target_klass == vmClasses::Serializable_klass();
  } else if (from_is_object) {
```

The last clause is the array rows. An array reaches `java.lang.Cloneable` and `java.io.Serializable` because they are named, and no other interface, which is why every non transitive triple in this grid ends at an interface an array cannot reach.

## The clauses this is a measurement of

JVMS 4.10.1.2@SE25 gives the relation as 22 clauses. Five of them are `isWideningReference`, and those carry every case the grid finds interesting:

```prolog
isWideningReference(class(_, _), class(To, L)) :- loadedClass(To, L, ToClass), classIsInterface(ToClass).
isWideningReference(class(From, L1), class(To, L2)) :- From \= To, loadedClass(From, L1, FromClass), loadedClass(To, L2, ToClass), loadedSuperclasses(FromClass, Supers), member(ToClass, Supers).
isWideningReference(class(ClassName, L1), class(ClassName, L2)) :- L1 \= L2, loadedClass(ClassName, L1, Class), loadedClass(ClassName, L2, Class).
isWideningReference(arrayOf(_), class(ClassName, L)) :- (ClassName = 'java/lang/Object' ; ClassName = 'java/lang/Cloneable' ; ClassName = 'java/io/Serializable') loadedClass(ClassName, L, LoadedClass), classDefiningLoader(LoadedClass, BL), isBootstrapLoader(BL).
isWideningReference(arrayOf(X), arrayOf(Y)) :- isWideningReference(X, Y).
```

One of those clauses does not parse. Prolog separates the goals of a clause body with commas, and this one has none between the parenthesised disjunction and the goal after it, which is where the text reads `loadedClass(ClassName,`. A Prolog system asked to consult 4.10.1.2@SE25 as written rejects it. Nothing depends on the text being executable, which is how a defect survives in a normative document: nobody has ever run it.

## Two verifiers, and which one answers

A class whose stack map frame is wrong is not always refused. src/hotspot/share/classfile/verifier.hpp:40@jdk-27+35 and src/hotspot/share/classfile/verifier.cpp:66@jdk-27+35 hold the two numbers that decide it:

- `STACKMAP_ATTRIBUTE_MAJOR_VERSION` is 50
- `NOFAILOVER_MAJOR_VERSION` is 51

| major | wrong frame | correct frame | which verifier answered |
| --- | --- | --- | --- |
| 49 | loads | loads | the old one, which never reads the attribute |
| 50 | loads | loads | the new one, then the old one |
| 51 | `VerifyError` | loads | the new one, and that is final |
| 52 | `VerifyError` | loads | the new one, and that is final |
| 71 | `VerifyError` | loads | the new one, and that is final |

The two rows that load are the two rows where nothing is reported to the program. A class file at major 50 with a frame the new verifier refuses is verified again by the old one and loads, and the only way to see that happen is `-Xlog:verification=info`.

## What the accepted half costs

A class that puts a `java.lang.String` in a local its stack map frame declares as `java.lang.Runnable` verifies. So does the same class with an `invokeinterface` of `Runnable.run()` on that local. Running it throws:

```
java.lang.IncompatibleClassChangeError: Class java.lang.String does not implement the requested interface java.lang.Runnable
```

The control is the same class with `java.lang.Thread`, a class rather than an interface, where `Runnable` was. It is refused:

```
Type 'java/lang/String' (current frame, locals[1]) is not assignable to 'java/lang/Thread' (stack map, locals[1])
```

## How much of a module rests on that

Every instruction below carries a check the verifier declined to make. Counted over `java.base` in two environments, 14,911 classes and 124,849 methods with code, 3,902,996 instructions in total.

| instruction | count | what is checked at run time | what it throws |
| --- | --- | --- | --- |
| `invokevirtual` | 240,622 | the receiver has the method | `AbstractMethodError` |
| `aastore` | 121,076 | the element fits the array's real component type | `ArrayStoreException` |
| `invokestatic` | 107,702 | the class resolves | `NoSuchMethodError` |
| `invokespecial` | 88,609 | the resolved method is callable here | `AbstractMethodError` |
| `new` | 68,112 | the class resolves | `NoClassDefFoundError` |
| `invokeinterface` | 52,507 | the receiver implements the interface | `IncompatibleClassChangeError` |
| `athrow` | 33,009 | the thrown reference is a `Throwable` | `VerifyError`, at verify time |
| `checkcast` | 28,489 | the reference is of the named type | `ClassCastException` |
| `instanceof` | 5,789 | the reference is of the named type | nothing, it answers |
| `invokedynamic` | 4,887 | the call site links | `BootstrapMethodError` |

`invokeinterface` is the one this page is about: 52,507 sites where the verifier let a reference through on the strength of a type it cannot distinguish from `Object`, each one resting on a check made again when the call runs.

## Cells worked out by hand

The grid is 256 answers from a machine, and nothing in it would notice if the harness built the same class 256 times. These twelve are the ones a reader of JVMS 4.10.1.2 can settle on paper. The probe writes no results file when a measured answer differs from the worked one.

| pair | worked on paper | measured | agrees |
| --- | --- | --- | --- |
| `int` to `top` | assignable | assignable | yes |
| `int` to `int` | assignable | assignable | yes |
| `int` to `float` | refused | refused | yes |
| `long` to `int` | refused | refused | yes |
| `null` to `String` | assignable | assignable | yes |
| `Object` to `String` | refused | refused | yes |
| `String` to `null` | refused | refused | yes |
| `String` to `Object` | assignable | assignable | yes |
| `Object[]` to `String[]` | refused | refused | yes |
| `String[]` to `Object[]` | assignable | assignable | yes |
| `int[]` to `Object` | assignable | assignable | yes |
| `int[]` to `Object[]` | refused | refused | yes |

## What this was read from

- JVMS SE25 chapter 4, which matches the hash in docs/generated/jvms-index.json: `5d97239610560daf`
- `src/hotspot/share/classfile/verificationType.cpp` at `jdk-27+35`: `1d367c0cd502b2a3`
- `src/hotspot/share/classfile/verifier.hpp` at `jdk-27+35`: `7cef7c216864d9da`
- `src/hotspot/share/classfile/verifier.cpp` at `jdk-27+35`: `bf76b8e16c4490ce`

- 2 environments measured the same 256 cells and agreed on every one

