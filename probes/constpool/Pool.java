/*
 * The Java half of probes/constpool/run.py. Two jobs, both of which need a JVM.
 *
 * The first is to ask the platform what a constant pool entry is. `java.lang.classfile`
 * has an interface per entry kind, and an entry made through it knows its tag, how many
 * slots it occupies, whether `ldc` may load it, and which other entries it points at.
 * Asking is better than reading a table, because a table has to be retyped every time
 * the format grows a kind and the JDK does not.
 *
 * The second is to count. The pool is over half of a class file by entry and nearly
 * three fifths of it is `CONSTANT_Utf8`, and no document says what all those strings
 * are. So this walks a module of the runtime image and counts what every Utf8 entry is
 * used as, what the method handles point at, how many slots are the unusable second half
 * of a long or a double, and how often the same constant appears twice in one pool.
 *
 * The last one is the one worth the trip. Nothing in the format forbids two identical
 * entries, and an implementation that assumes uniqueness is wrong on files the JDK
 * itself ships.
 *
 * Output is one `key<TAB>value` line per fact, which run.py turns into JSON.
 *
 *   java Pool.java [module]      default java.base
 */

import java.lang.classfile.Attribute;
import java.lang.classfile.ClassFile;
import java.lang.classfile.ClassModel;
import java.lang.classfile.FieldModel;
import java.lang.classfile.MethodModel;
import java.lang.classfile.attribute.AnnotationDefaultAttribute;
import java.lang.classfile.attribute.InnerClassesAttribute;
import java.lang.classfile.attribute.LocalVariableTableAttribute;
import java.lang.classfile.attribute.LocalVariableTypeTableAttribute;
import java.lang.classfile.attribute.MethodParametersAttribute;
import java.lang.classfile.attribute.ModuleAttribute;
import java.lang.classfile.attribute.ModuleHashesAttribute;
import java.lang.classfile.attribute.ModuleTargetAttribute;
import java.lang.classfile.attribute.RecordAttribute;
import java.lang.classfile.attribute.RuntimeInvisibleAnnotationsAttribute;
import java.lang.classfile.attribute.RuntimeInvisibleParameterAnnotationsAttribute;
import java.lang.classfile.attribute.RuntimeInvisibleTypeAnnotationsAttribute;
import java.lang.classfile.attribute.RuntimeVisibleAnnotationsAttribute;
import java.lang.classfile.attribute.RuntimeVisibleParameterAnnotationsAttribute;
import java.lang.classfile.attribute.RuntimeVisibleTypeAnnotationsAttribute;
import java.lang.classfile.attribute.SignatureAttribute;
import java.lang.classfile.attribute.SourceFileAttribute;
import java.lang.classfile.constantpool.ClassEntry;
import java.lang.classfile.constantpool.ConstantDynamicEntry;
import java.lang.classfile.constantpool.ConstantPoolBuilder;
import java.lang.classfile.constantpool.DoubleEntry;
import java.lang.classfile.constantpool.DynamicConstantPoolEntry;
import java.lang.classfile.constantpool.FloatEntry;
import java.lang.classfile.constantpool.IntegerEntry;
import java.lang.classfile.constantpool.InvokeDynamicEntry;
import java.lang.classfile.constantpool.LoadableConstantEntry;
import java.lang.classfile.constantpool.LongEntry;
import java.lang.classfile.constantpool.MemberRefEntry;
import java.lang.classfile.constantpool.MethodHandleEntry;
import java.lang.classfile.constantpool.MethodTypeEntry;
import java.lang.classfile.constantpool.ModuleEntry;
import java.lang.classfile.constantpool.NameAndTypeEntry;
import java.lang.classfile.constantpool.PackageEntry;
import java.lang.classfile.constantpool.PoolEntry;
import java.lang.classfile.constantpool.StringEntry;
import java.lang.classfile.constantpool.Utf8Entry;
import java.lang.constant.ClassDesc;
import java.lang.constant.ConstantDescs;
import java.lang.constant.DirectMethodHandleDesc;
import java.lang.constant.DynamicCallSiteDesc;
import java.lang.constant.DynamicConstantDesc;
import java.lang.constant.MethodHandleDesc;
import java.lang.constant.MethodTypeDesc;
import java.lang.reflect.Method;
import java.net.URI;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

