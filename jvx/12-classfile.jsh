// The Class File Playground. A class file you write one field at a time, and three
// independent opinions on what you wrote.
//
// The thing this exists to make possible is a reader typing the bytes of a class file
// into a cell, running it, and watching `javap` and the VM agree with them. Every other
// way of producing a class file hides the part worth seeing. A compiler hides all of it.
// java.lang.classfile hides the constant pool, the offsets and the lengths, which is
// exactly right for writing a tool and exactly wrong for learning what a class file is.
//
// So Cf writes nothing on its own. Every field in the file is a call the reader makes,
// in the order the specification lists them {[JVMS 4.1@SE25]}, and Cf does three things
// that carry no teaching and a great deal of arithmetic:
//
//   1. It hands out constant pool indices, because an index is a number you cannot know
//      until you have decided what else is in the pool, and getting one wrong produces
//      an error message about something else entirely.
//   2. It fills in the length fields, because a length is the size of what comes after
//      it, which you cannot write until you have written the rest.
//   3. It remembers what every byte was for, so the dump can say so.
//
// Not one number below is typed in. The magic number, the access flag bits, the constant
// pool tags and every opcode are read off the JDK the reader is running, through
// java.lang.classfile, which is the same library javac writes class files with. A table
// of tag numbers copied into this file would be a table that is right today and wrong on
// the JDK that adds a constant kind, and it would be the kind of wrong nobody notices.

class Cf {

    /** One run of bytes, and what it was for. The dump is a list of these. */
    record Span(String label, int at, int length, String note) {}

    private byte[] buf = new byte[512];
    private int len = 0;
    private final List<Span> spans = new ArrayList<>();

    // A length field that has been written but not yet filled in, as the offset of the
    // field, its width in bytes, and which span to relabel once the answer is known.
    private final Deque<int[]> pending = new ArrayDeque<>();

    // The constant pool, collected before any of it is written, because everything else
    // in the file points into it and a reader needs the index before they can point.
    private final List<byte[]> entries = new ArrayList<>();
    private final List<String> entryText = new ArrayList<>();
    private final List<Integer> entryIndex = new ArrayList<>();
    private final Map<String, Integer> alreadyThere = new LinkedHashMap<>();
    private int nextIndex = 1;
    private boolean poolWritten = false;

    // -- the constant pool ------------------------------------------------------------
    //
    // Two things about it surprise everybody once, and both are visible here rather than
    // explained. The first index is 1 and not 0 {[JVMS 4.1@SE25]}, so a valid index is
    // never zero and a zero where an index belongs means "no entry", which is why
    // super_class of java/lang/Object is 0 and nothing else in a class file is. And a
    // long or a double takes two slots, so the entry after one is numbered two higher
    // {[JVMS 4.4.5@SE25]}. Ask for a long and then a string, and look at the two numbers
    // that come back.

    private int add(String key, String text, int slots, byte[] body) {
        Integer found = alreadyThere.get(key);
        if (found != null) return found;
        if (poolWritten) {
            throw new IllegalStateException(
                "the constant pool has already been written into the file, so index "
                + nextIndex + " for " + text + " would land after the entries. Ask for "
                + "every index you need before calling pool()");
        }
        int index = nextIndex;
        entries.add(body);
        entryText.add(text);
        entryIndex.add(index);
        alreadyThere.put(key, index);
        nextIndex += slots;
        return index;
    }

