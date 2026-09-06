package jvx.jcstress;

import org.openjdk.jcstress.annotations.Actor;
import org.openjdk.jcstress.annotations.Description;
import org.openjdk.jcstress.annotations.Expect;
import org.openjdk.jcstress.annotations.JCStressTest;
import org.openjdk.jcstress.annotations.Outcome;
import org.openjdk.jcstress.annotations.State;
import org.openjdk.jcstress.infra.results.I_Result;

/**
 * An object published through a plain field, read by another thread.
 *
 * <p>The reference can become visible before the write to the field inside the object,
 * so a reader can hold a fully allocated object and read a field that has not been
 * written yet. This is the failure behind every broken double checked locking example
 * ever written, and it is much rarer in practice than the store buffering one, which is
 * exactly why a probe has to say how rare rather than a lesson asserting it.
 */
@JCStressTest
@Description("An object published without safe publication")
@Outcome(id = "-1", expect = Expect.ACCEPTABLE, desc = "the reader got there first")
@Outcome(id = "1", expect = Expect.ACCEPTABLE, desc = "fully constructed, as expected")
@Outcome(id = "0", expect = Expect.ACCEPTABLE_INTERESTING,
         desc = "the reference arrived before the field inside it")
@State
public class UnsafePublication {

    Holder h;

    @Actor
    public void writer() {
        h = new Holder();
    }

    @Actor
    public void reader(I_Result r) {
        Holder local = h;
        r.r1 = local == null ? -1 : local.x;
    }

    static class Holder {
        int x = 1;
    }
}
