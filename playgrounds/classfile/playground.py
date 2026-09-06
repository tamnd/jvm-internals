# ---
# id: classfile
# kind: playground
# title: The class file playground
# question: Can you write a class file by hand, one field at a time, and have javap and the JVM agree with you?
# part: 2
# pin: jdk-27+35
# status: draft
# blueprints: [BP-CLASSFILE, BP-BYTECODE]
# requires: []
# flags: []
# terms: [constant pool, descriptor, modified UTF-8]
# reviews:
#   beginner: null
#   expert: null
# ---

# %% [markdown] id=badge generated=badge

# %% [markdown] id=hook
# The class file you are about to write is 299 bytes of nothing mysterious. Every one of them is written down in one chapter of one document, in the order they appear, and you can type them.
#
# Almost nobody does. The tools in between are too good: `javac` produces a class file from Java and shows you none of it, and `java.lang.classfile` produces one from a builder and hides the constant pool, the offsets and the lengths, which is what you want when writing a tool and the opposite of what you want now.
#
# So this page has no builder. You write the fields, in the order the specification lists them, and three parties tell you what they made of it: `javap`, the VM in this kernel, and a fresh JVM that runs them.
#
# Nothing here is a quiz. Change a number, run the cell again, and see who complains.

# %% id=bootstrap generated=bootstrap env=E0

# %% [markdown] id=plan
# ## What you are about to write
#
# The file has ten top level fields and then the methods {[JVMS §4.1@SE25]}. In order: the magic number, two version numbers, the constant pool, the access flags, this class, the superclass, the interfaces, the fields, the methods, and the class attributes.
#
# Everything interesting is in the fourth one. The constant pool holds every name, every descriptor and every literal in the file, and the rest of the file is almost entirely indices into it. You cannot write `this_class` until you know which pool entry holds the class name, so the pool is built first even though a reader meets it fourth.
#
# `jvx.cf()` gives you an empty file. Asking it for a pool entry hands back an index and writes nothing yet. Calling `pool()` writes the count and every entry, in one go, at the point in the file where they belong.

# %% id=ask_for_the_pool env=E0
Cf c = jvx.cf();

// Ask for everything the file will need to point at. Each call returns the index that
// entry will have, and identical requests come back with the same index, because a
// constant pool with two copies of "java/lang/Object" in it is a file nobody writes.
int self = c.classEntry("Handwritten");
int parent = c.classEntry("java/lang/Object");
int out = c.fieldref("java/lang/System", "out", "Ljava/io/PrintStream;");
int println = c.methodref("java/io/PrintStream", "println", "(Ljava/lang/String;)V");
int greeting = c.stringEntry("hello from a file I typed");
int mainName = c.utf8("main");
int mainType = c.utf8("([Ljava/lang/String;)V");
int codeName = c.utf8("Code");

System.out.println("this class is #" + self + ", the greeting is #" + greeting);

# %% [markdown] id=pool_notes
# Two things about that pool are worth stopping on before you write it.
#
# The first index is 1 and not 0 {[JVMS §4.1@SE25]}. Nothing lives at index zero, so a zero where an index belongs can mean "there is no entry here" without being ambiguous, which is how a class with no superclass says so: `java/lang/Object` is the one class in the world whose `super_class` is 0.
#
# The second is that a `CONSTANT_Long` or a `CONSTANT_Double` takes two slots {[JVMS §4.4.5@SE25]}. The specification calls this a poor choice in a footnote, and it is still here. Run the next cell and read the two numbers.

# %% id=two_slots env=E0
Cf slots = jvx.cf();
System.out.println("a long is #" + slots.longEntry(42L));
System.out.println("the next entry is #" + slots.utf8("and this is what came after it"));

