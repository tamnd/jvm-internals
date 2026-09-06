# 8. Conformance

## 8.1 What conformance means here

An implementation conforms to this blueprint if, for every constant pool offered to `read`, it reaches the same decision as section 4a requires and reports a failure with the error class 4a names, if the table it produces answers the same questions about every index that 4a requires, and if `tag_at` obeys every clause in 4c for the life of the pool. Nothing is said about how the table is laid out, how much it costs, what its messages say, or what an implementation does with a pool section 4a does not describe.

Section 4b is not part of that obligation. Those clauses record what HotSpot at the pinned build does, and an implementation may differ from every one of them and conform. What it may not do is differ silently, and the clauses where that matters most are the conditional ones: an implementation whose cross index checking depends on where the class came from has to say so, because a reader who assumes 3.5 always runs will build a component on top of a table that was never checked.

Three consequences are worth stating because they are the ones people get wrong. First, matching a message is never conformance, so a test asserting on the wording of 4b.15 is testing HotSpot rather than the specification, and 4b.15 is the clause where the wording is misleading anyway. Second, an implementation that is stricter than 4a requires does not conform, and 4a.11 is the clause this catches most often, because deduplicating a pool on read is a natural optimisation and it changes the indices a later structure holds. Third, `OutOfMemoryError` is permitted at any allocation, so no item below expects a specific error for a large pool.

The capstone harness reports against 8.2 and against nothing else. An item is passed when a test exists that would fail if the behaviour changed, skipped when the item names a case the harness cannot construct, and failed otherwise. There is no fourth state, and in particular there is no state for an item nobody has looked at.

## 8.2 Checklist

- **8.2.1** 4a.1, 4b.9: refuses every entry whose tag is not one of the seventeen defined values, at every index including the first and the last, and does not attempt to skip such an entry by any assumed length.
- **8.2.2** 4a.2, 4b.10, 4b.11: places the entry after an eight byte constant at the index two further on, refuses any index that names the slot in between, and refuses an eight byte constant declared at the last index.
- **8.2.3** 4a.3, 4b.7: accepts each tag at the lowest class file version that permits it and refuses it at the version below, for all four thresholds, and reports the refusal as a malformed class file rather than as an unsupported version.
- **8.2.4** 4a.4, 4b.3, 4b.4, 4b.5, 4b.6: refuses an `ldc` of a `CONSTANT_Class_info` at a class file version below 49.0 and accepts it at 49.0 and above, whichever of its verifiers runs, which is the item the pinned HotSpot build passes only because the two version thresholds involved happen not to overlap.
- **8.2.5** 4a.5: accepts a `CONSTANT_Utf8_info` of 65535 bytes, treats its length as a byte count rather than a character count, and does not truncate or re-encode a string containing a supplementary character on the way in or out.
- **8.2.6** 4a.6: determines the role of every `CONSTANT_Utf8_info` from the entry or attribute that names it, and never from the bytes, which is checked by presenting the same string as a class name in one class and as a literal in another and observing that both are accepted.
- **8.2.7** 4a.7, 4b.14: checks that each of the three member reference kinds names a `CONSTANT_Class_info` and a `CONSTANT_NameAndType_info` of the right kinds, and either performs that check in every configuration or names the configuration in which it does not.
- **8.2.8** 4a.8: refuses a `CONSTANT_NameAndType_info` whose name is special and whose descriptor does not permit it, and reaches that decision after both halves have been read rather than while either is being read.
- **8.2.9** 4a.9, 4b.15, 4b.16: refuses a `reference_kind` outside 1 to 9, enforces the entry kind each of the nine requires, and permits kinds 6 and 7 to name a `CONSTANT_InterfaceMethodref_info` at class file version 52.0 and above and not below.
- **8.2.10** 4a.10: refuses a `CONSTANT_Dynamic_info` or a `CONSTANT_InvokeDynamic_info` whose bootstrap method index has no matching entry, including the case where the class carries no `BootstrapMethods` attribute at all.
- **8.2.11** 4a.11: accepts a pool containing two entries that denote the same constant, at every entry kind, and does not merge them in a way that changes what any index names.
- **8.2.12** 4a.12, 4b.8, 4c.3: refuses a `CONSTANT_Module_info` or a `CONSTANT_Package_info` outside a module declaration, refuses any index that names one, and reports a file that contains one together with the module access flag as a missing class rather than as a malformed one.
- **8.2.13** 4a.13, 4c.4: creates a class whose pool names a class, a field or a method that does not exist, and resolves no entry of the pool during creation.
- **8.2.14** 4a.14, 4b.2, 4c.6: throws an error of the same class on every use of a reference whose resolution failed, for each of the entry kinds that can fail, without attempting the resolution again.
- **8.2.15** 4b.1, 4b.17: keeps whatever internal states it needs for an entry in a representation no class file can produce, so that no byte sequence in a class file can put an entry into a state parsing alone should not reach.
- **8.2.16** 4b.12: documents, for every configuration it offers, which checks of section 3 are skipped, and either performs every 4a check in every configuration or names the configuration in which it does not.
- **8.2.17** 4b.13: if it shares string storage between classes, derives the same class from the same bytes whether or not another class holding the same strings has been loaded first, and exposes no way for one class to observe that another holds a string it holds.
- **8.2.18** 4b.18, 4b.19, 4c.5: publishes an entry's contents before the kind that describes them, so that no thread observes an entry in a state whose contents are not yet written, and records a resolution failure exactly once however many threads reach it together.
- **8.2.19** 4c.1, 4c.2: reads the class file version before the first entry and every entry before the first index is resolved, which is checked by presenting a pool whose first entry names its last one.
