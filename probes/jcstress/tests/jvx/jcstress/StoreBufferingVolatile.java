package jvx.jcstress;

import org.openjdk.jcstress.annotations.Actor;
import org.openjdk.jcstress.annotations.Description;
import org.openjdk.jcstress.annotations.Expect;
import org.openjdk.jcstress.annotations.JCStressTest;
import org.openjdk.jcstress.annotations.Outcome;
import org.openjdk.jcstress.annotations.State;
import org.openjdk.jcstress.infra.results.II_Result;

/**
 * The same idiom with volatile fields, which is the control rather than the experiment.
 *
 * <p>Here 0, 0 is forbidden by the memory model, so jcstress fails the run if it ever
 * appears. It is in the set for two reasons. It shows that the harness is capable of
 * failing, which a set of tests that only ever passes cannot show. And a machine that
 * reports zero interesting outcomes for {@link StoreBuffering} has said nothing useful
 * unless this one ran on it too.
 */
@JCStressTest
@Description("Dekker's idiom with volatile fields, where 0, 0 is forbidden")
@Outcome(id = "1, 1", expect = Expect.ACCEPTABLE, desc = "both loads saw the other store")
@Outcome(id = "0, 1", expect = Expect.ACCEPTABLE, desc = "one ran fully before the other")
@Outcome(id = "1, 0", expect = Expect.ACCEPTABLE, desc = "the other order")
@Outcome(id = "0, 0", expect = Expect.FORBIDDEN,
         desc = "sequential consistency says this cannot happen, and it does not")
@State
public class StoreBufferingVolatile {

    volatile int x;
    volatile int y;

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
