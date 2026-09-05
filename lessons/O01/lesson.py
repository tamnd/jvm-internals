# ---
# id: O01
# title: How big is an object
# question: How big is a bare Object, and why is the answer different from the one you learned?
# part: 0
# pin: jdk-27+35
# status: draft
# blueprints: [BP-HEADER]
# requires: []
# flags: [UseCompactObjectHeaders, ObjectAlignmentInBytes]
# terms: [object header, mark word, compact object headers, identity hash]
# reviews:
#   beginner: null
#   expert: null
# ---

# %% [markdown] id=badge generated=badge

# %% [markdown] id=hook
# Every Java object costs more than the fields you declared. The extra is the header, and for about twenty years the number everybody learned was 16 bytes for an empty object: 8 for the mark word, 4 for the class pointer, 4 for alignment.
#
# That number is wrong on the JVM you are about to run. It changed in JDK 27, by default, for everybody, and it changed by enough that a heap full of small objects can shrink by a fifth.
#
# The interesting part is not the new number. It is that knowing the new number still will not let you predict the size of an `Integer`, and there is a good reason for that which almost nobody thinks about until they measure it.
#
# You are going to measure it. Three questions, and you have to commit to an answer before each one.

# %% id=bootstrap generated=bootstrap env=E0

# %% [markdown] id=gate_1_intro
# ## The first question
#
# Answer before you run anything. Wrong is fine and wrong is useful, but skipping is neither.

# %% id=gate_1 tags=[predict] env=E0
jvx.gate("gate_1",
    "How many bytes does a bare `new Object()` occupy on JDK 27?",
    "a) 8",
    "b) 12",
    "c) 16",
    "d) it depends on the platform");

# %% id=answer_1 env=E0
// The letter here is a guess, and probably not yours. Change it, then run this cell.
jvx.answer("gate_1", "c");

# %% [markdown] id=measure_1_intro
# ### Measuring it, with nothing installed
#
# There is a library for this called JOL, and reaching for it here would be a mistake, because the whole answer is available without it and the shape of the answer is the lesson.
#
# The header is the thing your first field comes after. So the offset of the first field is where the header stops, which means it is the header size. That is the whole trick, and `jvx.headerSize` is four lines doing exactly it. The source is in the bootstrap cell above if you want to read it.

# %% id=measure_1 env=E0
// An object with no fields has no first field to point at, so measure classes that
// have exactly one and see where it starts.
class OneInt { int a; }
class OneRef { Object r; }

System.out.println("OneInt header stops at byte " + jvx.headerSize(OneInt.class));
System.out.println("OneRef header stops at byte " + jvx.headerSize(OneRef.class));
System.out.println("int[] elements start at byte " + jvx.arrayBase(int[].class));
System.out.println("long[] elements start at byte " + jvx.arrayBase(long[].class));

# %% id=reveal_1 env=E0
jvx.reveal("gate_1", "a");

# %% [markdown] id=explain_1
# The header stops at byte 8. An object with no fields is 8 bytes, and that is already 8 byte aligned, so there is nothing to pad.
#
# If you said 16, you were right until JDK 27 and you are in very good company, because that is what most books, most blog posts and most interview answers still say. If you said 12 you were remembering the header without the padding. If you said it depends on the platform, that is the most defensible wrong answer of the four, and it is worth two sentences.
#
# It does depend on things, but not on the platform. Every number in this lesson was measured on both an arm64 laptop and an x86_64 Linux server, and every offset was identical. What it depends on is a flag, which you will turn off in a minute.

