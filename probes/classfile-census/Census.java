/*
 * The Java half of probes/classfile-census/run.py. Two jobs, both of which need a JVM.
 *
 * The first is to ask the platform what a class file is. `java.lang.reflect.AccessFlag`
 * knows every access flag, its mask, and which parts of a class file it is allowed to
 * appear in, and it knows that per class file version rather than once. That last part
 * is the interesting one: a flag whose meaning depends on the version it is read at
 * cannot be printed as a single row in a table without lying. `java.lang.classfile`
 * knows which attributes it can model and how each one behaves when it is transformed.
 *
 * The second is to count. The specification lists 17 constant pool tags and 30 attributes
 * and gives no hint that some of them are in every class file ever compiled and others
 * are in about four. So this walks a module of the runtime image and counts constant pool
 * entries by tag, attributes by name and by where they appeared, and access flags by bit
 * and by where they appeared. It also records the class file version of every class,
 * because a module built for one release is a fact worth checking rather than assuming.
 *
 * Output is one `key<TAB>value` line per fact, which run.py turns into JSON.
 *
 *   java Census.java [module]      default java.base
 */

import java.lang.classfile.Attribute;
import java.lang.classfile.AttributeMapper;
import java.lang.classfile.Attributes;
import java.lang.classfile.ClassFile;
import java.lang.classfile.ClassModel;
import java.lang.classfile.CodeModel;
import java.lang.classfile.attribute.UnknownAttribute;
import java.lang.classfile.constantpool.PoolEntry;
import java.lang.reflect.AccessFlag;
import java.lang.reflect.ClassFileFormatVersion;
import java.lang.reflect.Method;
import java.net.URI;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Collectors;

public class Census {

    public static void main(String[] args) throws Exception {
        String module = args.length > 0 ? args[0] : "java.base";
        api();
        count(module);
    }

    /*
     * What this JDK believes a class file may contain, as opposed to what it does contain.
     *
     * The per version locations are here because `AccessFlag` is the only source in the
     * project that carries them. The specification says in prose that ACC_STRICT was
     * meaningful for methods in class file versions 46 through 60, and this is the same
     * fact as data, which is what a generated table needs.
     */
    private static void api() throws Exception {
        say("api.latest_major", Integer.toString(ClassFile.latestMajorVersion()));
        say("api.latest_minor", Integer.toString(ClassFile.latestMinorVersion()));

        // Keyed by release rather than by major version, because two releases share
        // major 45 and differ only in the minor, and a key that collides would quietly
        // drop one of the two answers.
        for (ClassFileFormatVersion version : ClassFileFormatVersion.values()) {
            say("release." + version.name(), Integer.toString(version.major()));
        }

        for (AccessFlag flag : AccessFlag.values()) {
            say("flag." + flag.name() + ".mask", Integer.toString(flag.mask()));
            say("flag." + flag.name() + ".source_modifier",
                    Boolean.toString(flag.sourceModifier()));
            say("flag." + flag.name() + ".locations", where(flag.locations()));
            for (ClassFileFormatVersion version : ClassFileFormatVersion.values()) {
                say("flagat." + flag.name() + "." + version.name(),
                        where(flag.locations(version)));
            }
        }

        // Reflection rather than a written list, because the point of asking the JDK is
        // to get the answer this JDK has rather than the one somebody typed last year.
        for (Method method : Attributes.class.getDeclaredMethods()) {
            if (method.getParameterCount() != 0
                    || !AttributeMapper.class.isAssignableFrom(method.getReturnType())) {
                continue;
            }
            AttributeMapper<?> mapper = (AttributeMapper<?>) method.invoke(null);
            say("attribute." + mapper.name() + ".stability", mapper.stability().name());
            say("attribute." + mapper.name() + ".allow_multiple",
                    Boolean.toString(mapper.allowMultiple()));
        }
    }

    private static String where(java.util.Set<AccessFlag.Location> locations) {
        return locations.stream().map(Enum::name).sorted().collect(Collectors.joining(","));
    }

