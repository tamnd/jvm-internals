/**
 * A JVM to look at, and nothing else.
 *
 * It says `ready` on stdout once it is up so the probe attaches to a VM that has finished
 * starting rather than to one that is still building its heap, and then it waits. It
 * touches one object of each kind the probe asks about so the classes are loaded, because
 * a type database entry exists whether or not anything used it but an offset that has
 * never been exercised is a worse thing to publish.
 */
public class Target {
    static Object held;

    public static void main(String[] args) throws Exception {
        held = new Object();
        held = Integer.valueOf(7);
        held = new int[4];
        held = Target.class;
        System.out.println("ready");
        System.out.flush();
        Thread.sleep(Long.MAX_VALUE);
    }
}
