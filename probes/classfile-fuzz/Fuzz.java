import static java.lang.constant.ConstantDescs.CD_String;
import static java.lang.constant.ConstantDescs.CD_int;
import static java.lang.constant.ConstantDescs.CD_void;
import static java.lang.constant.ConstantDescs.INIT_NAME;
import static java.lang.constant.ConstantDescs.MTD_void;

import java.lang.classfile.ClassFile;
import java.lang.classfile.Label;
import java.lang.constant.ClassDesc;
import java.lang.constant.MethodTypeDesc;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.TreeMap;

/**
 * Break one class file in every way the specification has a rule about, and then break it
 * at random, and write down what the JVM says each time.
 *
 * M2's last gate asks for a case where HotSpot's rejection differs from what the
 * specification names. That is a comparison between two texts, so this half only
 * measures: it builds one valid class file, patches a named byte of it, and records the
 * stage it died at and the exact words. The claim about what JVMS says for each case
 * lives in `tools/gen_fuzz.py`, next to the section it cites, and the comparison happens
 * there where a reader can see both sides of it at once.
 *
 * Nothing here is random unless it says so. The targeted patches are addressed through a
 * walk of the seed file rather than by counting bytes, because every offset in a class
 * file moves when anything in front of it changes, and a probe whose offsets are literals
 * is a probe that silently patches the wrong thing after an unrelated edit. The random
 * half is seeded, and the seed is in the output, so the same 256 mutants come out of the
 * same seed on every machine.
 *
 * The random half loads and links and never runs anything. A flipped branch offset that
 * survives verification is a loop nobody wrote, and a probe that hangs is worse than a
 * probe that measures less.
 *
 * Output is one `key\tvalue` line per fact, sorted, the shape the other probes here use.
 */
public class Fuzz {

    /** The same mutants on every machine, so two platforms can be compared at all. */
    static final long SEED = 20260906L;

    static final int RANDOM_MUTANTS = 256;

    static final String SEED_CLASS = "Target";

    static final TreeMap<String, String> FACTS = new TreeMap<>();

    /** Where a class file stops being acceptable, in the order the JVM finds out. */
    enum Stage {
        parse,     // defineClass: structure, flags, versions, indices into the pool
        link,      // first active use: the verifier
        run,       // the instruction executes: resolution
        accepted   // nobody objected
    }

    public static void main(String[] args) throws Exception {
        byte[] seed = seedClass();
        Layout layout = Layout.of(seed);

        put("probe.seed", String.valueOf(SEED));
        put("probe.class_file_major", String.valueOf(ClassFile.latestMajorVersion()));
        put("probe.seed_class", SEED_CLASS);
        put("probe.seed_bytes", String.valueOf(seed.length));
        put("probe.random_mutants", String.valueOf(RANDOM_MUTANTS));
        for (Region region : layout.regions()) {
            put("region." + region.name() + ".at", String.valueOf(region.at()));
            put("region." + region.name() + ".bytes", String.valueOf(region.length()));
        }

        // The seed itself has to load, link and run, or every result below is a
        // measurement of a broken starting point rather than of the patch.
        Outcome fine = load(SEED_CLASS, seed, true);
        put("seed.stage", fine.stage().name());
        put("seed.answer", fine.detail());

        boolean targeted = args.length == 0 || args[0].equals("--targeted");
        if (targeted) {
            for (Case one : cases(layout)) {
                run(one, seed, layout);
            }
        } else {
            int from = Integer.parseInt(args[1]);
            int count = Integer.parseInt(args[2]);
            for (int i = from; i < from + count && i < RANDOM_MUTANTS; i++) {
                flip(i, seed, layout);
            }
        }

        for (var entry : FACTS.entrySet()) {
            System.out.println(entry.getKey() + "\t" + entry.getValue());
        }
    }

    // The seed.

