package jvx.jcstress;

import org.openjdk.jcstress.annotations.Actor;
import org.openjdk.jcstress.annotations.Description;
import org.openjdk.jcstress.annotations.Expect;
import org.openjdk.jcstress.annotations.JCStressTest;
import org.openjdk.jcstress.annotations.Outcome;
import org.openjdk.jcstress.annotations.State;
import org.openjdk.jcstress.infra.results.II_Result;

/**
 * Dekker's idiom. Each thread writes one field and then reads the other one.
 *
 * <p>Read the code and there is no interleaving that explains both threads reading zero.
 * One of the two writes has to happen first, so the thread that writes second must see
 * the other write. On x86 it happens anyway, because a store sits in a store buffer that
 * the other core cannot see while the load that follows it has already gone ahead. This
 * is the outcome the whole concurrency part is about, and the question this probe asks
 * is whether a reader would ever see it on the hardware they have.
 */
@JCStressTest
@Description("Two threads each write one field and read the other")
@Outcome(id = "1, 1", expect = Expect.ACCEPTABLE, desc = "both loads saw the other store")
@Outcome(id = "0, 1", expect = Expect.ACCEPTABLE, desc = "one ran fully before the other")
@Outcome(id = "1, 0", expect = Expect.ACCEPTABLE, desc = "the other order")
@Outcome(id = "0, 0", expect = Expect.ACCEPTABLE_INTERESTING,
         desc = "both loads saw the initial value, which no interleaving explains")
@State
public class StoreBuffering {

    int x;
    int y;

    @Actor
    public void one(II_Result r) {
        x = 1;
        r.r1 = y;
    }

    @Actor
    public void two(II_Result r) {
        y = 1;
        r.r2 = x;
    }
}
