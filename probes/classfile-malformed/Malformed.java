import static java.lang.constant.ConstantDescs.CD_Object;
import static java.lang.constant.ConstantDescs.CD_String;
import static java.lang.constant.ConstantDescs.CD_int;
import static java.lang.constant.ConstantDescs.CD_void;

import java.lang.classfile.ClassFile;
import java.lang.classfile.CodeBuilder;
import java.lang.classfile.Label;
import java.lang.classfile.attribute.StackMapFrameInfo;
import java.lang.classfile.attribute.StackMapTableAttribute;
import java.lang.constant.ClassDesc;
import java.lang.constant.MethodTypeDesc;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.TreeMap;

/**
 * Six class files that are wrong on purpose, and what it takes to make each one.
 *
 * B11's boss fight and the whole fuzzer rest on being able to produce a class file the JVM
 * refuses, on demand and for a stated reason. `java.lang.classfile` is a well designed API
 * and a well designed API stops you writing nonsense, so the question is where it stops
 * you, and what is left once it does.
 *
 * Each case is asked twice. First: can the API express it. Second: with the bytes in hand,
 * however they were obtained, where does the JVM notice. Those are different questions and
 * a table that ran them together would be useless, because "the API refused" and "the JVM
 * refused" are opposite results.
 *
 * The second question has four answers and the whole point is telling them apart. Parse is
 * `defineClass`, before any code runs. Link is the first active use, which is where the
 * verifier lives. Run is the first execution of the offending instruction, which is where
 * resolution happens and is why a method reference to a method that does not exist gets
 * all the way into a running program. Accepted means nobody objected, which for a file
 * this deliberately broken is the most interesting answer of the four.
 *
 * Output is one `key\tvalue` line per fact, sorted, the same shape the capability probe
 * uses. Nothing here writes outside the directory it is given.
 */
public class Malformed {

    static final TreeMap<String, String> FACTS = new TreeMap<>();

    /** Where a class file stops being acceptable, in the order the JVM finds out. */
    enum Stage {
        parse,     // defineClass: structure, flags, versions, indices into the pool
        link,      // first active use: the verifier
        run,       // the instruction executes: resolution
        accepted   // it ran
    }

    public static void main(String[] args) throws Exception {
        Path out = args.length > 0 ? Path.of(args[0]) : null;

        poolIndexPastEnd(out);
        methodRefDescriptorMismatch(out);
        stackMapIntWhereReference(out);
        finalAndAbstract(out);
        maxStackTooSmall(out);
        versionAboveThePin(out);

        put("probe.class_file_major", String.valueOf(ClassFile.latestMajorVersion()));
        for (var entry : FACTS.entrySet()) {
            System.out.println(entry.getKey() + "\t" + entry.getValue());
        }
    }

    // The six cases.

    /**
     * A constant pool index pointing past the end of the pool.
     *
     * There is no API for this and there cannot be one. The builder hands out entries, not
     * numbers, so an index it did not issue is not a value any method here accepts. Asking
     * the pool for an entry it does not have is the closest thing to an attempt, and it is
     * recorded because "the API refused" is a weaker claim than "the API has no way to say
     * it", and this is the second one.
     */
    static void poolIndexPastEnd(Path out) throws Exception {
        String name = "pool_index_past_end";
        try {
            var pool = java.lang.classfile.constantpool.ConstantPoolBuilder.of();
            pool.entryByIndex(9999);
            record(name, "api", "built");
        } catch (Throwable t) {
            record(name, "api", "refused");
            record(name, "api_error", describe(t));
        }
        record(name, "api_note", "the builder issues entries, so an index is never named");

        byte[] valid = simple("PoolIndexPastEnd", MethodTypeDesc.of(CD_int), code -> {
            code.invokestatic(ClassDesc.of("PoolIndexPastEnd"), "helper",
                    MethodTypeDesc.of(CD_int));
            code.ireturn();
        }, builder -> builder.withMethodBody("helper", MethodTypeDesc.of(CD_int),
                ClassFile.ACC_STATIC, code -> {
                    code.bipush(7);
                    code.ireturn();
                }));

        // The operand of the invokestatic, set to an entry the pool does not have. Found by
        // searching for the opcode rather than by counting bytes, because the pool in front
        // of it is a different size every time anything about the class changes.
        byte[] broken = valid.clone();
        int at = find(broken, new int[] {0xB8, -1, -1, 0xAC});
        int beyond = poolCount(broken) + 100;
        broken[at + 1] = (byte) (beyond >> 8);
        broken[at + 2] = (byte) beyond;
        record(name, "patched", "invokestatic operand set to " + beyond
                + ", pool has " + (poolCount(broken) - 1) + " entries");
        load(name, "PoolIndexPastEnd", broken, out);
    }

