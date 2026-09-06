// Measure the verifier's type system by building class files and watching which ones load.
//
// Issue #15. BP-VERIFY section 2 is the verification type system: the types a frame can
// name and which of them may be assigned to which. JVMS 4.10.1.2 states that relation as
// nine Prolog rules and no table, and nobody prints the table, so this builds it.
//
// One cell of the table is one class file. A constructor stores a value of the type being
// assigned from into local 1, jumps forward, and carries a StackMapTable that says local 1
// holds the type being assigned to. The type checker accepts the file if and only if the
// first type is assignable to the second, which makes a load a yes and a VerifyError a no.
// Nothing here reads a rule and decides; every cell is a JVM's answer.
//
// Three other things are measured next to it, all of them about the same subject, which is
// where the verifier's guarantee stops.
//
//   the interface hole   The type system says every class is assignable to every interface
//                        (JVMS 4.10.1.2). This builds a class that puts a String in a local
//                        the frame calls Runnable and then calls run() on it, which is a
//                        file that verifies and a program that cannot work.
//
//   the failover         A class file at major version 50 whose StackMapTable is wrong is
//                        handed to the second verifier, which does not read the attribute.
//                        This writes the same wrong attribute at five versions and records
//                        which of them load.
//
//   the census           Every runtime type check in java.base, counted, because each one
//                        is a place the verifier decided not to prove something.
//
// Printed as key<TAB>value lines for run.py to fold. Run it through run.py rather than
// directly, because run.py is what refuses to write a results file when a self check fails.

import java.io.IOException;
import java.lang.classfile.ClassFile;
import java.lang.classfile.ClassModel;
import java.lang.classfile.CodeModel;
import java.lang.classfile.Instruction;
import java.lang.classfile.Label;
import java.lang.classfile.MethodModel;
import java.lang.classfile.attribute.StackMapFrameInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.ObjectVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.SimpleVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.UninitializedVerificationTypeInfo;
import java.lang.classfile.attribute.StackMapFrameInfo.VerificationTypeInfo;
import java.lang.classfile.attribute.StackMapTableAttribute;
import java.lang.classfile.instruction.FieldInstruction;
import java.lang.classfile.instruction.InvokeInstruction;
import java.lang.classfile.instruction.TypeCheckInstruction;
import java.lang.constant.ClassDesc;
import java.lang.constant.ConstantDescs;
import java.lang.constant.MethodTypeDesc;
import java.lang.invoke.MethodHandles;
import java.lang.reflect.InvocationTargetException;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Stream;

public final class Types {

    static final MethodTypeDesc VOID = MethodTypeDesc.of(ConstantDescs.CD_void);
    static final ClassDesc RUNNABLE = ClassDesc.of("java.lang.Runnable");
    static final ClassDesc CLONEABLE = ClassDesc.of("java.lang.Cloneable");
    static final ClassDesc SERIALIZABLE = ClassDesc.of("java.io.Serializable");

    /** The local the grid tests. Local 0 is `this`, which every cell needs to stay put. */
    static final int SLOT = 1;
    /** Where the unconstructed object is parked, so every cell has a `new` to point at. */
    static final int PARK = 4;
    /** Written to only so that max_locals is wide enough for a frame to name the others. */
    static final int PAD = 5;

    /**
     * One verification type, in the three forms this needs: the name the tables use, what
     * a frame says to name it, and how a constructor puts a value of it into a local.
     *
     * `field` is a descriptor when a value of this type comes out of a static field of
     * that type, which is the only uniform way to produce a reference type that is not
     * `Object`. A `getstatic` gives the verifier exactly the declared type and nothing
     * narrower, which a `new` or an `ldc` would not.
     */
    record Kind(String name, String what, ClassDesc field) {}

    static final List<Kind> KINDS = List.of(
        new Kind("top", "abstract", null),
        new Kind("int", "primitive", null),
        new Kind("float", "primitive", null),
        new Kind("long", "primitive", null),
        new Kind("double", "primitive", null),
        new Kind("null", "reference", null),
        new Kind("uninitializedThis", "reference", null),
        new Kind("uninitialized(Offset)", "reference", null),
        new Kind("Object", "reference", ConstantDescs.CD_Object),
        new Kind("String", "reference", ConstantDescs.CD_String),
        new Kind("Runnable", "reference", RUNNABLE),
        new Kind("Cloneable", "reference", CLONEABLE),
        new Kind("Serializable", "reference", SERIALIZABLE),
        new Kind("Object[]", "reference", ConstantDescs.CD_Object.arrayType()),
        new Kind("String[]", "reference", ConstantDescs.CD_String.arrayType()),
        new Kind("int[]", "reference", ConstantDescs.CD_int.arrayType()));

