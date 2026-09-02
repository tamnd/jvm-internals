import com.sun.management.HotSpotDiagnosticMXBean;
import java.lang.management.ManagementFactory;
import java.lang.module.ModuleFinder;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeMap;
import javax.management.MBeanOperationInfo;
import javax.management.MBeanServer;
import javax.management.ObjectName;

/**
 * The half of the capability probe that has to run inside a JVM to get an answer.
 *
 * Everything here is a question that cannot be answered by looking at a file or by reading
 * a flag dump: whether a diagnostic command is actually registered, whether a module is in
 * the runtime image, whether a class the curriculum wants to use is present and reachable
 * rather than merely named in a JEP. One JVM start answers all of them, which is why they
 * are collected here instead of being one `java -e` each.
 *
 * Output is one `key\tvalue` line per check on stdout, sorted, so the Python side can read
 * it without a JSON parser and a human can read it without anything at all. A check that
 * throws reports the throwable rather than failing the run, because "this is not available
 * here" is the answer for a lot of environments and it is a result and not an error.
 */
public class Capability {

    static final TreeMap<String, String> ANSWERS = new TreeMap<>();

    public static void main(String[] args) {
        vm();
        diagnosticCommands();
        modules();
        classes();
        access();
        flightRecorder();
        attach();
        for (var entry : ANSWERS.entrySet()) {
            System.out.println(entry.getKey() + "\t" + entry.getValue());
        }
    }

    static void put(String key, Object value) {
        ANSWERS.put(key, String.valueOf(value));
    }

    /** Run a check that is allowed to blow up, and record why it did. */
    static void ask(String key, Check check) {
        try {
            put(key, check.run());
        } catch (Throwable t) {
            // Unwrapped, because everything reached by reflection fails as an
            // InvocationTargetException and that name says nothing at all. The class name
            // and not the stack, because the stack is forty lines and the only part
            // anybody reads is which exception it was.
            Throwable cause = t instanceof InvocationTargetException && t.getCause() != null
                ? t.getCause()
                : t;
            put(key, "error:" + cause.getClass().getSimpleName());
        }
    }

    interface Check {
        Object run() throws Throwable;
    }

    static void vm() {
        Runtime.Version version = Runtime.version();
        put("vm.version", version.toString());
        put("vm.feature", version.feature());
        put("vm.name", System.getProperty("java.vm.name"));
        put("vm.vendor", System.getProperty("java.vm.vendor"));
        put("vm.arch", System.getProperty("os.arch"));
        put("vm.os", System.getProperty("os.name"));
        // The highest class file version this JVM will load. A lesson that writes a class
        // file by hand needs this number and not the release number, and they are only
        // equal when the JDK is the one it was pinned to.
        ask("vm.class_file_major", () -> {
            Class<?> classFile = Class.forName("java.lang.classfile.ClassFile");
            return classFile.getMethod("latestMajorVersion").invoke(null);
        });
        // Three VM decisions that change what an object looks like in memory, so every
        // lesson in the object layout family has to know them before it claims a number.
        for (String option : new String[] {
            "UseCompressedOops",
            "UseCompressedClassPointers",
            "UseCompactObjectHeaders",
            "ObjectAlignmentInBytes",
        }) {
            ask("vm." + option, () -> ManagementFactory
                .getPlatformMXBean(HotSpotDiagnosticMXBean.class)
                .getVMOption(option)
                .getValue());
        }
        ask("vm.max_heap_mb", () -> Runtime.getRuntime().maxMemory() / (1024 * 1024));
        ask("vm.processors", () -> Runtime.getRuntime().availableProcessors());
        ask("vm.gc", () -> {
            List<String> names = new ArrayList<>();
            for (var bean : ManagementFactory.getGarbageCollectorMXBeans()) {
                names.add(bean.getName());
            }
            return String.join(",", names);
        });
        // Whether this process is inside a container with a limit on it. A free notebook
        // runtime is, and a lesson that prints "your machine has N cores" is wrong there
        // unless it asks the JVM rather than the kernel.
        ask("vm.container_aware", () -> {
            long fromJvm = Runtime.getRuntime().maxMemory();
            Path limit = Path.of("/sys/fs/cgroup/memory.max");
            if (!Files.isReadable(limit)) {
                return "no cgroup file";
            }
            String text = Files.readString(limit).trim();
            if (text.equals("max")) {
                return "cgroup has no limit";
            }
            long fromCgroup = Long.parseLong(text);
            return fromJvm <= fromCgroup ? "yes" : "no, heap exceeds the cgroup limit";
        });
    }

