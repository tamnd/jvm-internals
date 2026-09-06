/*
 * The Java half of probes/stackmap/run.py. Three jobs, all of which need a JVM.
 *
 * The first is to ask the platform what a verification type is. `java.lang.classfile`
 * has an enum for the seven that carry no operand and an interface for each of the two
 * that do, and every one of them knows its own tag. Asking is better than retyping a
 * table, because the table is nine rows long and has been nine rows long since 2006,
 * which is exactly how a retyped number survives long enough to be trusted.
 *
 * The second is to build the two frames a walk of ordinary code will not find enough of.
 * A verification type naming an object that has been allocated and not yet constructed
 * only appears when a `new` is live across a branch, and the one naming `this` before
 * `super()` only appears in a constructor that branches before it chains. Both are
 * built here so the generated table has a measured row rather than a blank.
 *
 * The third is to count. A stack map frame is the only structure in a class file whose
 * meaning depends on the structure before it, so nothing about the attribute can be read
 * out of one frame. This walks a module of the runtime image, decodes every frame,
 * recovers the encoding each one was written in, works out the smallest encoding that
 * frame could have had, and compares. It also checks the offset arithmetic against the
 * bytecode offsets the platform resolves, which is the one rule in the attribute that
 * everybody who implements it gets wrong once.
 *
 * Output is one `key<TAB>value` line per fact, which run.py turns into JSON.
 *
 *   java Frames.java [module]      default java.base
 */