# %% [markdown] id=tour
# ## What is actually in there
#
# An object on the heap starts with a header, and the header exists because the runtime needs to know things about an object that the object's own fields cannot tell it. What class is this. Is anything holding a lock on it. Has anyone asked for its identity hash. How many collections has it survived.
#
# The first 8 bytes are the mark word. It is not a number that means one thing, it is several unrelated fields packed into one word, and which fields are present depends on what has happened to the object. The lock state lives in the bottom two bits {[HOTSPOT src/hotspot/share/oops/markWord.hpp:124@jdk-27+35]}. The collector's age counter is four bits above that {[HOTSPOT src/hotspot/share/oops/markWord.hpp:126@jdk-27+35]}. The identity hash, if anything has ever asked for one, is 31 bits in the middle {[HOTSPOT src/hotspot/share/oops/markWord.hpp:128@jdk-27+35]}.
#
# Then there is the class pointer, and this is the part that changed. Until JDK 27, it was a separate 4 byte field that came after the mark word, so the header was 8 plus 4, and since almost every object then needs 4 bytes of padding to reach an 8 byte boundary, an empty object cost 16. From JDK 27, `UseCompactObjectHeaders` is on by default and the class pointer moved into the top 22 bits of the mark word itself {[HOTSPOT src/hotspot/share/oops/markWord.hpp:150@jdk-27+35]}. No separate field, no padding, header of 8.
#
# Twenty two bits is the interesting constraint. It means a JVM can address about four million distinct classes, which is far more than any real program loads, and it is only possible because class metadata is allocated in one contiguous region so a class can be named by its offset into that region rather than by a full pointer.
#
# ### None of this is in the specification
#
# It is worth being precise about what kind of fact you are learning here, because this is the single most common way people get burned by internals knowledge.
#
# The Java Virtual Machine Specification says, in as many words, that "the Java Virtual Machine does not mandate any particular internal structure for objects" {[JVMS §2.7@SE25]}. Not the header size, not the mark word, not the class pointer, not the bit positions. All of it is HotSpot's choice, and OpenJ9 makes different ones.
#
# So every claim in this lesson except that one sentence is a `[HOTSPOT]` claim, and every one of them cites a line of HotSpot source at the pinned tag. That is not bureaucracy. It is the difference between "objects are 8 bytes now" and "objects are 8 bytes on this implementation at this version with this flag", and only the second one is true.
#
# ### Why the flag exists at all
#
# If compact headers are strictly better, why is there a switch? Because the change is not free. Twenty two bits caps the class space, and the identity hash lost a bit. More practically, code that reads object headers directly, and there is more of it in the wild than you would like, breaks. The flag is how you find out whether that is you.
#
# You are going to use the flag as a measuring instrument rather than as a setting. Running the same program both ways, in two fresh JVMs, is the only way to see the difference, because a flag that decides object layout cannot be changed in a VM whose objects are already laid out.

# %% [markdown] id=picture
# ## The picture
#
# ![The object header in bytes and in bits](https://raw.githubusercontent.com/tamnd/jvm-internals/main/docs/generated/markword.svg)
#
# Nothing in that drawing was drawn by hand. The bit positions come from `markWord.hpp` at the pinned tag, and the picture is generated from them, so a field that moves in a future release moves in the picture rather than quietly making it wrong.

# %% id=header_live env=E0
// The same bits, on an object you made a moment ago. Use jvx.freshMark rather than
// allocating into a variable, because assigning an object to a top level variable in
// JShell is enough to make something ask for its identity hash, and then the "before"
// you wanted to look at is gone.
System.out.print(MarkWord.decode(jvx.freshMark()));

# %% [markdown] id=header_note
# Look at the age field. It may read 0 and it may read 1, and both are right.
#
# Which one you get depends on whether a young collection happened between the object being allocated and the word being read, which is not something the page can control. On the machine this was written on, the first run of that cell in a fresh kernel reports age 1 and every run after it reports 0, because that allocation is the one that fills eden, and the object survives the collection it triggered and comes back with its age counter incremented. Run the cell a second time and watch it drop to 0.
#
# That is the age counter doing its job, and it is worth seeing this early: a header field is live state that the runtime writes as things happen to the object, not a constant stamped in at birth. You will see the same thing again with the identity hash further down, where it is easier to trigger on purpose.

# %% [markdown] id=gate_2_intro
# ## The second question
#
# Now a class with two fields in it.
#
# ```java
# class Two { int a; int b; }
# ```

# %% id=gate_2 tags=[predict] env=E0
jvx.gate("gate_2",
    "How many bytes is one `Two`, with compact headers on and with them off?",
    "a) 16 compact, 16 legacy",
    "b) 16 compact, 20 legacy",
    "c) 16 compact, 24 legacy",
    "d) 12 compact, 16 legacy");

# %% id=answer_2 env=E0
// Same as before, the letter is a guess. Change it to yours.
jvx.answer("gate_2", "b");

# %% [markdown] id=measure_2_intro
# ### Running the same program in two different JVMs
#
# `UseCompactObjectHeaders` decides where fields go, so it can only be chosen when the VM starts. To compare, you need two VMs. `jvx.run` writes a single Java file, hands it to `java`, and gives you back what it printed. There is no compile step and no classpath, because a lone `.java` file passed to `java` is compiled in memory and run.

# %% id=measure_2 env=E0
String probe = """
    import jdk.internal.misc.Unsafe;
    public class Size {
        static class Two { int a; int b; }
        public static void main(String[] a) {
            Unsafe u = Unsafe.getUnsafe();
            long first = u.objectFieldOffset(Two.class, "a");
            long last  = u.objectFieldOffset(Two.class, "b");
            long used  = last + 4;
            long size  = (used + 7) / 8 * 8;
            System.out.printf("header stops at %d, fields end at %d, object is %d bytes%n",
                first, used, size);
        }
    }
    """;

