// jvx is the small helper surface every lesson gets for free. It is deliberately thin.
// Anything it does that a reader could do themselves in three lines, it does in a way
// they can read, and it never hides the tool underneath it. When a lesson wants JOL or
// jcmd or jfr, the lesson calls JOL or jcmd or jfr, because watching the real tool is
// the point and a wrapper would be one more thing to trust.
//
// The class name is lower case. That is not a mistake and not Java style. It is a
// namespace that reads like one at a call site, `jvx.mark(o)`, and every lesson has it.

class jvx {

    static final String PIN = "@jvx:pin@";
    static final String BUILT_FROM = "@jvx:sources@";

    // -- reading raw object memory ------------------------------------------------
    //
    // There is no supported API for reading the bytes of an object header. That is not
    // an oversight, it is the whole reason the mark word is an implementation detail:
    // the JVMS does not mandate any internal structure for objects at all (JVMS 2.7),
    // so there is nothing for an API to promise. Two internal doors are open on JDK 27
    // and jvx tries them in this order.
    //
    //   1. jdk.internal.misc.Unsafe, which needs
    //      --add-exports java.base/jdk.internal.misc=ALL-UNNAMED on the command line.
    //      Preferred, because it prints nothing.
    //   2. sun.misc.Unsafe, which needs no flags and prints four lines of terminal
    //      deprecation warning the first time. It still works on JDK 27 and it is on
    //      its way out.
    //
    // Which door opened is not hidden. jvx.markRoute() says, and the banner prints it,
    // because "where did this number come from" is a question a reader is entitled to
    // ask about a number that came from reading memory directly.

    private static Object unsafe;
    private static Method getLongMethod;
    private static Method fieldOffsetMethod;
    private static Method arrayBaseMethod;
    private static boolean fieldOffsetTakesAField;
    private static String markRoute = "not tried yet";

    private static void openUnsafe() {
        if (getLongMethod != null) return;
        try {
            Class<?> c = Class.forName("jdk.internal.misc.Unsafe");
            unsafe = c.getMethod("getUnsafe").invoke(null);
            getLongMethod = c.getMethod("getLong", Object.class, long.class);
            // This one names the field with a string. The older door wants a
            // reflected Field object instead, which is why the two are not
            // interchangeable and why fieldOffset below has to know which it got.
            fieldOffsetMethod = c.getMethod("objectFieldOffset", Class.class, String.class);
            arrayBaseMethod = c.getMethod("arrayBaseOffset", Class.class);
            fieldOffsetTakesAField = false;
            markRoute = "jdk.internal.misc.Unsafe";
            return;
        } catch (Throwable ignored) {
            // Not exported to us. Fall through to the older door.
        }
        try {
            Class<?> c = Class.forName("sun.misc.Unsafe");
            Field f = c.getDeclaredField("theUnsafe");
            f.setAccessible(true);
            unsafe = f.get(null);
            getLongMethod = c.getMethod("getLong", Object.class, long.class);
            fieldOffsetMethod = c.getMethod("objectFieldOffset", Field.class);
            arrayBaseMethod = c.getMethod("arrayBaseOffset", Class.class);
            fieldOffsetTakesAField = true;
            markRoute = "sun.misc.Unsafe (deprecated for removal, expect a warning)";
            return;
        } catch (Throwable t) {
            markRoute = "neither door opened: " + t;
            throw new UnsupportedOperationException(
                "cannot read object memory on this JVM. Start the kernel with "
                + "--add-exports java.base/jdk.internal.misc=ALL-UNNAMED. " + markRoute);
        }
    }

    static String markRoute() {
        openUnsafe();
        return markRoute;
    }

    /** The eight bytes at offset 0 of an object, exactly as they sit in memory. */
    static long mark(Object o) {
        openUnsafe();
        try {
            return (Long) getLongMethod.invoke(unsafe, o, 0L);
        } catch (Exception e) {
            throw new RuntimeException("reading the mark word failed", e);
        }
    }

    /** The mark word, printed with its fields separated out. */
    static void header(Object o) {
        System.out.print(MarkWord.decode(mark(o)));
    }

    static String hex(long word) {
        return MarkWord.hex(word);
    }

    static long field(Object o, String name) {
        return MarkWord.get(mark(o), name);
    }

    /**
     * The mark word of an object nothing has ever touched.
     *
     * This exists because of a trap that is very easy to fall into and impossible to
     * see. JShell echoes the value of any expression you do not end with a semicolon,
     * and echoing an object calls toString(), and Object.toString() calls hashCode(),
     * and calling hashCode() on an object is what makes HotSpot write an identity hash
     * into its mark word. So this:
     *
     *     Object o = new Object()
     *
     * hands you an object whose hash field is already filled in, and the "before"
     * measurement you were about to take is gone. The same line with a semicolon on
     * the end does not, because JShell prints nothing.
     *
     * Nothing here can touch the object between allocating it and reading it, because
     * the reference never leaves this method. It is the honest "before".
     */
    static long freshMark() {
        return mark(new Object());
    }