# %% [markdown] id=header_intro
# ## The first ten bytes
#
# `0xCAFEBABE`, then a minor version, then a major version {[JVMS §4.1@SE25]}. The major version is what decides which rules apply to the rest of the file, and this JDK writes 71.
#
# The access flags are a bitmask, and the two bits here are `ACC_PUBLIC` and `ACC_SUPER` {[JVMS §4.1@SE25]}. `ACC_SUPER` has meant nothing since Java 8 and every class file still sets it, which is a hint about how much of this format is history rather than design.

# %% id=header env=E0
c.u4("magic", ClassFile.MAGIC_NUMBER, "0xcafebabe");
c.u2("minor_version", 0);
c.u2("major_version", ClassFile.latestMajorVersion());
c.pool();
c.u2("access_flags", ClassFile.ACC_PUBLIC | ClassFile.ACC_SUPER, "public super");
c.u2("this_class", self);
c.u2("super_class", parent);
c.u2("interfaces_count", 0);
c.u2("fields_count", 0);
c.u2("methods_count", 1);

# %% [markdown] id=method_intro
# ## One method, and two lengths you cannot write yet
#
# A method is four fields and then its attributes {[JVMS §4.6@SE25]}, and the code lives in an attribute rather than in the method, which is why an abstract method needs no special case: it has no `Code` attribute and the format has nothing else to say about it.
#
# Both `attribute_length` and `code_length` say how many bytes come after them, so neither can be written until those bytes exist. `openU4` writes a zero and remembers where, `close` goes back and fills in the difference, and that is what every class file writer ever written does at this point.
#
# The instructions are named rather than numbered. `c.op("getstatic")` asks `java.lang.classfile.Opcode` on your JDK for the byte, so the number in your file is the number that JDK's own writer would have used.

# %% id=method env=E0
c.u2("  method access_flags", ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, "public static");
c.u2("  method name_index", mainName);
c.u2("  method descriptor_index", mainType);
c.u2("  method attributes_count", 1);

c.u2("    attribute_name_index", codeName);
c.openU4("    attribute_length");
c.u2("    max_stack", 2);
c.u2("    max_locals", 1);
c.openU4("    code_length");

c.op("getstatic").u2("      System.out", out);
c.op("ldc").u1("      the greeting", greeting);
c.op("invokevirtual").u2("      println", println);
c.op("return");

c.close();                                  // code_length
c.u2("    exception_table_length", 0);
c.u2("    attributes_count", 0);
c.close();                                  // attribute_length
c.u2("attributes_count", 0);

byte[] bytes = c.bytes();
System.out.println(bytes.length + " bytes");

# %% [markdown] id=dump_intro
# ## Every byte, and what it was for
#
# This is the whole file. There is nothing else in it, and there is no part of it that this page has not made you write.

# %% id=dump env=E0
c.dump();

# %% [markdown] id=opinions
# ## Three opinions
#
# `javap` first, because it is the friendliest. It is the real tool, not a reimplementation: the bytes are written to a temporary file and handed to the disassembler the JDK ships.

# %% id=disassemble env=E0
jvx.javap(bytes, "-c", "-p");

# %% [markdown] id=load_intro
# Now the VM in this kernel. Defining the class parses the file, and linking it runs the verifier, and the two are different events with different error classes {[JVMS §5.4.1@SE25]}. `jvx.load` does both, so a file that parses and fails to verify fails here rather than looking accepted.

# %% id=load env=E0
Class<?> made = jvx.load("Handwritten", bytes);
System.out.println("the VM accepted it: " + made);
System.out.println("its one method is " + made.getDeclaredMethods()[0]);

# %% [markdown] id=launch_intro
# And a fresh JVM, which is the only one of the three that can tell you the code does what you meant. It runs in its own process, so a file that manages to take a VM down takes that one rather than this notebook.

# %% id=launch env=E0
jvx.launch("Handwritten", bytes);