public class Pool {

    public static void main(String[] args) throws Exception {
        String module = args.length > 0 ? args[0] : "java.base";
        kinds();
        count(module);
    }

    /*
     * One entry of every kind the platform can make, asked what it is.
     *
     * The entries are built rather than parsed out of a file because two of the
     * seventeen do not occur in the module this probe scans, and a kind that is absent
     * from the corpus is still a kind the format has. Building one is how the table
     * stays complete without anybody typing a row.
     */
    private static void kinds() {
        ConstantPoolBuilder cp = ConstantPoolBuilder.of();
        ClassDesc owner = ClassDesc.of("Owner");
        MethodTypeDesc signature = MethodTypeDesc.of(ConstantDescs.CD_void);
        DirectMethodHandleDesc bootstrap = MethodHandleDesc.ofMethod(
                DirectMethodHandleDesc.Kind.STATIC, owner, "boot",
                MethodTypeDesc.of(ConstantDescs.CD_Object, ConstantDescs.CD_MethodHandles_Lookup,
                        ConstantDescs.CD_String, ConstantDescs.CD_Class));

        List<PoolEntry> made = new ArrayList<>(List.of(
                cp.utf8Entry("text"),
                cp.classEntry(owner),
                cp.stringEntry("text"),
                cp.intEntry(1),
                cp.floatEntry(1f),
                cp.longEntry(1L),
                cp.doubleEntry(1d),
                cp.nameAndTypeEntry("member", ConstantDescs.CD_int),
                cp.fieldRefEntry(owner, "field", ConstantDescs.CD_int),
                cp.methodRefEntry(owner, "method", signature),
                cp.interfaceMethodRefEntry(owner, "method", signature),
                cp.methodTypeEntry(signature),
                cp.methodHandleEntry(MethodHandleDesc.ofMethod(
                        DirectMethodHandleDesc.Kind.STATIC, owner, "method", signature)),
                cp.moduleEntry(cp.utf8Entry("a.module")),
                cp.packageEntry(cp.utf8Entry("a/package")),
                cp.constantDynamicEntry(DynamicConstantDesc.ofNamed(
                        bootstrap, "constant", ConstantDescs.CD_int)),
                cp.invokeDynamicEntry(DynamicCallSiteDesc.of(
                        MethodHandleDesc.ofMethod(DirectMethodHandleDesc.Kind.STATIC, owner,
                                "boot", MethodTypeDesc.of(ConstantDescs.CD_Object,
                                        ConstantDescs.CD_MethodHandles_Lookup,
                                        ConstantDescs.CD_String, ConstantDescs.CD_MethodType)),
                        "site", signature))));

        for (PoolEntry entry : made) {
            Class<?> face = api(entry);
            String key = "kind." + face.getSimpleName();
            say(key + ".tag", Integer.toString(entry.tag()));
            say(key + ".slots", Integer.toString(entry.width()));
            say(key + ".loadable", Boolean.toString(entry instanceof LoadableConstantEntry));
            say(key + ".member_ref", Boolean.toString(entry instanceof MemberRefEntry));
            say(key + ".dynamic", Boolean.toString(entry instanceof DynamicConstantPoolEntry));
            say(key + ".holds", holds(face));
        }
    }

    /*
     * The public interface an entry implements, which is the name the format uses rather
     * than the name of the class in `jdk.internal.classfile.impl` that happens to
     * implement it. `getInterfaces()[0]` is not good enough: an implementation is free to
     * list them in any order, so this walks up to the one that is a `PoolEntry` and is
     * not one of the groupings every member ref or every loadable constant shares.
     */
    private static Class<?> api(PoolEntry entry) {
        Class<?> best = null;
        for (Class<?> face : entry.getClass().getInterfaces()) {
            for (Class<?> candidate : with(face)) {
                if (!PoolEntry.class.isAssignableFrom(candidate) || candidate == PoolEntry.class) {
                    continue;
                }
                if (candidate == LoadableConstantEntry.class || candidate == MemberRefEntry.class
                        || candidate == DynamicConstantPoolEntry.class
                        || candidate.getSimpleName().equals("AnnotationConstantValueEntry")) {
                    continue;
                }
                if (best == null || best.isAssignableFrom(candidate)) {
                    best = candidate;
                }
            }
        }
        return best == null ? entry.getClass() : best;
    }

