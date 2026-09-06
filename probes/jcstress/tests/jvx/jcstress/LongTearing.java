package jvx.jcstress;

import org.openjdk.jcstress.annotations.Actor;
import org.openjdk.jcstress.annotations.Description;
import org.openjdk.jcstress.annotations.Expect;
import org.openjdk.jcstress.annotations.JCStressTest;
import org.openjdk.jcstress.annotations.Outcome;
import org.openjdk.jcstress.annotations.State;
import org.openjdk.jcstress.infra.results.J_Result;

/**
 * A non volatile long written as all ones and read by another thread.
 *
 * <p>The specification allows a 64 bit write to a non volatile long to be split into two
 * 32 bit halves, so a reader is permitted to see a value that was never written. On any
 * 64 bit machine this does not happen and the test reports two outcomes. It is here as
 * the case where the answer is uninteresting for a reason worth teaching: the guarantee
 * comes from the hardware rather than from the language, and the lesson has to say which
 * of the two it is relying on.
 */
@JCStressTest
@Description("A long written without volatile, read whole or in halves")
@Outcome(id = "0", expect = Expect.ACCEPTABLE, desc = "read before the write")
@Outcome(id = "-1", expect = Expect.ACCEPTABLE, desc = "read after the write")
@Outcome(expect = Expect.ACCEPTABLE_INTERESTING, desc = "half of one write and half of another")
@State
public class LongTearing {

    long v;

    @Actor
    public void writer() {
        v = -1;
    }

    @Actor
    public void reader(J_Result r) {
        r.r1 = v;
    }
}
