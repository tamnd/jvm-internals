// What JOL believes about the layout of twelve classes, in a form a script can compare
// against what the VM printed for the same classes in the same process.
//
// Issue #5. The comparison only means something if both halves come from one JVM, so
// this runs under -XX:+PrintFieldLayout and the probe reads the VM's log and this
// program's output out of the same run. Anything else would be comparing two VMs.
//
// The twelve classes are declared here rather than borrowed from the JDK, because the
// interesting shapes are the ones that make a layout algorithm choose: a class with
// nothing in it, one field of each width, a mix that has to be packed, references next
// to primitives, and three levels of inheritance where a subclass either fills its
// superclass's padding or does not. java.lang.String is the twelfth, because a real
// class from the module the header work touched is worth one slot.
//
// Every line this prints starts with JVX| so that JOL's own warnings, which are the
// evidence for "silently fell back", stay separable from its answers.

import java.lang.reflect.Constructor;
import java.util.ArrayList;
import java.util.List;

import org.openjdk.jol.info.ClassLayout;
import org.openjdk.jol.info.FieldLayout;
import org.openjdk.jol.vm.VM;

public class JolDump {

    public static class Empty {
    }

    public static class OneBoolean {
        boolean a;
    }

    public static class OneInt {
        int a;
    }

    public static class OneLong {
        long a;
    }

    public static class OneRef {
        Object a;
    }

    public static class TwoRefs {
        Object a;
        Object b;
    }

    public static class MixedPrimitives {
        long a;
        int b;
        short c;
        char d;
        byte e;
        boolean f;
    }

    public static class RefsAndPrimitives {
        Object a;
        long b;
        Object c;
        int d;
    }

    public static class Parent {
        int a;
    }

    public static class Child extends Parent {
        long b;
    }

    public static class Grandchild extends Child {
        Object c;
    }

    public static void main(String[] args) throws Exception {
        System.out.println("JVX|vm"
                + "|objectAlignment=" + VM.current().objectAlignment()
                + "|objectHeaderSize=" + VM.current().objectHeaderSize()
                + "|arrayHeaderSize=" + VM.current().arrayHeaderSize()
                + "|addressSize=" + VM.current().addressSize());
        System.out.println("JVX|details|" + VM.current().details().replace('\n', ' '));

        for (String name : args) {
            Class<?> type = Class.forName(name);
            emit("parseClass", name, ClassLayout.parseClass(type));
            Object made = instantiate(type);
            if (made == null) {
                System.out.println("JVX|note|" + name + "|could not be instantiated");
            } else {
                emit("parseInstance", name, ClassLayout.parseInstance(made));
            }
        }
    }

    // parseClass infers a layout and parseInstance reads one off an object that exists.
    // They are different code paths in JOL and only one of them can look at a real
    // header, so the probe asks for both and lets the comparison say whether they agree.
    private static void emit(String mode, String name, ClassLayout layout) {
        System.out.println("JVX|layout|" + mode + "|" + name
                + "|instanceSize=" + layout.instanceSize()
                + "|headerSize=" + layout.headerSize());
        List<FieldLayout> fields = new ArrayList<>(layout.fields());
        for (FieldLayout field : fields) {
            System.out.println("JVX|field|" + mode + "|" + name
                    + "|" + field.name()
                    + "|" + field.offset()
                    + "|" + field.size()
                    + "|" + field.typeClass());
        }
    }

    private static Object instantiate(Class<?> type) {
        try {
            Constructor<?> ctor = type.getDeclaredConstructor();
            ctor.setAccessible(true);
            return ctor.newInstance();
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }
}