    private static List<Class<?>> with(Class<?> face) {
        List<Class<?>> all = new ArrayList<>();
        all.add(face);
        all.addAll(Arrays.asList(face.getInterfaces()));
        return all;
    }

    /*
     * What an entry holds, taken from the accessors its own interface declares. A method
     * returning another pool entry is a reference to somewhere else in the pool, and
     * everything else is a value stored in the entry itself.
     */
    private static String holds(Class<?> face) {
        List<String> parts = new ArrayList<>();
        for (Method method : face.getDeclaredMethods()) {
            if (method.getParameterCount() != 0 || method.isSynthetic()) {
                continue;
            }
            Class<?> returns = method.getReturnType();
            String kind = PoolEntry.class.isAssignableFrom(returns) ? "->" : ":";
            parts.add(method.getName() + kind + returns.getSimpleName());
        }
        parts.sort(String::compareTo);
        return String.join(",", parts);
    }

    /* Everything below is the count over a module of the runtime image. */

    private static final Map<String, Integer> tally = new TreeMap<>();

    private static void bump(String key) {
        tally.merge(key, 1, Integer::sum);
    }

    private static void bump(String key, int by) {
        tally.merge(key, by, Integer::sum);
    }

    private static void count(String module) throws Exception {
        Path root = FileSystems.getFileSystem(URI.create("jrt:/")).getPath("/modules/" + module);
        ClassFile parser = ClassFile.of();
        int classes = 0;
        int slots = 0;
        int entries = 0;
        int wide = 0;
        int largest = 0;
        String largestClass = "";
        int classesWithDuplicate = 0;
        int duplicates = 0;
        List<String> repeated = new ArrayList<>();
        List<String> unseen = new ArrayList<>();
        List<Path> files = new ArrayList<>();
        try (var walk = Files.walk(root)) {
            walk.filter(path -> path.toString().endsWith(".class")).forEach(files::add);
        }
        files.sort(Path::compareTo);

        for (Path file : files) {
            ClassModel model = parser.parse(Files.readAllBytes(file));
            classes++;
            int here = model.constantPool().size();
            slots += here;
            if (here > largest) {
                largest = here;
                largestClass = name(file);
            }

            Set<Utf8Entry> used = new HashSet<>();
            Map<String, Integer> seen = new HashMap<>();
            int duplicatedHere = 0;
            for (PoolEntry entry : model.constantPool()) {
                entries++;
                wide += entry.width() - 1;
                bump("tag." + entry.tag());
                inside(entry, used);
                String rendered = render(entry);
                if (rendered != null && seen.merge(rendered, 1, Integer::sum) > 1) {
                    duplicatedHere++;
                    duplicates++;
                    bump("duplicate.tag." + entry.tag());
                    repeated.add(name(file) + "|" + rendered);
                }
            }
            if (duplicatedHere > 0) {
                classesWithDuplicate++;
            }
            structure(model, used);

            for (PoolEntry entry : model.constantPool()) {
                if (entry instanceof Utf8Entry text && !used.contains(text)) {
                    bump("utf8.unseen");
                    unseen.add(name(file) + "|" + text.stringValue());
                }
            }
        }

        say("scan.module", module);
        say("scan.classes", Integer.toString(classes));
        say("scan.entries", Integer.toString(entries));
        say("scan.pool_count_sum", Integer.toString(slots));
        say("scan.second_slots", Integer.toString(wide));
        say("scan.largest_pool", Integer.toString(largest));
        say("scan.largest_pool_class", largestClass);
        say("duplicate.classes", Integer.toString(classesWithDuplicate));
        say("duplicate.entries", Integer.toString(duplicates));
        for (int at = 0; at < repeated.size(); at++) {
            say("repeated." + at, repeated.get(at));
        }
        for (int at = 0; at < unseen.size(); at++) {
            say("unseen." + at, unseen.get(at));
        }
        for (Map.Entry<String, Integer> one : tally.entrySet()) {
            say(one.getKey(), Integer.toString(one.getValue()));
        }
    }