    /** Where every bit position jvx believes in came from. */
    static void provenance() {
        System.out.print(MarkWord.provenance());
    }

    // -- measuring layout ------------------------------------------------------------
    //
    // There is a library for this, JOL, and it is a good one. These four methods are
    // here anyway, because reaching for a download is the difference between a lesson
    // a reader can start in thirty seconds and one they cannot, and because the whole
    // trick fits in a sentence: the header is the thing your first field comes after,
    // so the offset of the first field is the size of the header. Nothing is being
    // hidden here. Read the four methods and you have the technique.

    /** The byte offset of one field within an instance. */
    static long fieldOffset(Class<?> owner, String name) {
        openUnsafe();
        try {
            if (fieldOffsetTakesAField) {
                Field f = owner.getDeclaredField(name);
                return (Long) fieldOffsetMethod.invoke(unsafe, f);
            }
            return (Long) fieldOffsetMethod.invoke(unsafe, owner, name);
        } catch (NoSuchFieldException e) {
            throw new IllegalArgumentException(owner.getName() + " has no field called " + name);
        } catch (Exception e) {
            throw new RuntimeException("could not read the offset of " + name, e);
        }
    }

    /**
     * Where the header stops, in bytes, for instances of this class.
     *
     * This is the offset of the earliest field, which is the same thing. A class with
     * no fields at all has no first field to point at, so it has to say so rather than
     * return a number it does not know.
     */
    static long headerSize(Class<?> type) {
        long earliest = Long.MAX_VALUE;
        for (Class<?> c = type; c != null; c = c.getSuperclass()) {
            for (Field f : c.getDeclaredFields()) {
                if (!Modifier.isStatic(f.getModifiers())) {
                    earliest = Math.min(earliest, fieldOffset(c, f.getName()));
                }
            }
        }
        if (earliest == Long.MAX_VALUE) {
            throw new IllegalArgumentException(
                type.getName() + " has no instance fields, so there is no first field offset "
                + "to measure the header with. Add one field and measure that class instead.");
        }
        return earliest;
    }

    /** Where an array's elements start, which is the size of an array header. */
    static long arrayBase(Class<?> arrayType) {
        openUnsafe();
        try {
            return ((Number) arrayBaseMethod.invoke(unsafe, arrayType)).longValue();
        } catch (Exception e) {
            throw new RuntimeException("could not read the array base offset", e);
        }
    }

    /**
     * Wrap a class declaration in a program that measures it, ready for jvx.run.
     *
     * The declaration has to be called Candidate. The launcher class has to come first
     * in the file and has to match the file name, which is how the single file source
     * launcher decides what to run, so the reader's class cannot be the first one.
     */
    static String sizeProbe(String candidateSource) {
        return """
            import jdk.internal.misc.Unsafe;
            import java.lang.reflect.Field;
            import java.lang.reflect.Modifier;

            public class Answer {
                public static void main(String[] args) {
                    Unsafe u = Unsafe.getUnsafe();
                    long first = Long.MAX_VALUE;
                    long end = 0;
                    for (Class<?> c = Candidate.class; c != null; c = c.getSuperclass()) {
                        for (Field f : c.getDeclaredFields()) {
                            if (Modifier.isStatic(f.getModifiers())) continue;
                            long off = u.objectFieldOffset(c, f.getName());
                            first = Math.min(first, off);
                            end = Math.max(end, off + width(u, f.getType()));
                        }
                    }
                    if (first == Long.MAX_VALUE) {
                        System.out.println("Candidate has no instance fields, so give it one");
                        return;
                    }
                    long size = (end + 7) / 8 * 8;
                    System.out.printf("header stops at %d, fields end at %d, object is %d bytes%n",
                        first, end, size);
                }

                static int width(Unsafe u, Class<?> t) {
                    if (t == long.class || t == double.class) return 8;
                    if (t == int.class || t == float.class) return 4;
                    if (t == short.class || t == char.class) return 2;
                    if (t == byte.class || t == boolean.class) return 1;
                    // A reference is as wide as one slot of an Object[], which is where
                    // compressed oops show up as 4 rather than 8.
                    return u.arrayIndexScale(Object[].class);
                }
            }

            """ + candidateSource + "\n";
    }

    // -- asking the VM about itself -----------------------------------------------
    //
    // This part needs no internal access at all. HotSpotDiagnosticMXBean is supported
    // API in the jdk.management module and it answers for any flag the VM has,
    // including the origin, which is the part people forget to check. A flag that is
    // true because it is the default and a flag that is true because somebody put it
    // on the command line are different facts, and a lesson that confuses them is
    // teaching a local accident as a general truth.