    /**
     * A method reference whose descriptor does not match anything on the target.
     *
     * `String.length` takes nothing and this asks for the one that takes an int. The API is
     * fine with it, because a class file is allowed to name a method in another class that
     * the compiler has never seen, and checking now would mean loading String at build
     * time. That is separate compilation working exactly as designed.
     */
    static void methodRefDescriptorMismatch(Path out) throws Exception {
        String name = "method_ref_descriptor_mismatch";
        byte[] bytes = attempt(name, () -> simple("MethodRefMismatch",
                MethodTypeDesc.of(CD_int), code -> {
            code.ldc("abc");
            code.bipush(42);
            code.invokevirtual(CD_String, "length", MethodTypeDesc.of(CD_int, CD_int));
            code.ireturn();
        }));
        load(name, "MethodRefMismatch", bytes, out);
    }

    /**
     * A stack map frame that says int where the verifier needs a reference.
     *
     * The builder computes stack maps itself, so the only way to hand it a wrong one is to
     * turn that off and attach the attribute by hand. Both halves of that are public API,
     * which is the answer this issue wanted: the frames are data the caller can supply.
     */
    static void stackMapIntWhereReference(Path out) throws Exception {
        String name = "stack_map_int_where_reference";
        byte[] bytes = attempt(name, () ->
                ClassFile.of(ClassFile.StackMapsOption.DROP_STACK_MAPS)
                        .build(ClassDesc.of("StackMapLies"), builder -> {
                            builder.withFlags(ClassFile.ACC_PUBLIC | ClassFile.ACC_SUPER);
                            builder.withMethodBody("go", MethodTypeDesc.of(CD_Object),
                                    ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                                        Label later = code.newLabel();
                                        code.aconst_null();
                                        code.astore(0);
                                        code.iconst_1();
                                        code.ifeq(later);
                                        code.aload(0);
                                        code.areturn();
                                        code.labelBinding(later);
                                        code.aload(0);
                                        code.areturn();
                                        // Local 0 holds a reference and this says it holds
                                        // an int, at the one offset where the verifier has
                                        // to take the frame's word for it.
                                        code.with(StackMapTableAttribute.of(List.of(
                                                StackMapFrameInfo.of(later,
                                                        List.of(StackMapFrameInfo
                                                                .SimpleVerificationTypeInfo
                                                                .INTEGER),
                                                        List.of()))));
                                    });
                            main("StackMapLies", MethodTypeDesc.of(CD_Object)).add(builder);
                        }));
        load(name, "StackMapLies", bytes, out);
    }

    /** A class that is both final and abstract, which no subclass can ever satisfy. */
    static void finalAndAbstract(Path out) throws Exception {
        String name = "final_and_abstract";
        byte[] bytes = attempt(name, () -> ClassFile.of()
                .build(ClassDesc.of("FinalAndAbstract"), builder -> {
                    builder.withFlags(ClassFile.ACC_PUBLIC | ClassFile.ACC_FINAL
                            | ClassFile.ACC_ABSTRACT);
                    builder.withMethodBody("go", MethodTypeDesc.of(CD_void),
                            ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                            CodeBuilder::return_);
                    main("FinalAndAbstract", MethodTypeDesc.of(CD_void)).add(builder);
                }));
        if (bytes == null) {
            // The API stopped it, so the flags go in with a patch. access_flags sits two
            // bytes after the constant pool, which has to be walked to be found, and
            // walking it is the same work the fuzzer will be doing anyway.
            byte[] valid = simple("FinalAndAbstract", MethodTypeDesc.of(CD_void),
                    CodeBuilder::return_);
            int at = poolEnd(valid);
            int flags = ((valid[at] & 0xFF) << 8 | (valid[at + 1] & 0xFF))
                    | ClassFile.ACC_FINAL | ClassFile.ACC_ABSTRACT;
            valid[at] = (byte) (flags >> 8);
            valid[at + 1] = (byte) flags;
            record(name, "patched", "access_flags set to 0x" + Integer.toHexString(flags));
            bytes = valid;
        }
        load(name, "FinalAndAbstract", bytes, out);
    }

    /**
     * A code attribute whose max_stack is one too small for the body underneath it.
     *
     * max_stack is computed, not supplied, so there is no API for this either. The patch
     * finds the body by its opcodes and steps back over the three fields in front of it.
     */
    static void maxStackTooSmall(Path out) throws Exception {
        String name = "max_stack_too_small";
        record(name, "api", "refused");
        record(name, "api_note", "max_stack is computed by the builder and cannot be set");

        // bipush 42, bipush 43, iadd, ireturn. Two values on the stack at once, and a byte
        // pattern that appears nowhere else in the file.
        byte[] bytes = simple("MaxStackTooSmall", MethodTypeDesc.of(CD_int), code -> {
            code.bipush(42);
            code.bipush(43);
            code.iadd();
            code.ireturn();
        });
        int at = find(bytes, new int[] {0x10, 42, 0x10, 43, 0x60, 0xAC});
        // In front of the code array: max_stack, max_locals, code_length. The attribute
        // name and length are in front of those.
        int maxStack = at - 8;
        record(name, "measured_max_stack",
                String.valueOf((bytes[maxStack] & 0xFF) << 8 | (bytes[maxStack + 1] & 0xFF)));
        bytes[maxStack] = 0;
        bytes[maxStack + 1] = 1;
        record(name, "patched", "max_stack lowered to 1 for a body that needs 2");
        load(name, "MaxStackTooSmall", bytes, out);
    }

    /** A major version one above what this JDK will read. */
    static void versionAboveThePin(Path out) throws Exception {
        String name = "version_above_the_pin";
        int above = ClassFile.latestMajorVersion() + 1;
        byte[] bytes = attempt(name, () -> ClassFile.of()
                .build(ClassDesc.of("FromTheFuture"), builder -> {
                    builder.withVersion(above, 0);
                    builder.withFlags(ClassFile.ACC_PUBLIC | ClassFile.ACC_SUPER);
                    builder.withMethodBody("go", MethodTypeDesc.of(CD_void),
                            ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                            CodeBuilder::return_);
                    main("FromTheFuture", MethodTypeDesc.of(CD_void)).add(builder);
                }));
        record(name, "version", String.valueOf(above));
        load(name, "FromTheFuture", bytes, out);
    }

    // The two things every case does.

    /** Build it if the API will, and record what it said if it will not. */
    static byte[] attempt(String name, Build build) {
        try {
            byte[] bytes = build.run();
            record(name, "api", "built");
            return bytes;
        } catch (Throwable t) {
            record(name, "api", "refused");
            record(name, "api_error", describe(t));
            return null;
        }
    }

    /**
     * Take the bytes as far into a running program as they will go, and say where they
     * stopped.
     *
     * Three separate attempts and not one try block, because the whole result is which of
     * the three failed. A fresh loader each time so that a name is never already resolved.
     */
    static void load(String name, String className, byte[] bytes, Path out) throws Exception {
        if (bytes == null) {
            record(name, "stage", "not built");
            return;
        }
        record(name, "bytes", String.valueOf(bytes.length));
        if (out != null) {
            Files.createDirectories(out);
            Files.write(out.resolve(className + ".class"), bytes);
        }

        Loader loader = new Loader();
        Class<?> defined;
        try {
            defined = loader.define(className, bytes);
        } catch (Throwable t) {
            record(name, "stage", Stage.parse.name());
            record(name, "error", describe(t));
            return;
        }

        try {
            Class.forName(className, true, loader);
        } catch (Throwable t) {
            record(name, "stage", Stage.link.name());
            record(name, "error", describe(t));
            return;
        }

        Method go = null;
        for (Method method : defined.getDeclaredMethods()) {
            if (method.getName().equals("go") && method.getParameterCount() == 0) {
                go = method;
            }
        }
        if (go == null) {
            record(name, "stage", Stage.accepted.name());
            record(name, "error", "nothing to call, the class loaded and linked");
            return;
        }
        try {
            go.setAccessible(true);
            Object answer = go.invoke(null);
            record(name, "stage", Stage.accepted.name());
            record(name, "returned", String.valueOf(answer));
        } catch (Throwable t) {
            record(name, "stage", Stage.run.name());
            record(name, "error", describe(t));
        }
    }

    // Small helpers.

    interface Build {
        byte[] run() throws Exception;
    }

    interface Extra {
        void add(java.lang.classfile.ClassBuilder builder);
    }

    static final class Loader extends ClassLoader {
        Loader() {
            super(Malformed.class.getClassLoader());
        }

        Class<?> define(String name, byte[] bytes) {
            return defineClass(name, bytes, 0, bytes.length);
        }
    }

    /** A valid public class with one static `go` method and whatever else is asked for. */
    static byte[] simple(String className, MethodTypeDesc descriptor,
            java.util.function.Consumer<CodeBuilder> body, Extra... extras) {
        return ClassFile.of().build(ClassDesc.of(className), builder -> {
            builder.withFlags(ClassFile.ACC_PUBLIC | ClassFile.ACC_SUPER);
            builder.withMethodBody("go", descriptor,
                    ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, body::accept);
            main(className, descriptor).add(builder);
            for (Extra extra : extras) {
                extra.add(builder);
            }
        });
    }

    /**
     * A `main` that calls `go` and says so.
     *
     * Every generated class gets one, so that the same file can be handed to the launcher
     * as well as to a class loader. With the verifier off, whether this prints is the
     * difference between a broken class file that loads and a broken class file that
     * executes, and those are two very different lessons.
     */
    static Extra main(String className, MethodTypeDesc goDescriptor) {
        return builder -> builder.withMethodBody("main",
                MethodTypeDesc.of(CD_void, CD_String.arrayType()),
                ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC, code -> {
                    code.invokestatic(ClassDesc.of(className), "go", goDescriptor);
                    if (!goDescriptor.returnType().equals(CD_void)) {
                        code.pop();
                    }
                    code.getstatic(ClassDesc.of("java.lang.System"), "out",
                            ClassDesc.of("java.io.PrintStream"));
                    code.ldc("go ran");
                    code.invokevirtual(ClassDesc.of("java.io.PrintStream"), "println",
                            MethodTypeDesc.of(CD_void, CD_String));
                    code.return_();
                });
    }

    static int poolCount(byte[] bytes) {
        return (bytes[8] & 0xFF) << 8 | (bytes[9] & 0xFF);
    }

    /**
     * Walk the constant pool and return the offset of access_flags.
     *
     * Every entry is a tag and a payload whose length depends on the tag, so there is no
     * way to skip the pool without knowing all of them. Long and Double take two slots,
     * which is the famous mistake and the reason this loop counts the way it does.
     */
    static int poolEnd(byte[] bytes) {
        int at = 10;
        int index = 1;
        int count = poolCount(bytes);
        while (index < count) {
            int tag = bytes[at] & 0xFF;
            int size = switch (tag) {
                case 1 -> 3 + ((bytes[at + 1] & 0xFF) << 8 | (bytes[at + 2] & 0xFF));
                case 7, 8, 16, 19, 20 -> 3;
                case 15 -> 4;
                case 3, 4, 9, 10, 11, 12, 17, 18 -> 5;
                case 5, 6 -> 9;
                default -> throw new IllegalStateException("tag " + tag + " at " + at);
            };
            at += size;
            index += (tag == 5 || tag == 6) ? 2 : 1;
        }
        return at;
    }

    /**
     * Where a byte pattern starts, and a failure if it is not there exactly once.
     *
     * A -1 in the pattern matches any byte, which is how an instruction gets found by its
     * opcode and its shape when the operand in the middle is the unknown being looked for.
     */
    static int find(byte[] bytes, int[] pattern) {
        int found = -1;
        for (int at = 0; at + pattern.length <= bytes.length; at++) {
            boolean match = true;
            for (int i = 0; i < pattern.length; i++) {
                if (pattern[i] >= 0 && (bytes[at + i] & 0xFF) != (pattern[i] & 0xFF)) {
                    match = false;
                    break;
                }
            }
            if (match) {
                if (found >= 0) {
                    throw new IllegalStateException("pattern appears more than once");
                }
                found = at;
            }
        }
        if (found < 0) {
            throw new IllegalStateException("pattern not found");
        }
        return found;
    }

    static String describe(Throwable t) {
        Throwable cause = t instanceof InvocationTargetException && t.getCause() != null
                ? t.getCause()
                : t;
        String message = cause.getMessage();
        String text = cause.getClass().getName() + (message == null ? "" : ": " + message);
        return text.replace('\n', ' ').replace('\r', ' ').trim();
    }

    static void record(String name, String field, String value) {
        put("case." + name + "." + field, value);
    }

    static void put(String key, String value) {
        FACTS.put(key, value);
    }
}