    /**
     * One valid class file with something of every kind in it worth breaking.
     *
     * A static field and an instance field, a constructor, a method with a branch in it so
     * that the file carries a StackMapTable, and a `go` that returns a number the caller
     * can check. Small enough that a random byte lands somewhere interesting, complete
     * enough that the parser has to walk every structure to read it.
     */
    static byte[] seedClass() {
        ClassDesc self = ClassDesc.of(SEED_CLASS);
        return ClassFile.of().build(self, builder -> {
            builder.withFlags(ClassFile.ACC_PUBLIC | ClassFile.ACC_SUPER);
            builder.withField("COUNT", CD_int,
                    ClassFile.ACC_PRIVATE | ClassFile.ACC_STATIC);
            builder.withField("name", CD_String, ClassFile.ACC_PRIVATE);
            builder.withMethodBody(INIT_NAME, MTD_void, ClassFile.ACC_PUBLIC, code -> {
                code.aload(0);
                code.invokespecial(java.lang.constant.ConstantDescs.CD_Object,
                        INIT_NAME, MTD_void);
                code.aload(0);
                code.ldc("target");
                code.putfield(self, "name", CD_String);
                code.return_();
            });
            builder.withMethodBody("go", MethodTypeDesc.of(CD_int),
                    ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                        // The branch is the point. Two paths into one label is what makes
                        // the builder write a StackMapTable, and the stack map is one of
                        // the structures worth breaking.
                        Label small = code.newLabel();
                        Label done = code.newLabel();
                        code.getstatic(self, "COUNT", CD_int);
                        code.bipush(10);
                        code.if_icmplt(small);
                        code.bipush(20);
                        code.goto_(done);
                        code.labelBinding(small);
                        code.bipush(7);
                        code.labelBinding(done);
                        code.ireturn();
                    });
            builder.withMethodBody("main",
                    MethodTypeDesc.of(CD_void, CD_String.arrayType()),
                    ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                        code.invokestatic(self, "go", MethodTypeDesc.of(CD_int));
                        code.pop();
                        code.return_();
                    });
        });
    }

    // The targeted patches.

    /** One patch, addressed through the layout, with a sentence saying what it breaks. */
    record Case(String name, String what, int at, int length, long value) {}

    static Case at(String name, String what, int at, int length, long value) {
        return new Case(name, what, at, length, value);
    }

    static List<Case> cases(Layout layout) {
        List<Case> found = new ArrayList<>();
        int major = ClassFile.latestMajorVersion();

        found.add(at("magic_is_not_cafebabe",
                "the first four bytes, which every reader checks first", 0, 4, 0xCAFED00DL));
        found.add(at("major_version_from_the_future",
                "a major version one above the highest this JDK reads", 6, 2, major + 1));
        found.add(at("major_version_before_there_were_any",
                "a major version below 45, which no JDK ever wrote", 6, 2, 44));
        found.add(at("constant_pool_count_is_zero",
                "a constant pool count of zero, which is one less than the smallest legal "
                        + "value", 8, 2, 0));
        found.add(at("constant_pool_count_past_the_end",
                "a constant pool count larger than the entries that follow it", 8, 2,
                layout.poolCount() + 40));

        int utf8 = layout.firstOfTag(1);
        found.add(at("utf8_holds_a_zero_byte",
                "a zero byte inside a Utf8 entry, which the modified encoding forbids so "
                        + "that a string can hold no terminator", layout.entryAt[utf8] + 3, 1,
                0));
        found.add(at("constant_pool_tag_is_not_a_tag",
                "a constant pool tag of 2, a number the table has never used",
                layout.entryAt[utf8], 1, 2));

        int classEntry = layout.firstOfTag(7);
        found.add(at("class_entry_names_an_index_past_the_end",
                "a CONSTANT_Class whose name points past the last pool entry",
                layout.entryAt[classEntry] + 1, 2, layout.poolCount() + 40));
        found.add(at("class_entry_names_the_wrong_kind_of_entry",
                "a CONSTANT_Class whose name points at another CONSTANT_Class rather than "
                        + "at a Utf8", layout.entryAt[classEntry] + 1, 2, classEntry));

        found.add(at("this_class_is_not_a_class_entry",
                "this_class pointing at a Utf8 rather than at a CONSTANT_Class",
                layout.thisClassAt, 2, utf8));
        found.add(at("super_class_is_zero",
                "a super_class of zero, which only java/lang/Object may have",
                layout.superClassAt, 2, 0));
        found.add(at("the_class_is_final_and_abstract",
                "ACC_FINAL and ACC_ABSTRACT together, which no class can satisfy",
                layout.accessFlagsAt, 2,
                ClassFile.ACC_PUBLIC | ClassFile.ACC_FINAL | ClassFile.ACC_ABSTRACT));
        found.add(at("the_class_is_an_interface_that_is_not_abstract",
                "ACC_INTERFACE without ACC_ABSTRACT", layout.accessFlagsAt, 2,
                ClassFile.ACC_PUBLIC | ClassFile.ACC_INTERFACE));

        Member field = layout.fields.get(1);
        found.add(at("the_field_is_final_and_volatile",
                "ACC_FINAL and ACC_VOLATILE on the same field", field.at(), 2,
                ClassFile.ACC_PRIVATE | ClassFile.ACC_FINAL | ClassFile.ACC_VOLATILE));

        Member go = layout.method("go");
        found.add(at("the_method_is_abstract_and_final",
                "ACC_ABSTRACT and ACC_FINAL on the same method, one of which says there is "
                        + "no body and the other that it cannot be replaced", go.at(), 2,
                ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC | ClassFile.ACC_ABSTRACT
                        | ClassFile.ACC_FINAL));

        Attr code = layout.code(go);
        found.add(at("the_attribute_is_longer_than_what_follows_it",
                "a Code attribute whose declared length runs past the end of the file",
                code.lengthAt(), 4, code.length() + 200L));
        found.add(at("code_length_is_zero",
                "a method body of no bytes at all", code.dataAt() + 4, 4, 0));
        found.add(at("code_length_is_past_the_attribute",
                "a code array longer than the attribute that contains it",
                code.dataAt() + 4, 4, code.length() + 100L));
        found.add(at("max_stack_is_too_small",
                "a max_stack of zero for a body that pushes two values",
                code.dataAt(), 2, 0));
        found.add(at("max_locals_is_too_small",
                "a max_locals smaller than the constructor's own `this`",
                layout.code(layout.method("<init>")).dataAt() + 2, 2, 0));

        int codeAt = code.dataAt() + 8;
        found.add(at("the_opcode_is_not_an_opcode",
                "0xdd where an opcode belongs, which the table leaves unassigned", codeAt,
                1, 0xDD));
        found.add(at("the_jump_lands_outside_the_method",
                "a branch offset that points thousands of bytes past the end of the body",
                jumpAt(layout, code), 2, 20000));

        Attr maps = layout.stackMap(code);
        if (maps != null) {
            found.add(at("the_stack_map_frame_type_is_reserved",
                    "a stack map frame tagged 130, which the table reserves and no writer "
                            + "may use", maps.dataAt() + 2, 1, 130));
            // An attribute whose name is not one the JVM knows must be ignored. Renaming
            // the stack maps is the cheapest way to find out what ignoring them costs.
            int named = u2(layout.bytes, maps.at());
            found.add(at("the_stack_map_table_is_renamed_and_so_ignored",
                    "the last letter of the name `StackMapTable`, which makes it an "
                            + "attribute no reader recognises",
                    layout.entryAt[named] + 2 + "StackMapTable".length(), 1, 'X'));
        }

        found.add(at("this_class_names_a_different_class",
                "one letter of the name in the file, so that the class inside it is not "
                        + "the class that was asked for",
                layout.entryAt[layout.utf8Index(SEED_CLASS)] + 3 + SEED_CLASS.length() - 1,
                1, 'x'));

        found.add(at("the_file_is_truncated", "the last four bytes removed", -1, 0, 0));
        found.add(at("the_file_has_four_bytes_too_many",
                "four bytes appended after the last attribute", -2, 0, 0));
        return found;
    }

    /** The offset of the branch operand in `go`, found by opcode rather than by counting. */
    static int jumpAt(Layout layout, Attr code) {
        int start = code.dataAt() + 8;
        int length = i4(layout.bytes, code.dataAt() + 4);
        for (int at = start; at < start + length; at++) {
            if ((layout.bytes[at] & 0xFF) == 0xA1) {  // if_icmplt
                return at + 1;
            }
        }
        throw new IllegalStateException("no if_icmplt in the body of go");
    }

    static void run(Case one, byte[] seed, Layout layout) {
        byte[] broken;
        if (one.at() == -1) {
            broken = new byte[seed.length - 4];
            System.arraycopy(seed, 0, broken, 0, broken.length);
        } else if (one.at() == -2) {
            broken = new byte[seed.length + 4];
            System.arraycopy(seed, 0, broken, 0, seed.length);
        } else {
            broken = seed.clone();
            write(broken, one.at(), one.length(), one.value());
        }
        record(one.name(), "what", one.what());
        record(one.name(), "at", one.at() < 0 ? "the end of the file"
                : one.at() + " in " + layout.regionOf(one.at()));
        record(one.name(), "bytes", String.valueOf(broken.length));
        Outcome outcome = load(SEED_CLASS, broken, true);
        record(one.name(), "stage", outcome.stage().name());
        record(one.name(), "error", outcome.error());
        record(one.name(), "message", outcome.detail());
    }

    // The random half.

    static void flip(int index, byte[] seed, Layout layout) {
        Random random = new Random(SEED * 1000003L + index);
        int at = random.nextInt(seed.length);
        int bit = random.nextInt(8);
        byte[] broken = seed.clone();
        broken[at] = (byte) (broken[at] ^ (1 << bit));
        String key = String.format("%03d", index);
        Outcome outcome = load(SEED_CLASS, broken, false);
        put("random." + key + ".at", String.valueOf(at));
        put("random." + key + ".bit", String.valueOf(bit));
        put("random." + key + ".region", layout.regionOf(at));
        put("random." + key + ".stage", outcome.stage().name());
        put("random." + key + ".error", outcome.error());
        put("random." + key + ".message", outcome.detail());
    }

    // Loading.

    record Outcome(Stage stage, String error, String detail) {}

    /**
     * Take the bytes as far into a running program as they are asked to go.
     *
     * Three separate attempts rather than one try block, because the whole result is which
     * of the three failed, and a fresh loader each time so that no name is already
     * resolved from an earlier mutant.
     */
    static Outcome load(String className, byte[] bytes, boolean invoke) {
        Loader loader = new Loader();
        Class<?> defined;
        try {
            defined = loader.define(className, bytes);
        } catch (Throwable t) {
            return new Outcome(Stage.parse, kind(t), message(t));
        }
        try {
            Class.forName(className, true, loader);
        } catch (Throwable t) {
            return new Outcome(Stage.link, kind(t), message(t));
        }
        if (!invoke) {
            return new Outcome(Stage.accepted, "", "loaded and linked, not run");
        }
        Method go = null;
        for (Method method : defined.getDeclaredMethods()) {
            if (method.getName().equals("go") && method.getParameterCount() == 0) {
                go = method;
            }
        }
        if (go == null) {
            return new Outcome(Stage.accepted, "", "loaded and linked, no go to call");
        }
        try {
            go.setAccessible(true);
            return new Outcome(Stage.accepted, "", "go returned " + go.invoke(null));
        } catch (Throwable t) {
            return new Outcome(Stage.run, kind(t), message(t));
        }
    }

    static final class Loader extends ClassLoader {
        Loader() {
            super(Fuzz.class.getClassLoader());
        }

        Class<?> define(String name, byte[] bytes) {
            return defineClass(name, bytes, 0, bytes.length);
        }
    }

    static Throwable real(Throwable t) {
        return t instanceof InvocationTargetException && t.getCause() != null
                ? t.getCause() : t;
    }

    static String kind(Throwable t) {
        return real(t).getClass().getName();
    }

    static String message(Throwable t) {
        String message = real(t).getMessage();
        return message == null ? "" : message.replace('\n', ' ').replace('\r', ' ').trim();
    }

    // The walk.

    record Attr(String name, int at, int lengthAt, int length, int dataAt) {}

    record Member(int at, int nameIndex, int descriptorIndex, List<Attr> attributes,
            int end) {}

    record Region(String name, int at, int length) {}

    /**
     * Every offset this probe patches, worked out by walking the file.
     *
     * There is no way to skip the constant pool without knowing the size of every tag, and
     * no way to reach a method without walking every field, which is the same reason the
     * JVM's own parser is one long forward pass. Long and Double take two slots, which is
     * the famous mistake and the reason the loop counts the way it does.
     */
    static final class Layout {
        final byte[] bytes;
        int[] entryAt;
        int[] entryTag;
        int poolEnd;
        int accessFlagsAt;
        int thisClassAt;
        int superClassAt;
        int interfacesAt;
        int fieldsAt;
        int methodsAt;
        int attributesAt;
        List<Member> fields = new ArrayList<>();
        List<Member> methods = new ArrayList<>();

        Layout(byte[] bytes) {
            this.bytes = bytes;
        }

        static Layout of(byte[] bytes) {
            Layout layout = new Layout(bytes);
            int count = u2(bytes, 8);
            layout.entryAt = new int[count];
            layout.entryTag = new int[count];
            int at = 10;
            int index = 1;
            while (index < count) {
                int tag = bytes[at] & 0xFF;
                layout.entryAt[index] = at;
                layout.entryTag[index] = tag;
                int size = switch (tag) {
                    case 1 -> 3 + u2(bytes, at + 1);
                    case 7, 8, 16, 19, 20 -> 3;
                    case 15 -> 4;
                    case 3, 4, 9, 10, 11, 12, 17, 18 -> 5;
                    case 5, 6 -> 9;
                    default -> throw new IllegalStateException("tag " + tag + " at " + at);
                };
                at += size;
                index += (tag == 5 || tag == 6) ? 2 : 1;
            }
            layout.poolEnd = at;
            layout.accessFlagsAt = at;
            layout.thisClassAt = at + 2;
            layout.superClassAt = at + 4;
            layout.interfacesAt = at + 6;
            at += 8 + 2 * u2(bytes, at + 6);
            layout.fieldsAt = at;
            at = layout.members(at, layout.fields);
            layout.methodsAt = at;
            at = layout.members(at, layout.methods);
            layout.attributesAt = at;
            return layout;
        }

        int members(int at, List<Member> into) {
            int count = u2(bytes, at);
            at += 2;
            for (int i = 0; i < count; i++) {
                int start = at;
                int nameIndex = u2(bytes, at + 2);
                int descriptorIndex = u2(bytes, at + 4);
                int attributes = u2(bytes, at + 6);
                at += 8;
                List<Attr> found = new ArrayList<>();
                for (int a = 0; a < attributes; a++) {
                    at = attribute(at, found);
                }
                into.add(new Member(start, nameIndex, descriptorIndex, found, at));
            }
            return at;
        }

        int attribute(int at, List<Attr> into) {
            int length = i4(bytes, at + 2);
            into.add(new Attr(utf8(u2(bytes, at)), at, at + 2, length, at + 6));
            return at + 6 + length;
        }

        String utf8(int index) {
            int at = entryAt[index];
            int length = u2(bytes, at + 1);
            return new String(bytes, at + 3, length, java.nio.charset.StandardCharsets.UTF_8);
        }

        int poolCount() {
            return u2(bytes, 8);
        }

        int firstOfTag(int tag) {
            for (int i = 1; i < entryTag.length; i++) {
                if (entryTag[i] == tag) {
                    return i;
                }
            }
            throw new IllegalStateException("no pool entry with tag " + tag);
        }

        int utf8Index(String text) {
            for (int i = 1; i < entryTag.length; i++) {
                if (entryTag[i] == 1 && utf8(i).equals(text)) {
                    return i;
                }
            }
            throw new IllegalStateException("no Utf8 entry reading " + text);
        }

        Member method(String name) {
            for (Member member : methods) {
                if (utf8(member.nameIndex()).equals(name)) {
                    return member;
                }
            }
            throw new IllegalStateException("no method named " + name);
        }

        Attr code(Member member) {
            for (Attr attribute : member.attributes()) {
                if (attribute.name().equals("Code")) {
                    return attribute;
                }
            }
            throw new IllegalStateException("no Code attribute");
        }

        /** The StackMapTable inside a Code attribute, which is an attribute of an
         * attribute and so is not in the member's own list. */
        Attr stackMap(Attr code) {
            int at = code.dataAt() + 8 + i4(bytes, code.dataAt() + 4);
            at += 2 + 8 * u2(bytes, at);
            int count = u2(bytes, at);
            at += 2;
            List<Attr> found = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                at = attribute(at, found);
            }
            for (Attr attribute : found) {
                if (attribute.name().equals("StackMapTable")) {
                    return attribute;
                }
            }
            return null;
        }

        List<Region> regions() {
            List<Region> found = new ArrayList<>();
            found.add(new Region("header", 0, 10));
            found.add(new Region("constant pool", 10, poolEnd - 10));
            found.add(new Region("class, super and interfaces", poolEnd, fieldsAt - poolEnd));
            found.add(new Region("fields", fieldsAt, methodsAt - fieldsAt));
            found.add(new Region("methods", methodsAt, attributesAt - methodsAt));
            found.add(new Region("class attributes", attributesAt,
                    bytes.length - attributesAt));
            return found;
        }

        String regionOf(int at) {
            for (Region region : regions()) {
                if (at >= region.at() && at < region.at() + region.length()) {
                    return region.name();
                }
            }
            return "past the end";
        }
    }

    // Bytes.

    static int u2(byte[] bytes, int at) {
        return (bytes[at] & 0xFF) << 8 | (bytes[at + 1] & 0xFF);
    }

    static int i4(byte[] bytes, int at) {
        return (bytes[at] & 0xFF) << 24 | (bytes[at + 1] & 0xFF) << 16
                | (bytes[at + 2] & 0xFF) << 8 | (bytes[at + 3] & 0xFF);
    }

    static void write(byte[] bytes, int at, int length, long value) {
        for (int i = 0; i < length; i++) {
            bytes[at + length - 1 - i] = (byte) (value >> (8 * i));
        }
    }

    static void record(String name, String field, String value) {
        put("case." + name + "." + field, value);
    }

    static void put(String key, String value) {
        FACTS.put(key, value.replace('\t', ' '));
    }
}