    /** What separates the two type names in a grid key, so that `int[]` stays readable. */
    static final String ARROW = "->";

    static Kind kind(String name) {
        for (Kind k : KINDS) {
            if (k.name().equals(name)) {
                return k;
            }
        }
        throw new IllegalArgumentException(name);
    }

    /** The field this class carries so that every reference type has a producer. */
    static String fieldName(Kind k) {
        return "f_" + k.name().replace("[]", "_array");
    }

    // ---------------------------------------------------------------- one cell

    /**
     * A class whose constructor puts a `from` value in local 1 and whose stack map says
     * that local holds a `to`. It loads exactly when the first is assignable to the second.
     *
     * The shape is the same for all 256 cells so that a difference between two cells is a
     * difference between two types and not between two programs. Everything happens before
     * the superclass constructor runs, which is the only place `uninitializedThis` exists,
     * and the `new` parked in local 4 is the only place an `uninitialized(Offset)` can
     * point at.
     */
    static byte[] cell(String name, String from, String to, int major) {
        ClassDesc self = ClassDesc.of(name);
        Kind source = kind(from);
        Kind target = kind(to);
        return ClassFile.of(ClassFile.StackMapsOption.DROP_STACK_MAPS).build(self, cb -> {
            cb.withVersion(major, 0);
            for (Kind k : KINDS) {
                if (k.field() != null) {
                    cb.withField(fieldName(k), k.field(),
                                 ClassFile.ACC_STATIC | ClassFile.ACC_PUBLIC);
                }
            }
            cb.withMethodBody("<init>", VOID, ClassFile.ACC_PUBLIC, code -> {
                Label made = code.newLabel();
                Label join = code.newLabel();

                code.iconst_0().istore(PAD);
                code.labelBinding(made);
                code.new_(ConstantDescs.CD_Object).astore(PARK);

                switch (source.name()) {
                    case "top" -> { }
                    case "int" -> code.iconst_0().istore(SLOT);
                    case "float" -> code.fconst_0().fstore(SLOT);
                    case "long" -> code.lconst_0().lstore(SLOT);
                    case "double" -> code.dconst_0().dstore(SLOT);
                    case "null" -> code.aconst_null().astore(SLOT);
                    case "uninitializedThis" -> code.aload(0).astore(SLOT);
                    case "uninitialized(Offset)" -> code.aload(PARK).astore(SLOT);
                    default -> code.getstatic(self, fieldName(source), source.field())
                                   .astore(SLOT);
                }

                code.goto_(join);
                code.labelBinding(join);
                code.with(StackMapTableAttribute.of(List.of(
                    StackMapFrameInfo.of(join,
                        List.of(SimpleVerificationTypeInfo.UNINITIALIZED_THIS,
                                declared(target, made)),
                        List.of()))));
                code.aload(0).invokespecial(ConstantDescs.CD_Object, "<init>", VOID);
                code.return_();
            });
        });
    }

    /** What a frame writes down to name one verification type. */
    static VerificationTypeInfo declared(Kind k, Label made) {
        return switch (k.name()) {
            case "top" -> SimpleVerificationTypeInfo.TOP;
            case "int" -> SimpleVerificationTypeInfo.INTEGER;
            case "float" -> SimpleVerificationTypeInfo.FLOAT;
            case "long" -> SimpleVerificationTypeInfo.LONG;
            case "double" -> SimpleVerificationTypeInfo.DOUBLE;
            case "null" -> SimpleVerificationTypeInfo.NULL;
            case "uninitializedThis" -> SimpleVerificationTypeInfo.UNINITIALIZED_THIS;
            case "uninitialized(Offset)" -> UninitializedVerificationTypeInfo.of(made);
            default -> ObjectVerificationTypeInfo.of(k.field());
        };
    }