    private static String name(Path file) {
        String text = file.toString();
        int at = text.indexOf('/', "/modules/".length());
        return text.substring(at + 1, text.length() - ".class".length());
    }

    /*
     * What one entry says about the entries it points at, and the Utf8 entries it uses.
     *
     * The roles are the point. A `CONSTANT_Utf8` is the same structure whether it holds a
     * class name, a method descriptor, a string literal or the name of an attribute, and
     * the only way to know which is to ask what points at it.
     */
    private static void inside(PoolEntry entry, Set<Utf8Entry> used) {
        switch (entry) {
            case ClassEntry it -> role("class_name", it.name(), used);
            case StringEntry it -> role("string_value", it.utf8(), used);
            case NameAndTypeEntry it -> {
                role("name_and_type_name", it.name(), used);
                role("name_and_type_descriptor", it.type(), used);
            }
            case MethodTypeEntry it -> role("method_type_descriptor", it.descriptor(), used);
            case ModuleEntry it -> role("module_name", it.name(), used);
            case PackageEntry it -> role("package_name", it.name(), used);
            case MethodHandleEntry it -> bump("handle." + it.kind() + "."
                    + api(it.reference()).getSimpleName());
            case ConstantDynamicEntry it -> bump("dynamic.constant");
            case InvokeDynamicEntry it -> bump("dynamic.call_site");
            default -> { }
        }
    }

    private static void role(String what, Utf8Entry text, Set<Utf8Entry> used) {
        bump("utf8role." + what);
        used.add(text);
    }

    /*
     * The Utf8 entries the file's own structure uses, which is every one that is not
     * reached from another pool entry. A field or a method holds its name and descriptor
     * directly rather than through a `CONSTANT_NameAndType`, every attribute anywhere in
     * the file holds its own name, and the attributes walked below hold more.
     *
     * This is deliberately not every attribute. An attribute this JDK cannot model is
     * skipped, and so are the ones whose payload holds no Utf8. What comes out is a lower
     * bound on how many strings are accounted for, and `utf8.unseen` is what is left,
     * which is a number to explain rather than a number to trust.
     */
    private static void structure(ClassModel model, Set<Utf8Entry> used) {
        for (Attribute<?> attribute : model.attributes()) {
            attribute(attribute, used);
        }
        for (FieldModel field : model.fields()) {
            role("field_name", field.fieldName(), used);
            role("field_descriptor", field.fieldType(), used);
            for (Attribute<?> attribute : field.attributes()) {
                attribute(attribute, used);
            }
        }
        for (MethodModel method : model.methods()) {
            role("method_name", method.methodName(), used);
            role("method_descriptor", method.methodType(), used);
            for (Attribute<?> attribute : method.attributes()) {
                attribute(attribute, used);
                if (attribute instanceof java.lang.classfile.attribute.CodeAttribute code) {
                    for (Attribute<?> inner : code.attributes()) {
                        attribute(inner, used);
                    }
                }
            }
        }
    }