    private static HotSpotDiagnosticMXBean diagnostic() {
        return ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class);
    }

    /** A flag's value, or null when this VM has no such flag. */
    static String flag(String name) {
        try {
            return diagnostic().getVMOption(name).getValue();
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    /** Where a flag's value came from: default, command line, ergonomic, and so on. */
    static String flagOrigin(String name) {
        try {
            return diagnostic().getVMOption(name).getOrigin().toString();
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    static boolean on(String name) {
        return "true".equals(flag(name));
    }

    /**
     * A flag with its origin, the way `java -XX:+PrintFlagsFinal` would show it.
     *
     * Formatted into a string and printed once, rather than printf, and that is not a
     * style choice. The kernel turns every write on System.out into its own stream
     * message, and java.util.Formatter writes each padding space separately, so a
     * printf with a %-28s in it arrives at the reader as thirty little pieces and the
     * notebook renders each one on its own line. One string, one println, one line.
     */
    static void flags(String... names) {
        for (String name : names) {
            String value = flag(name);
            if (value == null) {
                System.out.println(String.format(
                    "%-28s %-10s %s", name, "-", "this VM has no such flag"));
            } else {
                System.out.println(String.format(
                    "%-28s %-10s {%s}", name, value, flagOrigin(name).toLowerCase()));
            }
        }
    }

    // -- running something in a different JVM -------------------------------------

    /**
     * Compile and run a single Java file in a fresh JVM, with the flags you give it,
     * and hand back everything it printed.
     *
     * Two quite different jobs need this and it is worth being clear about both.
     *
     * The first is that a lot of what this project teaches is only visible by
     * comparison, and the two things being compared are two JVMs started differently.
     * You cannot turn UseCompactObjectHeaders off in a running VM. The objects are
     * already laid out.
     *
     * The second is that the kernel you are typing into is a JShell, and JShell
     * changes some of what a lesson wants to observe. It wraps every snippet in a
     * synthetic class, which shows up in class histograms and compilation logs, and it
     * touches the objects you assign to variables. A subprocess has none of that,
     * because it is a plain JVM running a plain program.
     *
     * No javac step and no classpath, because a single .java file passed to `java` is
     * compiled in memory and run. That has been standard since JEP 330 in Java 11, and
     * it is why the source in a lesson cell is the whole program rather than the
     * interesting half of one.
     */
    static String run(String className, String source, String... vmArgs) {
        try {
            Path dir = Files.createTempDirectory("jvx");
            Path file = dir.resolve(className + ".java");
            Files.writeString(file, source);

            List<String> command = new ArrayList<>();
            command.add(Path.of(System.getProperty("java.home"), "bin", "java").toString());
            for (String arg : vmArgs) command.add(arg);
            command.add(file.toString());

            // Merged, and on purpose. A VM that refuses a flag says so on stderr, and a
            // reader who gets silence and no output has been told nothing at all.
            Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
            String out = new String(p.getInputStream().readAllBytes());
            p.waitFor();

            Files.deleteIfExists(file);
            Files.deleteIfExists(dir);
            return out;
        } catch (Exception e) {
            throw new RuntimeException("could not run " + className + " in a fresh JVM", e);
        }
    }

    /** The same thing, printed rather than returned, which is what a cell usually wants. */
    static void show(String className, String source, String... vmArgs) {
        System.out.print(run(className, source, vmArgs));
    }

    // -- prediction gates -----------------------------------------------------------
    //
    // Three calls, forwarded to Gate. A lesson never names Gate, so the day the text
    // version is replaced by a widget, no lesson changes.

    /** Ask a question and stop. Nothing here shows the answer. */
    static void gate(String id, String question, String... options) {
        Gate.ask(id, question, options);
    }

    /** Write your answer down. It is not marked yet. */
    static void answer(String id, String choice) {
        Gate.answer(id, choice);
    }

    /** Mark it. Run this after you have measured, not before. */
    static void reveal(String id, String correct) {
        Gate.reveal(id, correct);
    }

    // -- what am I running on -----------------------------------------------------

    /**
     * Printed by the bootstrap cell of every lesson. It is not decoration. Almost every
     * observation in this project is true of one configuration and false of another, so
     * a reader comparing their output with the page needs to see, on the same screen,
     * which configuration produced theirs.
     */
    static void banner() {
        Runtime.Version v = Runtime.version();
        System.out.println(String.format("java      %s  (%s)",
            v, System.getProperty("java.vm.version")));
        System.out.println(String.format("vm        %s", System.getProperty("java.vm.name")));
        System.out.println(String.format("on        %s %s",
            System.getProperty("os.name"), System.getProperty("os.arch")));
        System.out.println(String.format("lessons pinned to %s", PIN));
        System.out.println();
        // Three flags, not four. UseCompressedClassPointers used to belong in this
        // list and no longer exists on JDK 27, which is exactly why flag() returns
        // null for a missing flag rather than throwing: a banner that dies because
        // the VM moved on is a banner that stops anyone from reading anything.
        flags("UseCompactObjectHeaders", "UseCompressedOops", "ObjectAlignmentInBytes");
        System.out.println();
        System.out.println("mark word read through " + markRoute());
        if (!v.toString().startsWith(PIN.replace("jdk-", "").split("\\+")[0])) {
            System.out.println();
            System.out.println("NOTE: this VM is not the pinned one. Numbers below may differ from the page,");
            System.out.println("      and where they do, this VM is right about this VM and the page is right");
            System.out.println("      about " + PIN + ".");
        }
    }
}