    /**
     * What happened when this JVM was asked to load one of these files.
     *
     * A `VerifyError` is the answer the grid wants: this JVM read the frame, read the code,
     * and refused. Anything else means the file was wrong in some way that has nothing to
     * do with assignability, and a cell like that is reported rather than counted, because
     * a class that failed to load for the wrong reason would otherwise look like a no.
     */
    record Outcome(boolean loaded, String kind, String message, Class<?> type) {}

    /**
     * Define a class and insist that it link.
     *
     * Defining is not verifying. The type checker runs during linking, so a probe that
     * stops at `defineClass` measures the class file parser and calls it the verifier.
     * `ensureInitialized` is the shortest thing that forces linking to happen here.
     */
    static Outcome load(byte[] bytes) {
        try {
            MethodHandles.Lookup lookup = MethodHandles.lookup();
            Class<?> found = lookup.defineClass(bytes);
            lookup.ensureInitialized(found);
            return new Outcome(true, "", "", found);
        } catch (VerifyError e) {
            return new Outcome(false, "VerifyError", oneLine(e.getMessage()), null);
        } catch (Throwable e) {
            return new Outcome(false, e.getClass().getName(), oneLine(e.getMessage()), null);
        }
    }

    static String oneLine(String message) {
        if (message == null) {
            return "";
        }
        return message.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                      .replaceAll(" +", " ").trim();
    }

    /**
     * The clause of a HotSpot verify message that says what was wrong, without the frames,
     * the bytecode or the class name, so that two cells that failed the same way read the
     * same and a cell that failed a new way is visible.
     */
    static String reason(String message) {
        int at = message.indexOf("Reason:");
        if (at < 0) {
            return message.isEmpty() ? "no message" : message;
        }
        String rest = message.substring(at + "Reason:".length()).trim();
        int end = rest.indexOf("Current Frame:");
        return oneLine(end < 0 ? rest : rest.substring(0, end));
    }

    // ------------------------------------------------------------- the grid

    static int names = 0;

    static String next(String stem) {
        return "V" + stem + (names++);
    }

    /**
     * A refusal message with the two type names taken out of it.
     *
     * Every cell that is refused is refused about a different pair of types, so the
     * messages are all different and counting them counts nothing. What is worth counting
     * is the shape: one shape means the type checker has one way of saying no, and a
     * second shape is a cell that failed for a reason this grid is not measuring.
     */
    static String shape(String why) {
        return why.replaceAll("'[^']*'", "<type>")
                  .replaceAll("\\buninitialized \\d+", "<type>")
                  .replaceAll("\\b(integer|float|long|double|null|top|uninitializedThis)\\b",
                              "<type>")
                  .replaceAll("locals\\[\\d+\\]", "locals[N]");
    }

    static void grid(StringBuilder out, int major, Map<String, Boolean> cells) {
        Map<String, Integer> shapes = new TreeMap<>();
        int yes = 0;
        int structural = 0;
        for (Kind from : KINDS) {
            for (Kind to : KINDS) {
                Outcome got = load(cell(next("G"), from.name(), to.name(), major));
                String key = from.name() + ARROW + to.name();
                if (got.loaded()) {
                    yes++;
                    cells.put(key, true);
                    out.append("cell.").append(key).append("\t1\n");
                } else if (got.kind().equals("VerifyError")) {
                    cells.put(key, false);
                    out.append("cell.").append(key).append("\t0\n");
                    shapes.merge(shape(reason(got.message())), 1, Integer::sum);
                } else {
                    structural++;
                    out.append("cell.").append(key).append("\t-1\n");
                    out.append("unmeasurable.").append(key).append("\t")
                       .append(got.kind()).append(": ").append(got.message()).append("\n");
                }
            }
        }
        out.append("grid.types\t").append(KINDS.size()).append("\n");
        out.append("grid.cells\t").append(KINDS.size() * KINDS.size()).append("\n");
        out.append("grid.assignable\t").append(yes).append("\n");
        out.append("grid.unmeasurable\t").append(structural).append("\n");
        out.append("grid.refusal_shapes\t").append(shapes.size()).append("\n");
        for (Map.Entry<String, Integer> e : shapes.entrySet()) {
            out.append("shape.").append(e.getKey()).append("\t").append(e.getValue())
               .append("\n");
        }
    }