    private static void attribute(Attribute<?> attribute, Set<Utf8Entry> used) {
        role("attribute_name", attribute.attributeName(), used);
        switch (attribute) {
            case SignatureAttribute it -> role("signature", it.signature(), used);
            case SourceFileAttribute it -> role("source_file", it.sourceFile(), used);
            case LocalVariableTableAttribute it -> it.localVariables().forEach(local -> {
                role("local_variable_name", local.name(), used);
                role("local_variable_descriptor", local.type(), used);
            });
            case LocalVariableTypeTableAttribute it -> it.localVariableTypes().forEach(local -> {
                role("local_variable_name", local.name(), used);
                role("local_variable_signature", local.signature(), used);
            });
            case MethodParametersAttribute it -> it.parameters().forEach(parameter ->
                    parameter.name().ifPresent(name -> role("parameter_name", name, used)));
            case InnerClassesAttribute it -> it.classes().forEach(inner ->
                    inner.innerName().ifPresent(name -> role("inner_class_name", name, used)));
            case RecordAttribute it -> it.components().forEach(component -> {
                role("record_component_name", component.name(), used);
                role("record_component_descriptor", component.descriptor(), used);
                component.attributes().forEach(inner -> attribute(inner, used));
            });
            case ModuleAttribute it -> it.moduleVersion()
                    .ifPresent(version -> role("module_version", version, used));
            // Neither of these two is in the specification's list of attributes. They are
            // the JDK's own, they are in the image the JDK ships, and between them they
            // hold the last two strings in java.base that nothing else accounts for.
            case ModuleTargetAttribute it -> role("module_target", it.targetPlatform(), used);
            case ModuleHashesAttribute it -> role("module_hash_algorithm", it.algorithm(), used);
            case RuntimeVisibleAnnotationsAttribute it ->
                    it.annotations().forEach(one -> annotation(one, used));
            case RuntimeInvisibleAnnotationsAttribute it ->
                    it.annotations().forEach(one -> annotation(one, used));
            case RuntimeVisibleTypeAnnotationsAttribute it ->
                    it.annotations().forEach(one -> annotation(one.annotation(), used));
            case RuntimeInvisibleTypeAnnotationsAttribute it ->
                    it.annotations().forEach(one -> annotation(one.annotation(), used));
            case RuntimeVisibleParameterAnnotationsAttribute it ->
                    it.parameterAnnotations().forEach(list ->
                            list.forEach(one -> annotation(one, used)));
            case RuntimeInvisibleParameterAnnotationsAttribute it ->
                    it.parameterAnnotations().forEach(list ->
                            list.forEach(one -> annotation(one, used)));
            case AnnotationDefaultAttribute it -> value(it.defaultValue(), used);
            default -> { }
        }
    }

    private static void annotation(java.lang.classfile.Annotation one, Set<Utf8Entry> used) {
        role("annotation_type", one.className(), used);
        for (java.lang.classfile.AnnotationElement element : one.elements()) {
            role("annotation_element_name", element.name(), used);
            value(element.value(), used);
        }
    }

    private static void value(java.lang.classfile.AnnotationValue value, Set<Utf8Entry> used) {
        switch (value) {
            case java.lang.classfile.AnnotationValue.OfString it ->
                    role("annotation_string", it.constant(), used);
            case java.lang.classfile.AnnotationValue.OfClass it ->
                    role("annotation_class", it.className(), used);
            case java.lang.classfile.AnnotationValue.OfEnum it -> {
                role("annotation_enum_type", it.className(), used);
                role("annotation_enum_name", it.constantName(), used);
            }
            case java.lang.classfile.AnnotationValue.OfAnnotation it ->
                    annotation(it.annotation(), used);
            case java.lang.classfile.AnnotationValue.OfArray it ->
                    it.values().forEach(inner -> value(inner, used));
            default -> { }
        }
    }

    /*
     * What an entry means, as text, so that two entries that mean the same thing render
     * the same way. Two `CONSTANT_Methodref` entries naming the same owner, name and
     * descriptor are the same constant written twice, whatever indices they hold.
     *
     * The two dynamic kinds render as null and are left out of the count, because their
     * meaning includes a bootstrap method whose arguments are pool entries of their own,
     * and comparing those properly is a different probe.
     */
    private static String render(PoolEntry entry) {
        return switch (entry) {
            case Utf8Entry it -> "1|" + it.stringValue();
            case IntegerEntry it -> "3|" + it.intValue();
            case FloatEntry it -> "4|" + Float.floatToRawIntBits(it.floatValue());
            case LongEntry it -> "5|" + it.longValue();
            case DoubleEntry it -> "6|" + Double.doubleToRawLongBits(it.doubleValue());
            case ClassEntry it -> "7|" + it.asInternalName();
            case StringEntry it -> "8|" + it.stringValue();
            case MemberRefEntry it -> it.tag() + "|" + it.owner().asInternalName() + "."
                    + it.name().stringValue() + ":" + it.type().stringValue();
            case NameAndTypeEntry it -> "12|" + it.name().stringValue() + ":"
                    + it.type().stringValue();
            case MethodHandleEntry it -> "15|" + it.kind() + "|" + render(it.reference());
            case MethodTypeEntry it -> "16|" + it.descriptor().stringValue();
            case ModuleEntry it -> "19|" + it.name().stringValue();
            case PackageEntry it -> "20|" + it.name().stringValue();
            default -> null;
        };
    }

    private static void say(String key, String value) {
        System.out.println(key + "\t" + value);
    }
}
