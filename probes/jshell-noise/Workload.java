// The workload both sides of the JShell noise probe run, and the diagnostics that read
// the VM back afterwards. One file, so that the subprocess side can be handed straight
// to `java` with no compile step, and the kernel side can paste the same declarations
// into JShell and get the same work done by the same bytecode.
//
// The diagnostics are invoked in process through the DiagnosticCommand MBean rather
// than from outside with `jcmd`. That matters for fairness. An external `jcmd` has to
// find a pid and attach, which means the two sides would be observed at different
// moments in their lives and through a mechanism that itself loads classes. Asking the
// VM about itself, from inside, at a point the workload chose, is the same measurement
// on both sides.

import com.sun.management.HotSpotDiagnosticMXBean;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.management.MBeanServer;
import javax.management.ObjectName;

public class Workload {

    // Deliberately modest. The question is how much the runtime brought with it, not
    // how much this loop can allocate, so the work has to be small enough that any
    // difference between the two sides is the substrate rather than the workload.
    static final int WIDGETS = 50_000;
    static final int SPINS = 2_000_000;

    // Held in a static so the objects are live at the moment the heap is read. An
    // unreferenced array would make the histogram a measurement of when the last
    // collection happened to run.
    static Widget[] kept;
    static long sink;

    public static void main(String[] args) throws Exception {
        run();
        report(args.length > 0 ? args[0] : ".");
    }

    static void run() {
        kept = new Widget[WIDGETS];
        for (int i = 0; i < WIDGETS; i++) {
            kept[i] = new Widget(i);
        }
        long total = 0;
        for (int i = 0; i < SPINS; i++) {
            total += hot(i);
        }
        sink = total;
    }

    // Small, hot and pure, so it gets through the tiers quickly and predictably. What
    // it computes does not matter, only that the compiler notices it.
    static long hot(int i) {
        return (i * 31L) ^ (i >>> 3);
    }

    static class Widget {
        final int id;

        Widget(int id) {
            this.id = id;
        }
    }

    static String diagnostic(String command, String... args) {
        try {
            MBeanServer server = ManagementFactory.getPlatformMBeanServer();
            ObjectName name = new ObjectName("com.sun.management:type=DiagnosticCommand");
            return (String) server.invoke(
                name, command, new Object[] {args}, new String[] {String[].class.getName()});
        } catch (Exception e) {
            // Reported rather than thrown. A VM that refuses one diagnostic should still
            // give up the other five, and a missing number is more useful in the results
            // file than a run that died on the way to it.
            return "UNAVAILABLE " + command + ": " + e;
        }
    }

    static void report(String dir) throws IOException {
        Path out = Path.of(dir);
        Files.createDirectories(out);
        Files.writeString(out.resolve("histogram.txt"), diagnostic("gcClassHistogram"));
        Files.writeString(out.resolve("loaderstats.txt"), diagnostic("vmClassloaderStats"));

        // The research document asked for GC.class_stats as well. It is not here on
        // jdk-27+35: the dcmd is gone and jcmd answers "Unknown diagnostic command" for
        // it, so there is nothing to call. VM.classloader_stats above covers the part of
        // it this probe needed, which is how much metadata the substrate is carrying.
        Files.writeString(
            out.resolve("classstats.txt"),
            "GC.class_stats does not exist on this JDK, see the report\n");

        // The heap dump goes through HotSpotDiagnosticMXBean rather than the
        // DiagnosticCommand MBean, because GC.heap_dump is reachable from jcmd but is
        // not published as an MBean operation. This is the supported in process API for
        // it and it takes the same live flag.
        Path dump = out.resolve("heap.hprof");
        Files.deleteIfExists(dump);
        HotSpotDiagnosticMXBean bean =
            ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class);
        bean.dumpHeap(dump.toString(), true);

        System.out.println("WORKLOAD DONE sink=" + sink + " widgets=" + kept.length);
    }
}