    /**
     * What kind of relation the measured grid turns out to be.
     *
     * Everybody who reads about a type hierarchy assumes an order, and an order is
     * reflexive, transitive and antisymmetric. Whether this one is any of those three is
     * a question about the cells and not about the specification, so it is answered here
     * from the cells. The generator answers it again from the same cells, which is the
     * only way a derived number is worth more than the sentence that describes it.
     */
    static void properties(StringBuilder out, Map<String, Boolean> cells) {
        int reflexive = 0;
        for (Kind k : KINDS) {
            if (Boolean.TRUE.equals(cells.get(k.name() + ARROW + k.name()))) {
                reflexive++;
            }
        }
        List<String> broken = new java.util.ArrayList<>();
        for (Kind a : KINDS) {
            for (Kind b : KINDS) {
                if (!Boolean.TRUE.equals(cells.get(a.name() + ARROW + b.name()))) {
                    continue;
                }
                for (Kind c : KINDS) {
                    if (Boolean.TRUE.equals(cells.get(b.name() + ARROW + c.name()))
                        && !Boolean.TRUE.equals(cells.get(a.name() + ARROW + c.name()))) {
                        broken.add(a.name() + ARROW + b.name() + ARROW + c.name());
                    }
                }
            }
        }
        List<String> both = new java.util.ArrayList<>();
        for (int i = 0; i < KINDS.size(); i++) {
            for (int j = i + 1; j < KINDS.size(); j++) {
                String a = KINDS.get(i).name();
                String b = KINDS.get(j).name();
                if (Boolean.TRUE.equals(cells.get(a + ARROW + b))
                    && Boolean.TRUE.equals(cells.get(b + ARROW + a))) {
                    both.add(a + " and " + b);
                }
            }
        }
        out.append("property.reflexive\t").append(reflexive).append("\n");
        out.append("property.nontransitive\t").append(broken.size()).append("\n");
        out.append("property.mutual\t").append(both.size()).append("\n");
        for (String one : broken) {
            out.append("nontransitive.").append(one).append("\t1\n");
        }
        for (String one : both) {
            out.append("mutual.").append(one).append("\t1\n");
        }
    }

    /**
     * Cells whose answer can be worked out on paper, checked against what was measured.
     *
     * The grid is 256 answers from a machine and nothing else in this probe would notice
     * if the harness silently built the same class 256 times. These twelve are the ones a
     * reader of JVMS 4.10.1.2 can settle without running anything, and run.py writes no
     * results file when the measured answer differs from the worked one.
     */
    static void selfCheck(StringBuilder out, Map<String, Boolean> cells) {
        Map<String, Boolean> worked = new LinkedHashMap<>();
        worked.put("int" + ARROW + "int", true);
        worked.put("int" + ARROW + "top", true);
        worked.put("int" + ARROW + "float", false);
        worked.put("long" + ARROW + "int", false);
        worked.put("null" + ARROW + "String", true);
        worked.put("String" + ARROW + "null", false);
        worked.put("String" + ARROW + "Object", true);
        worked.put("Object" + ARROW + "String", false);
        worked.put("String[]" + ARROW + "Object[]", true);
        worked.put("Object[]" + ARROW + "String[]", false);
        worked.put("int[]" + ARROW + "Object[]", false);
        worked.put("int[]" + ARROW + "Object", true);
        int wrong = 0;
        for (Map.Entry<String, Boolean> e : worked.entrySet()) {
            boolean got = Boolean.TRUE.equals(cells.get(e.getKey()));
            out.append("check.").append(e.getKey()).append("\t")
               .append(got == e.getValue() ? 1 : 0).append("\n");
            if (got != e.getValue()) {
                wrong++;
            }
        }
        out.append("check.wrong\t").append(wrong).append("\n");
    }

    // ------------------------------------------------------- the interface hole

