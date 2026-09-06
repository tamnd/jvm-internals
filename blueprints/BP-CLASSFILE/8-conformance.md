# 8. Conformance

## 8.1 What conformance means here

An implementation conforms to this blueprint if, for every finite sequence of bytes offered to `derive`, it reaches the same decision as section 4a requires and reports a failure with the same error class 4a names, and if it performs the steps of section 3 in an order consistent with every clause in 4c. Nothing is said about how long it takes, how much it allocates, what its messages say, or what it does with a class file that section 4a does not describe.

Section 4b is not part of that obligation. Those clauses record what HotSpot at the pinned build does, and an implementation is free to differ from every one of them and still conform. What it is not free to do is differ silently. An implementation that departs from a 4b clause states which clause and what it does instead, in one line, because the value of 4b is that a reader can tell a choice from a requirement, and that value is lost if two implementations disagree without saying so.

Three consequences of this are worth stating because they are the ones people get wrong. First, matching a message is never conformance, so a test that asserts on `Incompatible magic value 1347093252` is testing HotSpot rather than the specification. Second, an implementation that is stricter than 4a requires does not conform, because refusing a class file the specification requires it to accept is a failure in the same way accepting a bad one is, and 4a.5 is the clause this catches most often. Third, `OutOfMemoryError` is permitted at any point and is never evidence of anything, so a checklist item that expects a specific error for a large input is unfalsifiable and is not in the list below.

The capstone harness reports against 8.2 and against nothing else. An item is passed when a test exists that would fail if the behaviour changed, skipped when the item names a case the harness cannot construct, and failed otherwise. There is no fourth state, and in particular there is no state for an item that nobody has looked at.

## 8.2 Checklist

- **8.2.1** 4a.1, 4b.1: refuses every input whose first four bytes are not `0xCAFEBABE`, including the empty input and an input of fewer than four bytes, and does so before interpreting anything else.
- **8.2.2** 4a.2, 4b.2, 4b.3, 4b.4: accepts exactly the major versions it documents, refuses the ones below and above with `UnsupportedClassVersionError`, and treats a minor version of 65535 as a preview class file rather than as an ordinary one.
- **8.2.3** 4a.3, 4b.14, 4c.2: indexes the constant pool from 1 to `constant_pool_count - 1`, resolves an entry that refers to a later entry, and does not allocate on the declared count in a way that a count of 65535 with no entries behind it can exhaust.
- **8.2.4** 4a.4, 4c.6: refuses a truncated file and a file with bytes after the last class attribute, and reaches the second decision only after every attribute it recognises has been consumed.
- **8.2.5** 4a.5, 4b.12: loads a class carrying an attribute with an unrecognised name on the class, on a field, on a method and inside a `Code` attribute, and reports no error and no warning for any of the four.
- **8.2.6** 4a.6, 4c.3: distinguishes the three failures of derivation, `ClassFormatError` for a representation that is not a `ClassFile` structure, `NoClassDefFoundError` for one that names a different class or has `ACC_MODULE` set, and reaches the second of those even when the file also contains a constant pool tag it does not recognise.
- **8.2.7** 4a.7, 4b.5: loads a class file with reserved `access_flags` bits set, and whatever it does with those bits, reports them consistently through every interface it offers.
- **8.2.8** 4a.8: refuses `ACC_INTERFACE` without `ACC_ABSTRACT`, `ACC_FINAL` together with `ACC_ABSTRACT` on a class, and every contradictory flag pair Table 4.1-B forbids on a field and on a method.
- **8.2.9** 4a.9: refuses a `CONSTANT_Utf8_info` containing a zero byte or a byte in the range `0xf0` to `0xff`, and accepts a supplementary character encoded as a surrogate pair of two three byte sequences.
- **8.2.10** 4a.10, 4b.9: accepts a class at each of the format's stated limits, 65535 fields, 65535 methods and 65535 superinterfaces, and refuses a method whose code array exceeds 65535 bytes whatever the width of the field says.
- **8.2.11** 4a.11, 4b.8, 4b.15: reports a violation of a static or structural constraint on a method's code as `VerifyError` rather than as `ClassFormatError`, which is the item the pinned HotSpot build fails for `max_stack` and for the argument count against `max_locals`.
- **8.2.12** 4b.6, 4b.7: documents, for every configuration it offers, which checks are skipped, and either performs every 4a check in every configuration or names the configuration in which it does not.
- **8.2.13** 4b.10, 4b.11: derives, for every entry point that takes a buffer the caller can still write to, a class that some single snapshot of that buffer would have derived, or documents that the entry point is unsafe under concurrent modification.
- **8.2.14** 4b.13: terminates on every input without reading outside the buffer it was given, which is checked by a fuzzer rather than by a test, and reports a diagnosable error for each of the eight top level items a file can be truncated inside.
- **8.2.15** 4c.1: reads magic, then minor version, then major version, then the constant pool count, and does not decide anything about a later item before an earlier one has been read.
- **8.2.16** 4c.4: raises `LinkageError` for a loader already recorded as an initiating loader of the name and `ClassCircularityError` for a derivation already in progress, both before any byte of the representation is parsed.
- **8.2.17** 4c.5: completes format checking before verification begins, so a class whose bytes are well formed and whose code does not verify exists as a `Class` object before the `VerifyError` is raised.