    /**
     * Modified UTF-8, which is not UTF-8 and differs in two places {[JVMS 4.4.7@SE25]}.
     *
     * A zero character is two bytes rather than one, so a name can contain a zero and
     * still be scanned by C code looking for a terminator. And a character outside the
     * basic plane is written as its two surrogates, three bytes each, rather than as one
     * four byte sequence. For ASCII the two encodings are identical, which is why using
     * the wrong one works for a long time and then does not.
     */
    static byte[] modifiedUtf8(String text) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c != 0 && c < 0x80) {
                out.write(c);
            } else if (c < 0x800) {
                out.write(0xc0 | (c >> 6));
                out.write(0x80 | (c & 0x3f));
            } else {
                out.write(0xe0 | (c >> 12));
                out.write(0x80 | ((c >> 6) & 0x3f));
                out.write(0x80 | (c & 0x3f));
            }
        }
        return out.toByteArray();
    }

    private static byte[] entry(int tag, byte[] rest) {
        byte[] all = new byte[rest.length + 1];
        all[0] = (byte) tag;
        System.arraycopy(rest, 0, all, 1, rest.length);
        return all;
    }

    private static byte[] u2bytes(int... values) {
        byte[] out = new byte[values.length * 2];
        for (int i = 0; i < values.length; i++) {
            out[i * 2] = (byte) (values[i] >> 8);
            out[i * 2 + 1] = (byte) values[i];
        }
        return out;
    }

    private static byte[] u4bytes(long value) {
        return new byte[] {
            (byte) (value >> 24), (byte) (value >> 16), (byte) (value >> 8), (byte) value };
    }

    /** A string, which is what a name, a descriptor and a literal are all made of. */
    int utf8(String text) {
        byte[] raw = modifiedUtf8(text);
        byte[] body = new byte[raw.length + 2];
        body[0] = (byte) (raw.length >> 8);
        body[1] = (byte) raw.length;
        System.arraycopy(raw, 0, body, 2, raw.length);
        return add("utf8:" + text, "Utf8 " + quoted(text),
            1, entry(PoolEntry.TAG_UTF8, body));
    }

    /** A class, named the way the class file names one: slashes, and no L or semicolon. */
    int classEntry(String internalName) {
        int name = utf8(internalName);
        return add("class:" + internalName, "Class #" + name + " " + internalName,
            1, entry(PoolEntry.TAG_CLASS, u2bytes(name)));
    }

    /** A string literal, which is a pointer to a Utf8 and not the characters themselves. */
    int stringEntry(String text) {
        int value = utf8(text);
        return add("string:" + text, "String #" + value + " " + quoted(text),
            1, entry(PoolEntry.TAG_STRING, u2bytes(value)));
    }

    /** A name and a descriptor together, which is what a member is identified by. */
    int nameAndType(String name, String descriptor) {
        int n = utf8(name);
        int d = utf8(descriptor);
        return add("nat:" + name + " " + descriptor,
            "NameAndType #" + n + ":#" + d + " " + name + " " + descriptor,
            1, entry(PoolEntry.TAG_NAME_AND_TYPE, u2bytes(n, d)));
    }

    private int member(int tag, String kind, String owner, String name, String descriptor) {
        int c = classEntry(owner);
        int nat = nameAndType(name, descriptor);
        return add(kind + ":" + owner + "." + name + descriptor,
            kind + " #" + c + ".#" + nat + " " + owner + "." + name + ":" + descriptor,
            1, entry(tag, u2bytes(c, nat)));
    }

    int fieldref(String owner, String name, String descriptor) {
        return member(PoolEntry.TAG_FIELDREF, "Fieldref", owner, name, descriptor);
    }

    int methodref(String owner, String name, String descriptor) {
        return member(PoolEntry.TAG_METHODREF, "Methodref", owner, name, descriptor);
    }

    int interfaceMethodref(String owner, String name, String descriptor) {
        return member(PoolEntry.TAG_INTERFACE_METHODREF, "InterfaceMethodref",
            owner, name, descriptor);
    }

    int integerEntry(int value) {
        return add("int:" + value, "Integer " + value,
            1, entry(PoolEntry.TAG_INTEGER, u4bytes(value)));
    }

    /** Two slots, not one, and the index after this one proves it. */
    int longEntry(long value) {
        byte[] body = new byte[8];
        for (int i = 0; i < 8; i++) body[i] = (byte) (value >> (56 - i * 8));
        return add("long:" + value, "Long " + value + " (takes two slots)",
            2, entry(PoolEntry.TAG_LONG, body));
    }

    private static String quoted(String text) {
        return "\"" + text.replace("\n", "\\n") + "\"";
    }

    // -- writing the file --------------------------------------------------------------

    private void ensure(int more) {
        if (len + more <= buf.length) return;
        byte[] bigger = new byte[Math.max(buf.length * 2, len + more)];
        System.arraycopy(buf, 0, bigger, 0, len);
        buf = bigger;
    }

    private Cf put(String label, int width, long value, String note) {
        ensure(width);
        int at = len;
        for (int i = width - 1; i >= 0; i--) buf[len++] = (byte) (value >> (i * 8));
        spans.add(new Span(label, at, width, note));
        return this;
    }

    /** One byte. */
    Cf u1(String label, int value) { return put(label, 1, value, String.valueOf(value)); }

    /** Two bytes, high one first, which is how every multi byte field in a class file is. */
    Cf u2(String label, int value) { return put(label, 2, value, String.valueOf(value)); }

    Cf u2(String label, int value, String note) { return put(label, 2, value, note); }

    Cf u4(String label, long value) { return put(label, 4, value, String.valueOf(value)); }

    Cf u4(String label, long value, String note) { return put(label, 4, value, note); }

    /** Bytes you have already got, appended as they are. */
    Cf raw(String label, byte[] value) {
        ensure(value.length);
        int at = len;
        System.arraycopy(value, 0, buf, len, value.length);
        len += value.length;
        spans.add(new Span(label, at, value.length, value.length + " bytes"));
        return this;
    }

    /**
     * One instruction, named rather than numbered.
     *
     * The number comes from java.lang.classfile.Opcode on the JDK this is running on,
     * so the byte a reader gets is the byte that JDK's own class file writer would emit
     * for that instruction, and nothing in this project chose it.
     */
    Cf op(String mnemonic) {
        int code = opcode(mnemonic);
        return put(mnemonic, 1, code, "opcode 0x" + Integer.toHexString(code));
    }

    /** The opcode byte for an instruction name, read off the JDK. */
    static int opcode(String mnemonic) {
        try {
            return Opcode.valueOf(mnemonic.toUpperCase(Locale.ROOT)).bytecode();
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException(
                "this JDK's java.lang.classfile.Opcode has no instruction called "
                + mnemonic + ". The names are the ones in docs/generated/opcodes.md");
        }
    }

    /** What instruction a byte is, or null when the specification assigns it nothing. */
    static String mnemonic(int code) {
        for (Opcode o : Opcode.values()) {
            if (o.bytecode() == (code & 0xff) && !o.isWide()) {
                return o.name().toLowerCase(Locale.ROOT);
            }
        }
        return null;
    }

    /**
     * The constant pool count and every entry, in one call.
     *
     * The count is one more than the number of slots used, which is the other half of
     * the pool being one based {[JVMS 4.1@SE25]}. A pool holding two entries says three.
     */
    Cf pool() {
        if (poolWritten) throw new IllegalStateException("the pool is already written");
        poolWritten = true;
        put("constant_pool_count", 2, nextIndex,
            nextIndex + ", so " + (nextIndex - 1) + " slots and the first is 1");
        for (int i = 0; i < entries.size(); i++) {
            raw("#" + entryIndex.get(i), entries.get(i));
            spans.set(spans.size() - 1, new Span(
                "#" + entryIndex.get(i), spans.get(spans.size() - 1).at(),
                entries.get(i).length, entryText.get(i)));
        }
        return this;
    }

    /**
     * A four byte length field to be filled in when the thing it measures is finished.
     *
     * `attribute_length` and `code_length` both say how many bytes come after them, and
     * neither can be written before those bytes exist. A compiler solves this by building
     * the body in a buffer first. Here the field is written as zero, the offset is
     * remembered, and `close` goes back and patches it, which is the same trick every
     * class file writer uses and is worth seeing once.
     */
    Cf openU4(String label) {
        put(label, 4, 0, "filled in by close()");
        pending.push(new int[] { len - 4, 4, spans.size() - 1 });
        return this;
    }

    /** The same, two bytes wide. */
    Cf openU2(String label) {
        put(label, 2, 0, "filled in by close()");
        pending.push(new int[] { len - 2, 2, spans.size() - 1 });
        return this;
    }

    /** Fill in the most recently opened length field with everything written since. */
    Cf close() {
        if (pending.isEmpty()) throw new IllegalStateException("nothing is open to close");
        int[] where = pending.pop();
        int at = where[0], width = where[1], span = where[2];
        long value = len - (at + width);
        for (int i = 0; i < width; i++) buf[at + i] = (byte) (value >> ((width - 1 - i) * 8));
        Span old = spans.get(span);
        spans.set(span, new Span(old.label(), old.at(), old.length(),
            value + " bytes follow, counted by close()"));
        return this;
    }

    /** The class file, as bytes. */
    byte[] bytes() {
        if (!pending.isEmpty()) {
            throw new IllegalStateException(
                pending.size() + " length field(s) opened and not closed, so the file has a "
                + "zero where a length belongs. Every openU4 needs a close");
        }
        return Arrays.copyOf(buf, len);
    }

    int size() { return len; }

    List<Span> spans() { return List.copyOf(spans); }

    /**
     * The field with this label, so an experiment can go back and find one.
     *
     * Labels are matched with the indentation taken off. Indenting a label is how the
     * dump shows what is nested inside what, and a reader who typed four spaces to make
     * the dump readable should not have to count them again here.
     */
    Span field(String label) {
        for (Span s : spans) {
            if (s.label().trim().equals(label.trim())) return s;
        }
        throw new IllegalArgumentException("no field called " + label + " was written");
    }

    /**
     * A copy of the file with one field set to something else, which is how you break it.
     *
     * The interesting question about a class file is not whether a correct one works. It
     * is which of the many things that could be wrong the VM notices, at what point, and
     * with what error class, and the only way to ask that is to write a correct file and
     * then damage exactly one field of it.
     */
    byte[] with(String label, long value) {
        Span s = field(label);
        byte[] copy = bytes();
        for (int i = 0; i < s.length(); i++) {
            copy[s.at() + i] = (byte) (value >> ((s.length() - 1 - i) * 8));
        }
        return copy;
    }

    // -- looking at what you wrote -----------------------------------------------------

    // Wide enough for eight bytes and an ellipsis, and for the longest field name in
    // JVMS 4.1 and 4.7.3, which is exception_table_length under four spaces of indent.
    private static final int HEX_WIDTH = 28;
    private static final int LABEL_WIDTH = 30;

    private String hexOf(Span s) {
        StringBuilder out = new StringBuilder();
        int shown = Math.min(s.length(), 8);
        for (int i = 0; i < shown; i++) {
            out.append(String.format("%02x", buf[s.at() + i]));
            if (i + 1 < shown) out.append(' ');
        }
        if (s.length() > shown) out.append(" ...");
        return out.toString();
    }

    /** Every byte of the file, in order, with what each run of them was for. */
    void dump() {
        String title = len + " bytes, " + spans.size() + " fields";
        if (Ui.html(dumpHtml(title))) return;
        System.out.print(dumpText(title));
    }

    String dumpHtml(String title) {
        StringBuilder rows = new StringBuilder();
        for (Span s : spans) {
            rows.append("<div style=\"font-family:").append(Ui.MONO)
                .append(";font-size:12px;line-height:1.6;white-space:pre\">")
                .append("<span style=\"color:").append(Ui.MUTED).append("\">")
                .append(String.format("%04d", s.at())).append("</span>  ")
                .append("<span style=\"color:").append(Ui.BLUE).append("\">")
                .append(Ui.esc(pad(hexOf(s), HEX_WIDTH))).append("</span>")
                .append(Ui.esc(pad(s.label(), LABEL_WIDTH)))
                .append("<span style=\"color:").append(Ui.MUTED).append("\">")
                .append(Ui.esc(s.note() == null ? "" : s.note()))
                .append("</span></div>");
        }
        return Ui.card(Ui.BLUE, "class file", Ui.line(Ui.prose(title)) + rows);
    }

    String dumpText(String title) {
        StringBuilder out = new StringBuilder(title).append("\n");
        for (Span s : spans) {
            out.append(String.format("%04d  %s%s%s%n", s.at(), pad(hexOf(s), HEX_WIDTH),
                pad(s.label(), LABEL_WIDTH), s.note() == null ? "" : s.note()));
        }
        return out.toString();
    }

    private static String pad(String text, int width) {
        StringBuilder out = new StringBuilder(text);
        while (out.length() < width) out.append(' ');
        return out.append(' ').toString();
    }

    // -- the three opinions --------------------------------------------------------------
    //
    // A class file you wrote by hand is a claim, and there are three parties who can
    // disagree with it. javap parses it with the JDK's own reader and prints what it
    // found. The VM parses it again with different code and different rules, and either
    // links it or refuses. And running it is the only one that tells you the code does
    // what you meant. They fail at different points on purpose: a file javap prints
    // happily can still be refused by the VM, and that gap is where the interesting
    // errors live.

    /**
     * The real javap, on your bytes.
     *
     * Not a reimplementation and not a parser of our own. This writes the file out and
     * hands it to the tool the JDK ships, through ToolProvider, so what comes back is
     * what a reader would get from a terminal.
     */
    static String javap(byte[] bytes, String... options) {
        ToolProvider javap = ToolProvider.findFirst("javap").orElseThrow(
            () -> new UnsupportedOperationException(
                "this runtime has no javap, so the jdk.jdeps module is missing"));
        try {
            Path dir = Files.createTempDirectory("jvx-cf");
            Path file = dir.resolve("Handwritten.class");
            Files.write(file, bytes);
            List<String> args = new ArrayList<>(Arrays.asList(options));
            args.add(file.toString());
            StringWriter out = new StringWriter();
            StringWriter err = new StringWriter();
            javap.run(new PrintWriter(out), new PrintWriter(err), args.toArray(new String[0]));
            Files.deleteIfExists(file);
            Files.deleteIfExists(dir);
            // Merged, because javap reports a file it could not read on stderr and a
            // reader who gets an empty pane has been told nothing.
            return out + err.toString();
        } catch (IOException e) {
            throw new RuntimeException("could not hand the bytes to javap", e);
        }
    }

    /**
     * A loader that will define any bytes once and then never be used again.
     *
     * A fresh one per attempt, which is what makes this a playground: the same class name
     * can be defined again after a fix, and a loader that had already refused it would
     * refuse the corrected one for the wrong reason.
     */
    static class Once extends ClassLoader {
        Once() { super(Cf.class.getClassLoader()); }
        Class<?> take(String name, byte[] bytes) {
            return defineClass(name, bytes, 0, bytes.length);
        }
    }

    /**
     * Define the class here, link it, and hand it back.
     *
     * The two happen at different times and the difference is the whole of chapter 5.
     * Defining parses the file and is where a `ClassFormatError` comes from. Linking runs
     * the verifier and is where a `VerifyError` comes from, and it does not happen until
     * something uses the class, which is why this asks for it explicitly rather than
     * letting a reader believe a file was accepted when it was merely stored.
     */
    static Class<?> load(String binaryName, byte[] bytes) {
        Once loader = new Once();
        Class<?> defined = loader.take(binaryName, bytes);
        try {
            Class.forName(binaryName, true, loader);
        } catch (ClassNotFoundException e) {
            throw new IllegalStateException("defined and then not found: " + binaryName, e);
        }
        return defined;
    }

    /**
     * Run the class in a fresh JVM and hand back everything it printed.
     *
     * Two reasons to leave the kernel. A broken class file that takes the VM down rather
     * than throwing would take the reader's whole session with it, which is why
     * probes/classfile-fuzz runs its mutants in a separate process even though none of
     * its 256 bit flips has managed it. And the flags worth trying on a handwritten class
     * file cannot be changed in a VM that is already running.
     */
    static String launch(String binaryName, byte[] bytes, String... vmArgs) {
        try {
            Path dir = Files.createTempDirectory("jvx-cf");
            Path file = dir.resolve(binaryName.replace('.', '/') + ".class");
            Files.createDirectories(file.getParent());
            Files.write(file, bytes);

            List<String> command = new ArrayList<>();
            command.add(Path.of(System.getProperty("java.home"), "bin", "java").toString());
            command.addAll(Arrays.asList(vmArgs));
            command.add("-cp");
            command.add(dir.toString());
            command.add(binaryName);

            Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
            String out = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            p.waitFor();
            return out;
        } catch (Exception e) {
            throw new RuntimeException("could not run " + binaryName + " in a fresh JVM", e);
        }
    }

    /**
     * What went wrong, in one line, for a file that was meant to be refused.
     *
     * The error class matters more than the message here and is printed first. A reader
     * comparing a rejection against what the specification names is comparing class
     * names, and the message is HotSpot's own wording, which the specification does not
     * govern and which probes/classfile-fuzz found uses none of the words its own rule
     * uses in twelve of twenty seven cases.
     */
    static String refusal(String binaryName, byte[] bytes) {
        try {
            load(binaryName, bytes);
            return "no error: this VM accepted it";
        } catch (Throwable t) {
            return t.getClass().getName() + ": " + t.getMessage();
        }
    }
}