    /**
     * A String in a local a frame calls Runnable, and then `invokeinterface run()`.
     *
     * The verifier's own rule says any class widens to any interface, so this file is not
     * malformed and not a trick. It is what the type system permits, written down.
     */
    static byte[] hole(String name, ClassDesc target, boolean call) {
        ClassDesc self = ClassDesc.of(name);
        return ClassFile.of(ClassFile.StackMapsOption.DROP_STACK_MAPS).build(self, cb -> {
            cb.withVersion(ClassFile.latestMajorVersion(), 0);
            cb.withMethodBody(ConstantDescs.INIT_NAME, VOID, ClassFile.ACC_PUBLIC,
                code -> code.aload(0)
                            .invokespecial(ConstantDescs.CD_Object,
                                           ConstantDescs.INIT_NAME, VOID)
                            .return_());
            cb.withMethodBody("go", VOID, ClassFile.ACC_STATIC | ClassFile.ACC_PUBLIC,
                code -> {
                    Label join = code.newLabel();
                    code.iconst_0().istore(PAD);
                    code.ldc("a String, which is not a Runnable").astore(SLOT);
                    code.goto_(join);
                    code.labelBinding(join);
                    code.with(StackMapTableAttribute.of(List.of(
                        StackMapFrameInfo.of(join,
                            List.of(SimpleVerificationTypeInfo.TOP,
                                    ObjectVerificationTypeInfo.of(target)),
                            List.of()))));
                    if (call) {
                        code.aload(SLOT).invokeinterface(target, "run", VOID);
                    }
                    code.return_();
                });
        });
    }

    static void interfaceHole(StringBuilder out) {
        Outcome verified = load(hole(next("H"), RUNNABLE, false));
        out.append("hole.verifies\t").append(verified.loaded() ? 1 : 0).append("\n");
        if (!verified.loaded()) {
            out.append("hole.rejected_by\t").append(verified.kind()).append(": ")
               .append(verified.message()).append("\n");
            return;
        }

        // The same file with the call in it. It has to verify too, or the finding is about
        // a file nobody would write rather than about the type system.
        Outcome withCall = load(hole(next("H"), RUNNABLE, true));
        out.append("hole.call_verifies\t").append(withCall.loaded() ? 1 : 0).append("\n");
        if (!withCall.loaded()) {
            out.append("hole.rejected_by\t").append(withCall.kind()).append(": ")
               .append(withCall.message()).append("\n");
            return;
        }
        out.append("hole.throws\t").append(run(withCall.type())).append("\n");

        // The same String, in a local the frame calls a class rather than an interface.
        // This has to be refused, or the finding is about the probe and not about
        // interfaces. It is a refusal, and the message it is refused with is the evidence.
        Outcome control = load(hole(next("H"), ClassDesc.of("java.lang.Thread"), false));
        out.append("hole.control_verifies\t").append(control.loaded() ? 1 : 0).append("\n");
        out.append("hole.control_rejected_by\t").append(reason(control.message()))
           .append("\n");
    }

    /** Call the built method and report what came back out, by class name and message. */
    static String run(Class<?> built) {
        try {
            built.getMethod("go").invoke(null);
            return "nothing";
        } catch (InvocationTargetException e) {
            Throwable cause = e.getCause();
            return cause.getClass().getName() + ": " + oneLine(cause.getMessage());
        } catch (Throwable e) {
            return e.getClass().getName() + ": " + oneLine(e.getMessage());
        }
    }

    // ------------------------------------------------------------ the failover

    /**
     * The same wrong attribute at five class file versions.
     *
     * A frame that says `int` where the code put a `float` is wrong in a way no reader can
     * argue about. Below 50 the attribute is not read at all, at 50 it is read and its
     * rejection is not fatal, and above 50 it is fatal. A control with a correct frame runs
     * at every version, so a version that refuses the wrong file for some other reason is
     * visible rather than counted as a refusal.
     */
    static byte[] versioned(String name, int major, boolean correct) {
        ClassDesc self = ClassDesc.of(name);
        return ClassFile.of(ClassFile.StackMapsOption.DROP_STACK_MAPS).build(self, cb -> {
            cb.withVersion(major, 0);
            cb.withMethodBody("go", VOID, ClassFile.ACC_STATIC | ClassFile.ACC_PUBLIC,
                code -> {
                    Label join = code.newLabel();
                    code.iconst_0().istore(PAD);
                    code.fconst_0().fstore(SLOT);
                    code.goto_(join);
                    code.labelBinding(join);
                    code.with(StackMapTableAttribute.of(List.of(
                        StackMapFrameInfo.of(join,
                            List.of(SimpleVerificationTypeInfo.TOP,
                                    correct ? SimpleVerificationTypeInfo.FLOAT
                                            : SimpleVerificationTypeInfo.INTEGER),
                            List.of()))));
                    code.return_();
                });
        });
    }