    /*
     * Walk one module of the runtime image and count what is in it.
     *
     * Same image and the same refusal to swallow a parse failure as probes/opcodes. A
     * class inside the JDK that the JDK's own class file API cannot read is the most
     * interesting line this could print, so it is printed.
     */
    private static void count(String module) throws Exception {
        var image = FileSystems.getFileSystem(URI.create("jrt:/"));
        Path root = image.getPath("/modules/" + module);
        if (!Files.isDirectory(root)) {
            throw new IllegalArgumentException("no module " + module + " in this image");
        }

        Map<String, Long> versions = new TreeMap<>();
        Map<Integer, Long> tags = new TreeMap<>();
        Map<String, Long> attributes = new TreeMap<>();
        Map<String, Long> unknown = new TreeMap<>();
        Map<String, Long> flags = new TreeMap<>();
        // A bit set where this JDK's own AccessFlag says no flag lives. JVMS 4.1 says
        // the unassigned bits should be zero in a generated class file, and whether the
        // JDK's own module obeys that is a question with an answer rather than a guess.
        Map<String, Long> stray = new TreeMap<>();
        Map<String, List<String>> strayExamples = new TreeMap<>();
        Map<String, List<String>> versionExamples = new HashMap<>();
        List<String> unreadable = new ArrayList<>();

        Map<String, Integer> allowed = new HashMap<>();
        for (AccessFlag flag : AccessFlag.values()) {
            for (AccessFlag.Location location : flag.locations()) {
                allowed.merge(location.name(), flag.mask(), (a, b) -> a | b);
            }
        }

        long classes = 0, fields = 0, methods = 0, bodies = 0, components = 0;
        long entries = 0;
        int largestPool = 0;
        String largestPoolClass = "";

        ClassFile parser = ClassFile.of();
        List<Path> files;
        try (var walk = Files.walk(root)) {
            files = walk.filter(p -> p.toString().endsWith(".class")).sorted().toList();
        }

        for (Path file : files) {
            classes++;
            try {
                ClassModel model = parser.parse(Files.readAllBytes(file));
                String who = model.thisClass().asInternalName();
                String version = model.majorVersion() + "." + model.minorVersion();
                bump(versions, version);
                example(versionExamples, version, who);

                var pool = model.constantPool();
                if (pool.size() > largestPool) {
                    largestPool = pool.size();
                    largestPoolClass = who;
                }
                int index = 1;
                while (index < pool.size()) {
                    PoolEntry entry = pool.entryByIndex(index);
                    tags.merge(entry.tag(), 1L, Long::sum);
                    entries++;
                    // A CONSTANT_Long or CONSTANT_Double takes two slots and the second
                    // one is unusable, which is the oldest wart in the format and the
                    // reason this loop steps by width rather than by one.
                    index += entry.width();
                }

                var seen = new Counters(attributes, unknown, flags, stray, strayExamples,
                        allowed, who);
                seen.record("CLASS", model.attributes(), model.flags().flagsMask());
                for (var field : model.fields()) {
                    fields++;
                    seen.record("FIELD", field.attributes(), field.flags().flagsMask());
                }
                for (var method : model.methods()) {
                    methods++;
                    seen.record("METHOD", method.attributes(),
                            method.flags().flagsMask());
                    var code = method.code();
                    if (code.isPresent() && code.get() instanceof CodeModel body) {
                        bodies++;
                        seen.record("CODE", body.attributes(), -1);
                    }
                }
                for (var component : model.findAttribute(Attributes.record())
                        .map(a -> a.components()).orElse(List.of())) {
                    components++;
                    seen.record("RECORD_COMPONENT", component.attributes(), -1);
                }
            } catch (Exception problem) {
                unreadable.add(file + ": " + problem.getClass().getSimpleName());
            }
        }

        say("scan.module", module);
        say("scan.classes", Long.toString(classes));
        say("scan.fields", Long.toString(fields));
        say("scan.methods", Long.toString(methods));
        say("scan.method_bodies", Long.toString(bodies));
        say("scan.record_components", Long.toString(components));
        say("scan.pool_entries", Long.toString(entries));
        say("scan.largest_pool", Integer.toString(largestPool));
        say("scan.largest_pool_class", largestPoolClass);
        say("scan.unreadable", Integer.toString(unreadable.size()));
        for (String line : unreadable) {
            say("unreadable", line);
        }
        for (var entry : versions.entrySet()) {
            say("version." + entry.getKey(), Long.toString(entry.getValue()));
            say("versionexample." + entry.getKey(),
                    String.join(",", versionExamples.get(entry.getKey())));
        }
        for (var entry : tags.entrySet()) {
            say("tag." + entry.getKey(), Long.toString(entry.getValue()));
        }
        for (var entry : attributes.entrySet()) {
            say("attr." + entry.getKey(), Long.toString(entry.getValue()));
        }
        for (var entry : unknown.entrySet()) {
            say("unknown." + entry.getKey(), Long.toString(entry.getValue()));
        }
        for (var entry : flags.entrySet()) {
            say("flagseen." + entry.getKey(), Long.toString(entry.getValue()));
        }
        for (var entry : stray.entrySet()) {
            say("stray." + entry.getKey(), Long.toString(entry.getValue()));
            say("strayexample." + entry.getKey(),
                    String.join(",", strayExamples.get(entry.getKey())));
        }
    }

    /* One class's worth of counting, so the tallies do not have to be threaded through
     * a method signature nobody can read. */
    private record Counters(Map<String, Long> attributes, Map<String, Long> unknown,
                            Map<String, Long> flags, Map<String, Long> stray,
                            Map<String, List<String>> strayExamples,
                            Map<String, Integer> allowed, String who) {

        /* One element's attributes and, when it has them, its access flags. */
        void record(String location, List<Attribute<?>> found, int mask) {
            for (Attribute<?> attribute : found) {
                String name = attribute.attributeName().stringValue();
                bump(attributes, location + "." + name);
                if (attribute instanceof UnknownAttribute) {
                    bump(unknown, name);
                }
            }
            if (mask < 0) {
                return;
            }
            int known = allowed.getOrDefault(location, 0);
            for (int bit = 0; bit < 16; bit++) {
                int value = 1 << bit;
                if ((mask & value) == 0) {
                    continue;
                }
                bump(flags, location + "." + value);
                if ((known & value) == 0) {
                    bump(stray, location + "." + value);
                    example(strayExamples, location + "." + value, who);
                }
            }
        }
    }

    private static <K> void bump(Map<K, Long> counter, K key) {
        counter.merge(key, 1L, Long::sum);
    }

    /* Up to five names per key. Enough for a reader to go and look, and few enough that
     * a results file stays a results file rather than becoming a listing. */
    private static void example(Map<String, List<String>> examples, String key,
                                String name) {
        var found = examples.computeIfAbsent(key, k -> new ArrayList<>());
        if (found.size() < 5 && !found.contains(name)) {
            found.add(name);
        }
    }

    private static void say(String key, String value) {
        System.out.println(key + "\t" + value.replace('\t', ' ').replace('\n', ' '));
    }
}