String open = "--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED";
System.out.print("compact  " + jvx.run("Size", probe, open));
System.out.print("legacy   " + jvx.run("Size", probe, open, "-XX:-UseCompactObjectHeaders"));

# %% id=reveal_2 env=E0
jvx.reveal("gate_2", "c");

# %% [markdown] id=explain_2
# Compact is 8 for the header plus 4 plus 4, which is 16 exactly and needs no padding. Legacy is 12 for the header plus 4 plus 4, which is 20, and 20 is not a multiple of 8, so it rounds up to 24.
#
# If you said 20 you did the header arithmetic correctly and forgot the alignment, which is the most common way to be wrong here. Objects are allocated on 8 byte boundaries, controlled by `ObjectAlignmentInBytes`, so no object is ever an odd size.
#
# So this one really did save 8 bytes per instance, not 4. That is the version of the story everybody repeats. Now the third question.

# %% [markdown] id=gate_3_intro
# ## The third question
#
# `java.lang.Integer` is a class with one `int` field in it. Nothing else.

# %% id=gate_3 tags=[predict] env=E0
jvx.gate("gate_3",
    "How many bytes is one `java.lang.Integer`, compact and legacy?",
    "a) 12 compact, 16 legacy",
    "b) 16 compact, 16 legacy",
    "c) 16 compact, 20 legacy",
    "d) 16 compact, 24 legacy");

# %% id=answer_3 env=E0
// Change the letter to yours before you run it.
jvx.answer("gate_3", "a");

# %% id=measure_3 env=E0
String probe3 = """
    import jdk.internal.misc.Unsafe;
    public class Boxed {
        public static void main(String[] a) throws Exception {
            Unsafe u = Unsafe.getUnsafe();
            long off = u.objectFieldOffset(Integer.class, "value");
            long used = off + 4;
            System.out.printf("header stops at %d, fields end at %d, object is %d bytes%n",
                off, used, (used + 7) / 8 * 8);
        }
    }
    """;

System.out.print("compact  " + jvx.run("Boxed", probe3, open));
System.out.print("legacy   " + jvx.run("Boxed", probe3, open, "-XX:-UseCompactObjectHeaders"));

# %% id=reveal_3 env=E0
jvx.reveal("gate_3", "b");

# %% [markdown] id=explain_3
# 16 bytes either way. The header shrank by 4 bytes and the object did not shrink at all.
#
# Compact: 8 for the header plus 4 for the `int` is 12, which rounds up to 16. Legacy: 12 for the header plus 4 for the `int` is 16 exactly. The four bytes the header gave back went straight into padding, and the reader who was told "compact headers save four bytes per object" got nothing here.
#
# This is the point of the lesson. That sentence is true about the header and it is not reliably true about the object, because every object is rounded up to a multiple of 8 anyway. Whether you actually keep the saving depends on what your fields add up to. `Two` kept it, and kept twice as much as advertised. `Integer` kept none of it.
#
# If your heap is mostly boxed numbers, compact headers may do nothing measurable for you. If it is mostly small objects with two or three fields, it can be a fifth of your heap. Neither of those is a guess you can make from the flag's description, and both of them are five minutes of measuring.
#
# One more thing worth noticing. `Long` behaves differently again: its field is 8 bytes wide and has to be 8 byte aligned, so legacy puts it at offset 16 rather than 12, wasting four bytes to alignment inside the object. Compact puts it at 8. You can check that with the same probe.

# %% [markdown] id=klass_intro
# ## Proving the class pointer is really in there
#
# The claim that the top 22 bits hold the class pointer is easy to state and easy to take on faith. It is also easy to check: two objects of different classes should differ in those bits, and two objects of the same class should not.

# %% id=klass_proof env=E0
class Two { int a; int b; }

long objectKlass = MarkWord.get(jvx.freshMark(), "klass");
long twoKlassA = jvx.field(new Two(), "klass");
long twoKlassB = jvx.field(new Two(), "klass");

// Formatted, then printed. printf in a notebook cell arrives one fragment at a time and
// the front end puts each fragment on its own line, which is a thing worth knowing once.
System.out.println(String.format("Object     klass bits 0x%x", objectKlass));
System.out.println(String.format("Two        klass bits 0x%x", twoKlassA));
System.out.println(String.format("Two again  klass bits 0x%x", twoKlassB));
System.out.println();
System.out.println("two Twos agree:            " + (twoKlassA == twoKlassB));
System.out.println("Two differs from Object:   " + (twoKlassA != objectKlass));

