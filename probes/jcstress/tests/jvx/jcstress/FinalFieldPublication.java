package jvx.jcstress;

import org.openjdk.jcstress.annotations.Actor;
import org.openjdk.jcstress.annotations.Description;
import org.openjdk.jcstress.annotations.Expect;
import org.openjdk.jcstress.annotations.JCStressTest;
import org.openjdk.jcstress.annotations.Outcome;
import org.openjdk.jcstress.annotations.State;
import org.openjdk.jcstress.infra.results.I_Result;

/**
 * The same publication with a final field, which is the control for {@link
 * UnsafePublication}.
 *
 * <p>Making the field final is the whole fix, and reading zero here is forbidden. The
 * pair is what makes the lesson: one word of source, one line of difference in the
 * outcome table, and nothing else changed.
 */
@JCStressTest
@Description("An object published through a plain field, with a final field inside it")
@Outcome(id = "-1", expect = Expect.ACCEPTABLE, desc = "the reader got there first")
@Outcome(id = "1", expect = Expect.ACCEPTABLE, desc = "fully constructed, as expected")
@Outcome(id = "0", expect = Expect.FORBIDDEN,
         desc = "final field semantics forbid seeing the default")
@State
public class FinalFieldPublication {

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
        final int x;

        Holder() {
            x = 1;
        }
    }
}