    static void failover(StringBuilder out, int[] majors) {
        for (int major : majors) {
            Outcome wrong = load(versioned(next("W"), major, false));
            Outcome right = load(versioned(next("C"), major, true));
            out.append("failover.").append(major).append(".wrong_loads\t")
               .append(wrong.loaded() ? 1 : 0).append("\n");
            out.append("failover.").append(major).append(".right_loads\t")
               .append(right.loaded() ? 1 : 0).append("\n");
            if (!wrong.loaded()) {
                out.append("failover.").append(major).append(".rejected_by\t")
                   .append(wrong.kind()).append("\n");
            }
            if (!right.loaded()) {
                out.append("failover.").append(major).append(".control_rejected_by\t")
                   .append(right.kind()).append(": ").append(right.message()).append("\n");
            }
        }
    }

    // --------------------------------------------------------------- the census

    /**
     * Every check in java.base that runs because the verifier declined to prove something.
     *
     * `invokeinterface` is the one the type system names: the verifier lets any object into
     * an interface local, so the call has to look. `checkcast` and `aastore` are the other
     * two, one because a cast is a claim the verifier will not check and one because array
     * subtyping is covariant and stores into an array therefore are not sound.
     */
    static void census(StringBuilder out, String module) throws IOException {
        Path root = FileSystems.getFileSystem(java.net.URI.create("jrt:/"))
                               .getPath("modules", module);
        Map<String, Long> counts = new LinkedHashMap<>();
        long classes = 0;
        long methods = 0;
        long withCode = 0;
        long instructions = 0;
        long codeBytes = 0;
        try (Stream<Path> walk = Files.walk(root)) {
            for (Path path : (Iterable<Path>) walk.filter(Files::isRegularFile)
                    .filter(p -> p.toString().endsWith(".class"))::iterator) {
                ClassModel model = ClassFile.of().parse(Files.readAllBytes(path));
                classes++;
                for (MethodModel method : model.methods()) {
                    methods++;
                    CodeModel code = method.code().orElse(null);
                    if (code == null) {
                        continue;
                    }
                    withCode++;
                    if (code instanceof java.lang.classfile.attribute.CodeAttribute body) {
                        codeBytes += body.codeLength();
                    }
                    for (var element : code) {
                        if (!(element instanceof Instruction instruction)) {
                            continue;
                        }
                        instructions++;
                        String name = switch (instruction) {
                            case InvokeInstruction invoke -> invoke.opcode().name();
                            case TypeCheckInstruction check -> check.opcode().name();
                            case FieldInstruction field -> field.opcode().name();
                            default -> instruction.opcode().name();
                        };
                        if (WATCHED.contains(name)) {
                            counts.merge(name, 1L, Long::sum);
                        }
                    }
                }
            }
        }
        out.append("census.module\t").append(module).append("\n");
        out.append("census.classes\t").append(classes).append("\n");
        out.append("census.methods\t").append(methods).append("\n");
        out.append("census.methods_with_code\t").append(withCode).append("\n");
        out.append("census.instructions\t").append(instructions).append("\n");
        out.append("census.code_bytes\t").append(codeBytes).append("\n");
        for (String name : WATCHED) {
            out.append("op.").append(name).append("\t")
               .append(counts.getOrDefault(name, 0L)).append("\n");
        }
    }

    static final List<String> WATCHED = List.of(
        "INVOKEINTERFACE", "INVOKEVIRTUAL", "INVOKESTATIC", "INVOKESPECIAL",
        "INVOKEDYNAMIC", "CHECKCAST", "INSTANCEOF", "AASTORE", "ATHROW", "NEW");

    // ------------------------------------------------------------------- driver

    public static void main(String[] args) throws Exception {
        String module = args.length > 0 ? args[0] : "java.base";
        StringBuilder out = new StringBuilder();

        for (Kind k : KINDS) {
            out.append("type.").append(k.name()).append(".what\t").append(k.what())
               .append("\n");
        }

        Map<String, Boolean> cells = new LinkedHashMap<>();
        grid(out, ClassFile.latestMajorVersion(), cells);
        properties(out, cells);
        selfCheck(out, cells);
        interfaceHole(out);
        failover(out, new int[] {49, 50, 51, 52, ClassFile.latestMajorVersion()});
        census(out, module);

        System.out.print(out);
    }
}