# %% [markdown] id=klass_note
# Both true, so the field is doing what the header file says it does.
#
# Do not write the actual number down anywhere. It is an offset into the region where class metadata lives, so it depends on what your program loaded and in what order. Measured on two machines, `Object` came out as `0x0017ac` on one and `0x001774` on the other. What is stable is the relationship, not the value.

# %% [markdown] id=hash_intro
# ## Watching a field get written
#
# The identity hash is the clearest thing in the header to watch, because it starts empty and it is filled in lazily, the first time anybody asks. Nothing writes it at allocation.

# %% id=hash_live env=E0
String probe4 = """
    import jdk.internal.misc.Unsafe;
    public class Hash {
        public static void main(String[] a) {
            Unsafe u = Unsafe.getUnsafe();
            Object o = new Object();
            System.out.printf("before  0x%016x%n", u.getLong(o, 0L));
            int h = System.identityHashCode(o);
            System.out.printf("after   0x%016x%n", u.getLong(o, 0L));
            System.out.printf("hash    0x%x, and bits 41..11 of the word say 0x%x%n",
                h, (u.getLong(o, 0L) >>> 11) & ((1L << 31) - 1));
        }
    }
    """;
jvx.show("Hash", probe4, open);

# %% [markdown] id=hash_note
# The word changes exactly once, in the middle, and the bits that changed decode to the number `identityHashCode` returned.
#
# This runs in a separate JVM rather than here, for a reason worth knowing. JShell assigns your top level variables into its own machinery, and that is enough to make something ask the object for its identity hash. So an object you create in a cell and look at in the next cell already has a hash by the time you look. The "before" would be gone and you would never know it had been there. Anything that needs a genuinely untouched object goes through `jvx.run` or through `jvx.freshMark`.

# %% id=provenance env=E0
// Every bit position used above, and the line of HotSpot it came from.
jvx.provenance();

# %% [markdown] id=boss_intro
# ## The boss fight
#
# Write a class called `Candidate` whose instances are exactly 24 bytes with compact headers on and exactly 32 bytes with them off, using only `int`, `long` and reference fields, and no more than four of them.
#
# The two sizes have to differ by 8 rather than by 4, and the header change on its own only gives you 4. So you need the alignment padding to work against you in one configuration and not the other, which means thinking about where an 8 byte field is allowed to sit.
#
# Measure, do not reason. Edit the cell until both numbers are right, then save your class to `lessons/O01/answer.java` and run `python lessons/O01/grade.py lessons/O01/answer.java`.

# %% id=boss env=E0
String mine = """
    class Candidate {
        // your fields here
    }
    """;

// The same measurement the grader does, so you can iterate before you submit.
System.out.print("compact  " + jvx.run("Answer", jvx.sizeProbe(mine), open));
System.out.print("legacy   " + jvx.run("Answer", jvx.sizeProbe(mine), open, "-XX:-UseCompactObjectHeaders"));

# %% [markdown] id=blueprint
# ## What this contributes to BP-HEADER
#
# Three clauses, each of which is now measured rather than asserted.
#
# **H1.** With `UseCompactObjectHeaders` on, which is the default from JDK 27, the object header is 8 bytes and contains the compressed class pointer in bits 63 to 42 of the mark word {[HOTSPOT src/hotspot/share/oops/markWord.hpp:150@jdk-27+35]}. With it off, the header is 12 bytes: an 8 byte mark word followed by a separate 4 byte class pointer.
#
# **H2.** Instance size is the first field offset plus the space the fields occupy, rounded up to `ObjectAlignmentInBytes`, which defaults to 8. The rounding is why a 4 byte reduction in header size does not imply a 4 byte reduction in object size, and for `java.lang.Integer` it implies none at all.
#
# **H3.** None of the above is specified. The JVMS does not mandate any internal structure for objects {[JVMS §2.7@SE25]}, so every clause here is a statement about HotSpot at `jdk-27+35` and about nothing else.

# %% [markdown] id=what_you_now_know
# ## What you now know
#
# - You can measure the header size of any class on any JVM with one call and nothing installed, because the first field offset is where the header ends.
# - You can predict an object's size from its fields, and you know to round up to 8 before you believe the answer.
# - You can tell someone why compact object headers saved 8 bytes on one of your classes and 0 on another, without hand waving about it depending.
# - You can read a mark word, name each field, and cite the line of HotSpot that puts it there.
# - You know that all of this is HotSpot's choice rather than the specification's, and you know the sentence in the JVMS that says so.
# - You know that JShell touches the objects you assign to variables, and which of your measurements that would quietly ruin.
