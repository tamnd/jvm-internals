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
    private static String markRoute = "not tried yet";

    private static void openUnsafe() {
        if (getLongMethod != null) return;
        try {
            Class<?> c = Class.forName("jdk.internal.misc.Unsafe");
            unsafe = c.getMethod("getUnsafe").invoke(null);
            getLongMethod = c.getMethod("getLong", Object.class, long.class);
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

    /** A flag with its origin, the way `java -XX:+PrintFlagsFinal` would show it. */
    static void flags(String... names) {
        for (String name : names) {
            String value = flag(name);
            if (value == null) {
                System.out.printf("%-28s %-10s %s%n", name, "-", "this VM has no such flag");
            } else {
                System.out.printf("%-28s %-10s {%s}%n", name, value, flagOrigin(name).toLowerCase());
            }
        }
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
        System.out.printf("java      %s  (%s)%n", v, System.getProperty("java.vm.version"));
        System.out.printf("vm        %s%n", System.getProperty("java.vm.name"));
        System.out.printf("on        %s %s%n",
            System.getProperty("os.name"), System.getProperty("os.arch"));
        System.out.printf("lessons pinned to %s%n", PIN);
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
