import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Iterator;
import java.util.List;

import sun.jvm.hotspot.HotSpotAgent;
import sun.jvm.hotspot.runtime.VM;
import sun.jvm.hotspot.types.Field;
import sun.jvm.hotspot.types.Type;
import sun.jvm.hotspot.types.TypeDataBase;

/**
 * Print HotSpot's own struct layouts, as the Serviceability Agent resolved them.
 *
 * Issue #3. This part has to be Java, because the type database is a Java API and no
 * command line tool prints it as data. `jhsdb clhsdb` prints the same facts to a terminal
 * one type at a time, which is what the probe compares this against, and which is exactly
 * the shape a generator cannot consume.
 *
 * Nothing here interprets what it finds. A field `vmStructs` does not export is not here
 * and this file does not guess at it: the reason for going through the SA rather than
 * parsing `vmStructs.cpp` is that this is the resolved, per configuration answer rather
 * than a reading of the source.
 *
 * Three ways in, because the interesting question is not what the type database says but
 * who is allowed to read it:
 *
 *   --pid 1234                     attach to something already running
 *   --exe <java> --core <file>     open a core file, no attach and no permission needed
 *   --spawn <java> [args...]       start a JVM and attach to it, so the target is a
 *                                  child of this process rather than a stranger
 *
 * The last one is there because Linux's ptrace_scope 1 allows tracing a descendant. A
 * tool that launches the VM it reads is therefore in a different position from a tool
 * that attaches to one somebody else started, and the difference decides whether `bpc`
 * needs root.
 *
 *   java --add-modules jdk.hotspot.agent \
 *        --add-exports jdk.hotspot.agent/sun.jvm.hotspot=ALL-UNNAMED \
 *        --add-exports jdk.hotspot.agent/sun.jvm.hotspot.runtime=ALL-UNNAMED \
 *        --add-exports jdk.hotspot.agent/sun.jvm.hotspot.types=ALL-UNNAMED \
 *        TypeDump.java --types oopDesc,Klass --pid 1234
 */
public class TypeDump {

    public static void main(String[] args) throws Exception {
        List<String> types = new ArrayList<>();
        List<String> spawn = new ArrayList<>();
        String pid = null;
        String exe = null;
        String core = null;
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--types" -> types.addAll(Arrays.asList(args[++i].split(",")));
                case "--pid" -> pid = args[++i];
                case "--exe" -> exe = args[++i];
                case "--core" -> core = args[++i];
                // Everything after --spawn is the command, so it goes last.
                case "--spawn" -> {
                    spawn.addAll(Arrays.asList(args).subList(i + 1, args.length));
                    i = args.length;
                }
                default -> throw new IllegalArgumentException("unknown argument " + args[i]);
            }
        }

        Process target = null;
        if (!spawn.isEmpty()) {
            target = start(spawn);
            pid = String.valueOf(target.pid());
        }

        HotSpotAgent agent = new HotSpotAgent();
        if (pid != null) {
            agent.attach(Integer.parseInt(pid));
        } else {
            agent.attach(exe, core);
        }
        try {
            System.out.println(dump(VM.getVM().getTypeDataBase(), types));
        } finally {
            // The agent stops the target while it is attached, so a probe that forgets
            // this leaves a frozen JVM behind and the next route measures a corpse.
            agent.detach();
            if (target != null) target.destroyForcibly();
        }
    }

    /** Start the target and wait until it says it is up, rather than sleeping and hoping. */
    static Process start(List<String> command) throws Exception {
        Process target = new ProcessBuilder(command).redirectErrorStream(true).start();
        BufferedReader lines = new BufferedReader(new InputStreamReader(target.getInputStream()));
        long deadline = System.currentTimeMillis() + 120_000;
        while (System.currentTimeMillis() < deadline) {
            String line = lines.readLine();
            if (line == null) throw new IllegalStateException("the target exited before it was up");
            if (line.trim().equals("ready")) return target;
        }
        throw new IllegalStateException("the target never said it was up");
    }

    static String dump(TypeDataBase db, List<String> types) {
        StringBuilder out = new StringBuilder("{\n");
        for (int i = 0; i < types.size(); i++) {
            String name = types.get(i);
            // throwException = false, so a type this build does not have comes back null
            // and is reported as null rather than ending the run. Which types exist is
            // part of the answer.
            Type type = db.lookupType(name, false);
            out.append("  \"").append(name).append("\": ").append(one(type));
            out.append(i + 1 < types.size() ? ",\n" : "\n");
        }
        return out.append("}").toString();
    }

    static String one(Type type) {
        if (type == null) return "null";
        StringBuilder out = new StringBuilder("{\"size\": ").append(type.getSize());
        out.append(", \"super\": ").append(type.getSuperclass() == null
            ? "null" : "\"" + type.getSuperclass().getName() + "\"");
        out.append(", \"fields\": [");
        boolean first = true;
        for (Iterator<?> it = type.getFields(); it.hasNext(); ) {
            Field field = (Field) it.next();
            if (!first) out.append(", ");
            first = false;
            out.append("{\"name\": \"").append(field.getName())
               .append("\", \"type\": \"").append(field.getType().getName())
               .append("\", \"static\": ").append(field.isStatic())
               // A static field has an address rather than an offset, and asking one for
               // its offset throws. The generator wants instance fields, so the address is
               // left out rather than reported as a number that means something else.
               .append(", \"offset\": ").append(field.isStatic()
                   ? "null" : String.valueOf(field.getOffset()))
               .append("}");
        }
        return out.append("]}").toString();
    }
}
