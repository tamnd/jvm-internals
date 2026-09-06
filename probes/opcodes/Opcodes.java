/*
 * The Java half of probes/opcodes/run.py. Two jobs, both of which need a JVM.
 *
 * The first is to dump `java.lang.classfile.Opcode`, which is the platform's own
 * opinion about what an opcode is: its byte, its length when that is fixed, the
 * kind of instruction it makes, and whether it is a wide form. That is one of the
 * three sources the generated table joins, and it is the only one of the three that
 * can be read off the pinned JDK rather than off a file on the internet.
 *
 * The second is to count. A table of 205 opcodes tells a reader nothing about which
 * ones they will meet, and the answer is not evenly spread: a handful of opcodes are
 * most of all the bytecode ever compiled, and a few are emitted by nothing. So this
 * walks a module of the runtime image through the class file API and counts every
 * instruction in every method body. It is the same reader's JDK, so the number is
 * about their platform rather than about a corpus somebody else chose.
 *
 * Output is one `key<TAB>value` line per fact, which run.py turns into JSON.
 *
 *   java Opcodes.java [module]      default java.base
 */

import java.lang.classfile.ClassFile;
import java.lang.classfile.Instruction;
import java.lang.classfile.Opcode;
import java.lang.classfile.instruction.DiscontinuedInstruction;
import java.lang.classfile.instruction.IncrementInstruction;
import java.lang.classfile.instruction.LoadInstruction;
import java.lang.classfile.instruction.StoreInstruction;
import java.net.URI;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

public class Opcodes {

    public static void main(String[] args) throws Exception {
        String module = args.length > 0 ? args[0] : "java.base";

        for (Opcode opcode : Opcode.values()) {
            String name = opcode.name();
            say("opcode." + name + ".bytecode", Integer.toString(opcode.bytecode()));
            say("opcode." + name + ".size", Integer.toString(opcode.sizeIfFixed()));
            say("opcode." + name + ".kind", opcode.kind().name());
            say("opcode." + name + ".wide", Boolean.toString(opcode.isWide()));
        }

        count(module);
    }

    /*
     * Walk one module of the runtime image and count instructions.
     *
     * `jrt:/` is the image the reader is running, not a jar fetched from anywhere, so
     * this measures the JDK the rest of the probe is describing. Classes that fail to
     * parse are recorded rather than skipped quietly: a class file inside the JDK that
     * the JDK's own class file API cannot read would be the most interesting single
     * line in the output, and swallowing it would be the way to never find out.
     */
    private static void count(String module) throws Exception {
        var image = FileSystems.getFileSystem(URI.create("jrt:/"));
        Path root = image.getPath("/modules/" + module);
        if (!Files.isDirectory(root)) {
            throw new IllegalArgumentException("no module " + module + " in this image");
        }

        Map<Opcode, Long> counts = new EnumMap<>(Opcode.class);
        // The highest local variable slot each instruction form ever names. A wide form
        // exists because a one byte operand cannot say 300, so the question that decides
        // what the wide forms are for is whether the ones that do occur are naming a
        // slot above 255 or widening their other operand instead. Asserting an answer to
        // that would be guessing, and it is one map away from being measured.
        Map<Opcode, Integer> widestSlot = new EnumMap<>(Opcode.class);
        int highestSlot = -1;
        int highestIncrement = Integer.MIN_VALUE;
        int lowestIncrement = Integer.MAX_VALUE;

        List<String> unreadable = new ArrayList<>();
        long classes = 0;
        long methods = 0;
        long bodies = 0;
        long instructions = 0;

        ClassFile parser = ClassFile.of();
        List<Path> files;
        try (var walk = Files.walk(root)) {
            files = walk.filter(p -> p.toString().endsWith(".class")).sorted().toList();
        }

        for (Path file : files) {
            classes++;
            try {
                var model = parser.parse(Files.readAllBytes(file));
                for (var method : model.methods()) {
                    methods++;
                    var code = method.code();
                    if (code.isEmpty()) {
                        continue;
                    }
                    bodies++;
                    for (var element : code.get()) {
                        if (!(element instanceof Instruction instruction)) {
                            continue;
                        }
                        counts.merge(instruction.opcode(), 1L, Long::sum);
                        instructions++;

                        int slot = slotOf(instruction);
                        if (slot >= 0) {
                            highestSlot = Math.max(highestSlot, slot);
                            widestSlot.merge(instruction.opcode(), slot, Math::max);
                        }
                        if (instruction instanceof IncrementInstruction increment) {
                            highestIncrement = Math.max(highestIncrement,
                                    increment.constant());
                            lowestIncrement = Math.min(lowestIncrement,
                                    increment.constant());
                        }
                    }
                }
            } catch (Exception problem) {
                unreadable.add(file + ": " + problem.getClass().getSimpleName());
            }
        }

        say("scan.module", module);
        say("scan.classes", Long.toString(classes));
        say("scan.methods", Long.toString(methods));
        say("scan.method_bodies", Long.toString(bodies));
        say("scan.instructions", Long.toString(instructions));
        say("scan.unreadable", Integer.toString(unreadable.size()));
        say("scan.highest_local_slot", Integer.toString(highestSlot));
        say("scan.highest_iinc_constant", Integer.toString(highestIncrement));
        say("scan.lowest_iinc_constant", Integer.toString(lowestIncrement));
        for (String line : unreadable) {
            say("unreadable", line);
        }

        for (Map.Entry<Opcode, Integer> entry : widestSlot.entrySet()) {
            say("slot." + entry.getKey().name(), Integer.toString(entry.getValue()));
        }

        // Every opcode, including the ones that never occurred. A zero that is present
        // is a measurement and a zero that is missing is a gap in the output, and a
        // reader of the committed results file cannot tell those apart afterwards.
        for (Opcode opcode : Opcode.values()) {
            say("count." + opcode.name(), Long.toString(counts.getOrDefault(opcode, 0L)));
        }
    }

    /* The local variable slot an instruction names, or -1 for one that names none. */
    private static int slotOf(Instruction instruction) {
        return switch (instruction) {
            case LoadInstruction load -> load.slot();
            case StoreInstruction store -> store.slot();
            case IncrementInstruction increment -> increment.slot();
            case DiscontinuedInstruction.RetInstruction ret -> ret.slot();
            default -> -1;
        };
    }

    private static void say(String key, String value) {
        System.out.println(key + "\t" + value.replace('\t', ' ').replace('\n', ' '));
    }
}