# %% [markdown] id=break_intro
# ## Now break it
#
# A correct class file teaches you the format. A broken one teaches you the VM, because the question worth asking is which of the many things that could be wrong it notices, when, and in what words.
#
# `c.with("max_stack", 0)` hands back a copy of the file with that one field changed and everything else identical. The method below pushes two values, so a `max_stack` of zero is a lie the file tells about itself.

# %% id=break_stack env=E0
System.out.println(jvx.refusal("Handwritten", c.with("max_stack", 0)));

# %% [markdown] id=break_stack_note
# That one behaved. A `VerifyError`, and a message that names the stack, which is what {[JVMS §4.9.2@SE25]} and {[JVMS §5.4.1@SE25]} together say should happen: a file that breaks a §4.9 constraint is refused by the verifier.
#
# It behaved because your method has no branches. Add one and the method needs a `StackMapTable` {[JVMS §4.10.1@SE25]}, HotSpot then checks the declared stack size while reading that attribute rather than while verifying, and the same mistake comes back as a `ClassFormatError` saying `bad type array size`. Same rule, same file, different error class, decided by whether the method happens to have a branch in it.
#
# Now run the next cell and read the third line.

# %% id=break_more env=E0
System.out.println("bad magic       " + jvx.refusal("Handwritten", c.with("magic", 0xcafed00dL)));
System.out.println("from the future " + jvx.refusal("Handwritten", c.with("major_version", 99)));
System.out.println("short on locals " + jvx.refusal("Handwritten", c.with("max_locals", 0)));
System.out.println("pool too big    " + jvx.refusal("Handwritten", c.with("constant_pool_count", 400)));

# %% [markdown] id=break_more_note
# `max_locals` breaks a constraint from the same §4.9 that `max_stack` did, and it comes back as a `ClassFormatError`. HotSpot checks it in the parser, immediately after reading the two numbers and before it has looked at the code {[HOTSPOT src/hotspot/share/classfile/classFileParser.cpp:2290@jdk-27+35]}, and the parser has one error class to throw. The error follows the place rather than the rule, and nothing in the file told you which of the two you would get.
#
# The last line is worth a second look too. Claiming 400 constants when 21 follow produces `Unknown constant tag 0`, which is the truth about the byte the parser reached and says nothing at all about the count that sent it there.
#
# [docs/probes/classfile-fuzz.md](../../docs/probes/classfile-fuzz.md) does this 27 times and counts how often a message uses any of the words its own rule uses. Twelve times out of twenty seven it uses none of them.

# %% [markdown] id=yours
# ## Your turn
#
# The cell below is empty on purpose. Some things worth building, roughly in order of how much they will teach you:
#
# A method that returns a number, so you meet `ireturn` and a descriptor with a return type in it. A second method, which means a second `Code` attribute and a `methods_count` of 2. A field, which is the shape of a method with no code. A constructor, which is a method whose name is `<init>` and which has to call the superclass constructor before it does anything else. A branch, which is where you find out that the moment your code has two paths through it the verifier wants a `StackMapTable` and will not accept the method without one {[JVMS §4.10.1@SE25]}.
#
# That last one is the wall this playground exists to walk you into, and BP-STACKMAP is the blueprint that gets you over it.

# %% id=scratch env=E0
// Yours. jvx.cf() for an empty file, c.dump() to see it, jvx.javap(bytes) to check it.

# %% [markdown] id=where_this_goes
# ## Where this goes
#
# Every field you wrote is specified in [BP-CLASSFILE](../../docs/blueprints/BP-CLASSFILE.md), whose section 2 is generated from the same JDK you are running rather than typed, and every instruction you used is in [BP-BYTECODE](../../docs/blueprints/BP-BYTECODE.md) section 3, which counts the opcodes three ways and gets three different numbers.
#
# The helper you have been calling is `jvx/12-classfile.jsh`, and its source is in the bootstrap cell at the top of this page. It is about four hundred lines and it contains no table of opcodes, no table of constant pool tags and no magic number, because all of those are read off your JDK through `java.lang.classfile` at the moment you ask for them.