import java.lang.classfile.Attributes;
import java.lang.classfile.ClassFile;
import java.lang.classfile.ClassModel;
import java.lang.classfile.CodeModel;
import java.lang.classfile.Label;
import java.lang.classfile.MethodModel;
import java.lang.classfile.attribute.CodeAttribute;
import java.lang.classfile.attribute.StackMapFrameInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.ObjectVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.SimpleVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.UninitializedVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.VerificationTypeInfo;
import java.lang.classfile.attribute.StackMapTableAttribute;
import java.lang.constant.ClassDesc;
import java.lang.constant.ConstantDescs;
import java.lang.constant.MethodTypeDesc;
import java.lang.reflect.AccessFlag;
import java.net.URI;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public class Frames {

    // The seven frame kinds of the format, by the range of first bytes each occupies.
    // The names are the specification's. The gap between 127 and 247 has no name in the
    // specification beyond "reserved for future use", and it is 119 of the 256 values.
    private static final int SAME_END = 63;
    private static final int SAME_LOCALS_1_START = 64;
    private static final int SAME_LOCALS_1_END = 127;
    private static final int RESERVED_START = 128;
    private static final int RESERVED_END = 246;
    private static final int SAME_LOCALS_1_EXTENDED = 247;
    private static final int CHOP_START = 248;
    private static final int CHOP_END = 250;
    private static final int SAME_EXTENDED = 251;
    private static final int APPEND_START = 252;
    private static final int APPEND_END = 254;
    private static final int FULL = 255;

    private static final Map<String, Long> tally = new TreeMap<>();
    private static final List<String> notes = new ArrayList<>();

    private static void say(String key, Object value) {
        System.out.println(key + "\t" + value);
    }

    private static void bump(String key) {
        bump(key, 1);
    }

    private static void bump(String key, long by) {
        tally.merge(key, by, Long::sum);
    }

    // ---------------------------------------------------------------- asking

    /** Which kind a first byte belongs to, by the ranges above and nothing else. */
    private static String kindOf(int frameType) {
        if (frameType <= SAME_END) return "SAME";
        if (frameType <= SAME_LOCALS_1_END) return "SAME_LOCALS_1_STACK_ITEM";
        if (frameType <= RESERVED_END) return "RESERVED";
        if (frameType == SAME_LOCALS_1_EXTENDED) return "SAME_LOCALS_1_STACK_ITEM_EXTENDED";
        if (frameType <= CHOP_END) return "CHOP";
        if (frameType == SAME_EXTENDED) return "SAME_FRAME_EXTENDED";
        if (frameType <= APPEND_END) return "APPEND";
        return "FULL_FRAME";
    }

    /** One verification type costs one byte, or three when it carries an index. */
    private static int width(VerificationTypeInfo type) {
        return type.tag() >= 7 ? 3 : 1;
    }

    /**
     * A verification type as a string, so two of them can be compared without depending
     * on whether the API's implementation classes compare structurally. Two frames name
     * the same type when they name the same class or the same `new`, and a type that
     * carries nothing is its own name.
     */
    private static String key(VerificationTypeInfo type, CodeAttribute code) {
        if (type instanceof ObjectVerificationTypeInfo object) {
            return "L" + object.classSymbol().descriptorString();
        }
        if (type instanceof UninitializedVerificationTypeInfo uninitialized) {
            return "U" + code.labelToBci(uninitialized.newTarget());
        }
        return ((SimpleVerificationTypeInfo) type).name();
    }

    private static List<String> keys(List<VerificationTypeInfo> types, CodeAttribute code) {
        List<String> out = new ArrayList<>(types.size());
        for (VerificationTypeInfo type : types) out.add(key(type, code));
        return out;
    }

    /** The same name, for a type named by a descriptor rather than by a frame. */
    private static String key(ClassDesc type) {
        if (!type.isPrimitive()) return "L" + type.descriptorString();
        return switch (type.descriptorString()) {
            case "J" -> "LONG";
            case "D" -> "DOUBLE";
            case "F" -> "FLOAT";
            default -> "INTEGER";
        };
    }

    /**
     * The locals a method starts with, which the file does not store. Every frame in the
     * attribute is a delta against this one, so a probe that starts from an empty list
     * gets the first frame of every method wrong and then reports the correction as a
     * finding. The receiver of a constructor is the type that is not yet constructed.
     */
    private static List<String> entryFrame(ClassModel model, MethodModel method) {
        List<String> locals = new ArrayList<>();
        if (!method.flags().has(AccessFlag.STATIC)) {
            locals.add(method.methodName().equalsString(ConstantDescs.INIT_NAME)
                       && !model.thisClass().asSymbol().equals(ConstantDescs.CD_Object)
                    ? SimpleVerificationTypeInfo.UNINITIALIZED_THIS.name()
                    : "L" + model.thisClass().asSymbol().descriptorString());
        }
        for (ClassDesc parameter : method.methodTypeSymbol().parameterList()) {
            locals.add(key(parameter));
        }
        return locals;
    }

    private static int width(List<VerificationTypeInfo> types) {
        int total = 0;
        for (VerificationTypeInfo type : types) total += width(type);
        return total;
    }

    /** The bytes this frame occupies, decided by the first byte it was written with. */
    private static int encoded(int frameType, List<VerificationTypeInfo> locals,
                               List<VerificationTypeInfo> stack, int appended) {
        if (frameType <= SAME_END) return 1;
        if (frameType <= SAME_LOCALS_1_END) return 1 + width(stack.get(0));
        if (frameType == SAME_LOCALS_1_EXTENDED) return 3 + width(stack.get(0));
        if (frameType <= CHOP_END) return 3;
        if (frameType == SAME_EXTENDED) return 3;
        if (frameType <= APPEND_END) {
            int total = 3;
            for (int i = locals.size() - appended; i < locals.size(); i++) {
                total += width(locals.get(i));
            }
            return total;
        }
        return 7 + width(locals) + width(stack);
    }

    /**
     * The smallest first byte this frame could have been written with, given the frame
     * before it. Every branch here is a rule from the format rather than a preference,
     * so a frame written with a larger one carries bytes that decode to the same thing.
     */
    private static int minimal(List<String> locals, List<String> stack,
                               List<String> wasLocals, int delta) {
        boolean small = delta <= SAME_END;
        if (stack.isEmpty() && locals.equals(wasLocals)) {
            return small ? delta : SAME_EXTENDED;
        }
        if (stack.size() == 1 && locals.equals(wasLocals)) {
            return small ? SAME_LOCALS_1_START + delta : SAME_LOCALS_1_EXTENDED;
        }
        if (stack.isEmpty() && locals.size() < wasLocals.size()
                && wasLocals.size() - locals.size() <= 3
                && locals.equals(wasLocals.subList(0, locals.size()))) {
            return SAME_EXTENDED - (wasLocals.size() - locals.size());
        }
        if (stack.isEmpty() && locals.size() > wasLocals.size()
                && locals.size() - wasLocals.size() <= 3
                && wasLocals.equals(locals.subList(0, wasLocals.size()))) {
            return APPEND_START + (locals.size() - wasLocals.size()) - 1;
        }
        return FULL;
    }

    /** How many locals an APPEND frame added, which its first byte states directly. */
    private static int appendedBy(int frameType) {
        return frameType >= APPEND_START && frameType <= APPEND_END
                ? frameType - APPEND_START + 1 : 0;
    }

    private static void askThePlatform() {
        for (SimpleVerificationTypeInfo type : SimpleVerificationTypeInfo.values()) {
            say("type." + type.name() + ".tag", type.tag());
            say("type." + type.name() + ".carries", "");
        }
        say("type.OBJECT.tag",
            ObjectVerificationTypeInfo.of(ConstantDescs.CD_String).tag());
        say("type.OBJECT.carries", "constant pool index");
        say("type.UNINITIALIZED.carries", "bytecode offset");
    }

    // ------------------------------------------------------- building the rare two

    /**
     * A class whose three methods force the verification types a walk of a module does
     * not turn up. `held` keeps a freshly allocated object on the operand stack across a
     * branch, so the frame at the target has to name it as allocated and not constructed.
     * `stored` puts the same reference in a local variable instead, which puts that type
     * in the locals array rather than the stack. The constructor branches before it
     * chains, so the frame there has to name `this` the same way.
     *
     * `stored` is here because of what the scan found rather than the other way round. In
     * two runtime images this type occurs several hundred times and never once in a local
     * variable, and a count of zero is a fact about what a compiler emits and not about
     * what the format allows, so the difference is worth having a file to point at.
     */
    private static byte[] awkward() {
        ClassDesc self = ClassDesc.of("Awkward");
        ClassDesc object = ConstantDescs.CD_Object;
        MethodTypeDesc takesBoolean = MethodTypeDesc.of(object, ConstantDescs.CD_boolean);
        return ClassFile.of().build(self, builder -> {
            builder.withMethodBody("held", takesBoolean,
                ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                    Label after = code.newLabel();
                    code.new_(object).dup()
                        .iload(0).ifeq(after)
                        .nop()
                        .labelBinding(after)
                        .invokespecial(object, ConstantDescs.INIT_NAME,
                                       ConstantDescs.MTD_void)
                        .areturn();
                });
            builder.withMethodBody("stored", takesBoolean,
                ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                    Label after = code.newLabel();
                    code.new_(object).astore(1)
                        .iload(0).ifeq(after)
                        .nop()
                        .labelBinding(after)
                        .aload(1)
                        .invokespecial(object, ConstantDescs.INIT_NAME,
                                       ConstantDescs.MTD_void)
                        .aload(1).areturn();
                });
            builder.withMethodBody(ConstantDescs.INIT_NAME,
                MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_boolean),
                ClassFile.ACC_PUBLIC, code -> {
                    Label after = code.newLabel();
                    code.aload(0)
                        .iload(1).ifeq(after)
                        .nop()
                        .labelBinding(after)
                        .invokespecial(object, ConstantDescs.INIT_NAME,
                                       ConstantDescs.MTD_void)
                        .return_();
                });
        });
    }

    private static void buildTheRareTwo() {
        byte[] bytes = awkward();
        ClassModel model = ClassFile.of().parse(bytes);
        Map<String, Long> found = new TreeMap<>();
        for (String side : List.of("locals", "stack")) {
            found.put("uninitialized_in_" + side, 0L);
            found.put("uninitialized_this_in_" + side, 0L);
        }
        int tag = -1;
        for (MethodModel method : model.methods()) {
            CodeModel code = method.code().orElse(null);
            if (code == null) continue;
            StackMapTableAttribute table =
                code.findAttribute(Attributes.stackMapTable()).orElse(null);
            if (table == null) continue;
            for (StackMapFrameInfo frame : table.entries()) {
                for (String side : List.of("locals", "stack")) {
                    List<VerificationTypeInfo> types =
                        side.equals("locals") ? frame.locals() : frame.stack();
                    for (VerificationTypeInfo type : types) {
                        if (type instanceof UninitializedVerificationTypeInfo) {
                            found.merge("uninitialized_in_" + side, 1L, Long::sum);
                            tag = type.tag();
                        }
                        if (type == SimpleVerificationTypeInfo.UNINITIALIZED_THIS) {
                            found.merge("uninitialized_this_in_" + side, 1L, Long::sum);
                        }
                    }
                }
            }
        }
        say("type.UNINITIALIZED.tag", tag);
        for (Map.Entry<String, Long> entry : found.entrySet()) {
            say("built." + entry.getKey(), entry.getValue());
        }
        say("built.bytes", bytes.length);
        // The file the two counts above came from is only evidence if a JVM agrees it is
        // a class, so it is handed to the platform's own verifier before anything is said
        // about it. A failure here is the finding, not a crash.
        try {
            var lookup = java.lang.invoke.MethodHandles.lookup();
            // Defining a class does not verify it. Linking does, and initialising is the
            // shortest way to insist on linking.
            lookup.ensureInitialized(lookup.defineClass(bytes));
            say("built.verifies", 1);
        } catch (Throwable failed) {
            say("built.verifies", 0);
            say("built.rejected_by", failed.getClass().getName() + ": " + failed.getMessage());
        }
    }

    // ------------------------------------------------------- checking the checker

    /**
     * Six frames whose smallest encoding is known by hand, run through the same function
     * the scan uses. The scan's headline number is a zero, and a zero from a function
     * nobody has watched discriminate is not a measurement, so these are emitted with the
     * results and run.py refuses to write a file in which any of them is wrong.
     */
    private static void checkTheChecker() {
        List<String> one = List.of("Ljava/lang/String;");
        List<String> two = List.of("Ljava/lang/String;", "INTEGER");
        List<String> none = List.of();
        say("check.same", minimal(one, none, one, 5));
        say("check.same_extended", minimal(one, none, one, 900));
        say("check.same_locals_1", minimal(one, one, one, 5));
        say("check.chop", minimal(one, none, two, 5));
        say("check.append", minimal(two, none, one, 5));
        say("check.full", minimal(two, two, one, 5));
    }

    // ------------------------------------------------------------ the second reader

    /**
     * A class file walked as bytes, with no help from the platform, to find out how long
     * every `StackMapTable` attribute really is.
     *
     * The scan's byte counts are worked out from decoded frames by the same width rules
     * twice over, once for what was written and once for the smallest thing that could
     * have been, so a width rule that is wrong is wrong on both sides and the difference
     * still comes out at zero. That is a comparison checking itself. This walks the file
     * to the attribute and reads `attribute_length` out of it, which is the number the
     * file actually contains, and the scan is only reported if the two agree.
     *
     * It is also a second witness for the frame type histogram, which is the other claim
     * on this page that a single decoder could be quietly wrong about.
     */
    private static final class Raw {
        private final byte[] b;
        private int at;
        private String[] utf8;
        long attributeBytes;

        Raw(byte[] bytes) { this.b = bytes; }

        private int u1() { return b[at++] & 0xff; }
        private int u2() { return (u1() << 8) | u1(); }
        private long u4() { return ((long) u2() << 16) | u2(); }

        private void pool() {
            int count = u2();
            utf8 = new String[count];
            for (int index = 1; index < count; index++) {
                int tag = u1();
                switch (tag) {
                    case 1 -> {
                        int length = u2();
                        utf8[index] = new String(b, at, length, java.nio.charset.StandardCharsets.UTF_8);
                        at += length;
                    }
                    case 7, 8, 16, 19, 20 -> at += 2;
                    case 15 -> at += 3;
                    case 3, 4, 9, 10, 11, 12, 17, 18 -> at += 4;
                    case 5, 6 -> { at += 8; index++; }
                    default -> throw new IllegalStateException("constant pool tag " + tag);
                }
            }
        }

        /** Every attribute of one member, with `inCode` deciding what to look inside. */
        private void attributes(boolean inCode) {
            int count = u2();
            for (int i = 0; i < count; i++) {
                String name = utf8[u2()];
                long length = u4();
                int end = (int) (at + length);
                if ("Code".equals(name) && !inCode) {
                    at += 4;                      // max_stack, max_locals
                    // Read first and add second, every time. `at += u4()` loads the old
                    // `at` before the call moves it, so the four bytes of the length
                    // itself are silently dropped and the walk lands in the middle of an
                    // instruction several hundred kilobytes later.
                    int codeLength = (int) u4();
                    at += codeLength;
                    int handlers = u2();
                    at += 8 * handlers;
                    attributes(true);
                } else if ("StackMapTable".equals(name) && inCode) {
                    // Six bytes of header, then whatever the length said.
                    attributeBytes += 6 + length;
                    int entries = u2();
                    for (int frame = 0; frame < entries; frame++) {
                        int frameType = u1();
                        bump("rawframetype." + frameType);
                        skipFrame(frameType);
                    }
                }
                at = end;
            }
        }

        /** One frame's payload, by the same width rules the decoded side uses. */
        private void skipFrame(int frameType) {
            if (frameType <= SAME_END) return;
            if (frameType <= SAME_LOCALS_1_END) { skipType(); return; }
            if (frameType <= RESERVED_END) throw new IllegalStateException("reserved");
            if (frameType == SAME_LOCALS_1_EXTENDED) { at += 2; skipType(); return; }
            if (frameType <= SAME_EXTENDED) { at += 2; return; }
            if (frameType <= APPEND_END) {
                at += 2;
                for (int i = 0; i < frameType - APPEND_START + 1; i++) skipType();
                return;
            }
            at += 2;
            for (int i = u2(); i > 0; i--) skipType();
            for (int i = u2(); i > 0; i--) skipType();
        }

        private void skipType() {
            int tag = u1();
            if (tag >= 7) at += 2;
        }

        void walk() {
            at = 8;                               // magic, minor, major
            pool();
            at += 6;                              // access flags, this class, super class
            int interfaces = u2();
            at += 2 * interfaces;
            for (int kind = 0; kind < 2; kind++) {
                for (int i = u2(); i > 0; i--) {
                    at += 6;                      // access flags, name, descriptor
                    attributes(false);
                }
            }
        }
    }

    // ---------------------------------------------------------------- counting

    private static String name(Path file) {
        String text = file.toString();
        int cut = text.indexOf("/", "/modules/".length());
        return text.substring(cut + 1).replace(".class", "").replace('/', '.');
    }

    private static void count(String module) throws Exception {
        Path root = FileSystems.getFileSystem(URI.create("jrt:/")).getPath("/modules/" + module);
        ClassFile parser = ClassFile.of();
        List<Path> files = new ArrayList<>();
        try (var walk = Files.walk(root)) {
            walk.filter(path -> path.toString().endsWith(".class")).forEach(files::add);
        }
        files.sort(Path::compareTo);

        long classes = 0;
        long classBytes = 0;
        long methods = 0;
        long withCode = 0;
        long withFrames = 0;
        long codeBytes = 0;
        long frames = 0;
        long attributeBytes = 0;
        long minimalBytes = 0;
        long allFullBytes = 0;
        long larger = 0;
        long deltaMax = 0;
        long deltaOver63 = 0;
        long arithmeticChecked = 0;
        long arithmeticWrong = 0;
        int widestLocals = 0;
        int deepestStack = 0;
        String widestWhere = "";
        String deepestWhere = "";
        String largerWhere = "";

        long rawBytes = 0;
        for (Path file : files) {
            byte[] bytes = Files.readAllBytes(file);
            ClassModel model = parser.parse(bytes);
            classes++;
            classBytes += bytes.length;
            Raw raw = new Raw(bytes);
            raw.walk();
            rawBytes += raw.attributeBytes;
            for (MethodModel method : model.methods()) {
                methods++;
                CodeModel code = method.code().orElse(null);
                if (code == null) continue;
                withCode++;
                CodeAttribute located = (CodeAttribute) code;
                codeBytes += located.codeLength();
                StackMapTableAttribute table =
                    code.findAttribute(Attributes.stackMapTable()).orElse(null);
                if (table == null) {
                    continue;
                }
                withFrames++;
                // The attribute's own header and its count, before any frame.
                attributeBytes += 8;
                minimalBytes += 8;
                allFullBytes += 8;

                List<String> wasLocals = entryFrame(model, method);
                boolean first = true;
                int previous = -1;
                for (StackMapFrameInfo frame : table.entries()) {
                    frames++;
                    int frameType = frame.frameType();
                    String kind = kindOf(frameType);
                    bump("frametype." + frameType);
                    bump("kind." + kind);

                    List<VerificationTypeInfo> locals = frame.locals();
                    List<VerificationTypeInfo> stack = frame.stack();
                    for (VerificationTypeInfo type : locals) {
                        bump("item." + type.tag());
                        bump("where.locals." + type.tag());
                    }
                    for (VerificationTypeInfo type : stack) {
                        bump("item." + type.tag());
                        bump("where.stack." + type.tag());
                    }
                    if (locals.size() > widestLocals) {
                        widestLocals = locals.size();
                        widestWhere = name(file) + "." + method.methodName().stringValue();
                    }
                    if (stack.size() > deepestStack) {
                        deepestStack = stack.size();
                        deepestWhere = name(file) + "." + method.methodName().stringValue();
                    }

                    int at = located.labelToBci(frame.target());
                    int delta = first ? at : at - previous - 1;
                    if (delta > deltaMax) deltaMax = delta;
                    if (delta > SAME_END) deltaOver63++;

                    // The offset arithmetic, checked rather than quoted. A frame whose
                    // first byte carries its own delta says what the delta was, so the
                    // rule can be tested against the offset the platform resolved.
                    if (frameType <= SAME_LOCALS_1_END) {
                        arithmeticChecked++;
                        int carried = frameType <= SAME_END
                                ? frameType : frameType - SAME_LOCALS_1_START;
                        if (carried != delta) {
                            arithmeticWrong++;
                            if (notes.size() < 8) {
                                notes.add(name(file) + "." + method.methodName().stringValue()
                                          + "|frame type " + frameType + " at bci " + at);
                            }
                        }
                    }

                    List<String> localKeys = keys(locals, located);
                    int appended = appendedBy(frameType);
                    int here = encoded(frameType, locals, stack, appended);
                    int best = minimal(localKeys, keys(stack, located), wasLocals, delta);
                    int there = encoded(best, locals, stack, appendedBy(best));
                    attributeBytes += here;
                    minimalBytes += there;
                    allFullBytes += encoded(FULL, locals, stack, 0);
                    if (best != frameType) {
                        larger++;
                        bump("larger." + kind + "-to-" + kindOf(best));
                        if (largerWhere.isEmpty()) {
                            largerWhere = name(file) + "." + method.methodName().stringValue();
                        }
                    }

                    wasLocals = localKeys;
                    previous = at;
                    first = false;
                }
            }
        }

        say("scan.module", module);
        say("scan.classes", classes);
        say("scan.class_bytes", classBytes);
        say("scan.methods", methods);
        say("scan.methods_with_code", withCode);
        say("scan.methods_with_frames", withFrames);
        say("scan.code_bytes", codeBytes);
        say("scan.frames", frames);
        say("scan.attribute_bytes", attributeBytes);
        say("scan.minimal_bytes", minimalBytes);
        say("scan.all_full_bytes", allFullBytes);
        say("scan.raw_attribute_bytes", rawBytes);
        say("scan.frames_larger_than_needed", larger);
        say("scan.delta_max", deltaMax);
        say("scan.delta_over_63", deltaOver63);
        say("scan.arithmetic_checked", arithmeticChecked);
        say("scan.arithmetic_wrong", arithmeticWrong);
        say("scan.widest_locals", widestLocals);
        say("scan.widest_locals_in", widestWhere);
        say("scan.deepest_stack", deepestStack);
        say("scan.deepest_stack_in", deepestWhere);
        say("scan.first_larger_in", largerWhere);
        for (String note : notes) {
            say("note.arithmetic", note);
        }
        for (Map.Entry<String, Long> entry : tally.entrySet()) {
            say(entry.getKey(), entry.getValue());
        }
    }

    public static void main(String[] args) throws Exception {
        askThePlatform();
        buildTheRareTwo();
        checkTheChecker();
        count(args.length > 0 ? args[0] : "java.base");
    }
}