    static void diagnosticCommands() {
        // What jcmd can do, asked of the MBean rather than by running jcmd, because the
        // MBean is the same registry jcmd talks to and this way there is no second process
        // and no attach permission in the way.
        String[] wanted = {
            "gcClassHistogram",
            "gcClassStats",
            "gcHeapDump",
            "gcHeapInfo",
            "gcRun",
            "vmClassloaderStats",
            "vmClassHierarchy",
            "vmFlags",
            "vmMetaspace",
            "vmNativeMemory",
            "vmSystemProperties",
            "vmUptime",
            "threadPrint",
            "threadDumpToFile",
            "compilerCodelist",
            "compilerCodecache",
            "compilerQueue",
            // The three that exist on Linux and nowhere else, which is the whole of the
            // difference in dcmd.count between the platforms measured so far.
            "compilerPerfmap",
            "systemNativeHeapInfo",
            "systemTrimNativeHeap",
            "jfrStart",
            "jfrDump",
            "jfrCheck",
            "systemDump",
        };
        try {
            MBeanServer server = ManagementFactory.getPlatformMBeanServer();
            ObjectName name = new ObjectName("com.sun.management:type=DiagnosticCommand");
            List<String> present = new ArrayList<>();
            for (MBeanOperationInfo operation : server.getMBeanInfo(name).getOperations()) {
                present.add(operation.getName());
            }
            put("dcmd.count", present.size());
            for (String command : wanted) {
                put("dcmd." + command, present.contains(command));
            }
        } catch (Throwable t) {
            put("dcmd.count", "error:" + t.getClass().getSimpleName());
            for (String command : wanted) {
                put("dcmd." + command, "unknown");
            }
        }
    }

    static void modules() {
        // Whether the runtime image has these in it. A jlink'd image or a stripped
        // container can be missing any of them, and a lesson that opens with jshell on a
        // runtime without jdk.jshell fails in a way the reader cannot diagnose.
        String[] wanted = {
            "jdk.attach",
            "jdk.compiler",
            "jdk.hotspot.agent",
            "jdk.incubator.vector",
            "jdk.jcmd",
            "jdk.jdi",
            "jdk.jdwp.agent",
            "jdk.jfr",
            "jdk.jshell",
            "jdk.management",
            "jdk.unsupported",
            "java.instrument",
            "java.management",
        };
        // ModuleFinder and not ModuleLayer.boot(), which only sees what this run happens
        // to have resolved. jdk.hotspot.agent and jdk.jcmd are in every full JDK and in
        // no application's module graph, so the boot layer answers no to both and the
        // question being asked here is whether the image has them.
        ModuleFinder image = ModuleFinder.ofSystem();
        for (String module : wanted) {
            put("module." + module, image.find(module).isPresent());
        }
    }

    static void classes() {
        // Present and loadable, which is a different question from being specified. Two of
        // these are the ones most likely to move under the project: the class file API left
        // preview in 24, and the Vector API has been incubating since 16.
        String[] wanted = {
            "java.lang.classfile.ClassFile",
            "java.lang.foreign.Linker",
            "java.lang.ScopedValue",
            "java.util.concurrent.StructuredTaskScope",
            "jdk.incubator.vector.IntVector",
            "sun.misc.Unsafe",
            "jdk.internal.misc.Unsafe",
            "com.sun.management.HotSpotDiagnosticMXBean",
            "com.sun.tools.attach.VirtualMachine",
            "sun.jvm.hotspot.HotSpotAgent",
        };
        for (String name : wanted) {
            boolean found;
            try {
                Class.forName(name, false, ClassLoader.getSystemClassLoader());
                found = true;
            } catch (Throwable t) {
                found = false;
            }
            put("api." + name, found);
        }
    }

    static void access() {
        // Reflection into java.base, which several lessons need and which is denied by
        // default since 16. The question is not whether the field exists, it is whether
        // this JVM was started with the --add-opens that makes it reachable.
        ask("access.open_java_lang", () -> {
            Field value = String.class.getDeclaredField("value");
            value.setAccessible(true);
            return true;
        });
        ask("access.unsafe_theUnsafe", () -> {
            Class<?> unsafe = Class.forName("sun.misc.Unsafe");
            Field field = unsafe.getDeclaredField("theUnsafe");
            field.setAccessible(true);
            return field.get(null) != null;
        });
        // The supported route to the same place. If this works and the one above does not,
        // a lesson should be using this one.
        ask("access.hotspot_mxbean", () ->
            ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class) != null);
    }

    static void flightRecorder() {
        // Not whether jdk.jfr is on the module path, which the module check already
        // answered, but whether a recording will actually start here. It will not on a
        // runtime with no writable temp directory, and it is slow to start on a small
        // machine, which is worth knowing before a lesson depends on it.
        ask("jfr.can_record", () -> {
            Class<?> recordingClass = Class.forName("jdk.jfr.Recording");
            Object recording = recordingClass.getConstructor().newInstance();
            long start = System.nanoTime();
            recordingClass.getMethod("start").invoke(recording);
            recordingClass.getMethod("stop").invoke(recording);
            recordingClass.getMethod("close").invoke(recording);
            long millis = (System.nanoTime() - start) / 1_000_000;
            put("jfr.start_millis", millis);
            return true;
        });
    }

    static void attach() {
        // Attaching to a running JVM is how jcmd, jstack and the Serviceability Agent all
        // work. It is also the thing most likely to be refused: Linux wants ptrace to be
        // permitted, macOS wants the tool signed, and a container usually wants neither.
        // Attaching to yourself is the cheapest way to find out which world you are in.
        ask("attach.self", () -> {
            Class<?> vm = Class.forName("com.sun.tools.attach.VirtualMachine");
            String pid = String.valueOf(ProcessHandle.current().pid());
            Method attach = vm.getMethod("attach", String.class);
            Object handle = attach.invoke(null, pid);
            vm.getMethod("detach").invoke(handle);
            return true;
        });
    }
}
