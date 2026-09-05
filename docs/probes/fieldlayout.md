# Does JOL tell the truth about compact object headers

Part IV of the curriculum is built on compact object headers, O01 is the pilot lesson, and both of them want to hand the reader a tool that prints an object's layout. JOL is that tool: it is what everybody already reaches for, and its newest release is 0.17 from January 2023, two years before JEP 450 shipped. If JOL reports the legacy 12 byte header on a VM that is using the 8 byte one, or quietly falls back to a guess, then the pilot lesson is wrong and so are ten others.

The only thing that can settle it is the VM's own account of what it did. That is `-XX:+PrintFieldLayout`, which [the capability probe](capability.md) found is refused on all four machines it asked, because it is a `develop` flag and therefore compiled out of every product build. That is the `flag.PrintFieldLayout` row of [the capability matrix](../generated/capability-matrix.md), from [issue #33](https://github.com/tamnd/jvm-internals/issues/33). So this probe builds a fastdebug JDK from the pinned commit first, and that build is the reason this took two and a quarter hours of `make` rather than a minute.

The comparison happens inside one JVM. `JolDump.java` runs under `-XX:+PrintFieldLayout` and prints what JOL believes onto the same stream the VM prints what it did, so the two halves cannot be describing two different VMs, two different classes or two different flag settings. Twelve classes, four configurations, each run twice, once with self attach permitted and once refused. Ninety six class comparisons. The full result is [the generated page](../generated/fieldlayout.md).

## The headline

**JOL 0.17 is right.** Every field offset it reported matched the offset the VM printed, in all four configurations, with and without attach. It reports the header as 8 bytes when `UseCompactObjectHeaders` is on and 12 when it is off, and it gets array base offsets right in both. The pilot lesson can use it and so can the rest of Part IV.

**It is right in the configuration where it says it is guessing, too.** Refusing the attach changes nothing about the answer: the layouts come back byte for byte identical to the runs where the agent loaded. That is not luck. Field offsets come from `Unsafe.objectFieldOffset`, which needs no agent and no Serviceability Agent, and the machinery JOL cannot reach without attaching is the machinery for turning a reference into an address. The warning is real and it is about something else.

**It misses exactly one field, and that field does not exist in Java.** `java.lang.String` has a `flags` byte at offset 14 that the VM prints and JOL does not report. It is not a field `String` declares. HotSpot injects it, at src/hotspot/share/classfile/javaClasses.hpp:57@jdk-27+35, and reflection cannot see injected fields, so no reflection based tool can report it. This is the one genuine limitation the probe found, and it predates compact headers entirely.

## Why the missing field is worth a paragraph

It would be easy to write this up as "11 of 12" and move on, and that would leave a reader assuming JOL got an offset wrong somewhere. It did not. The distinction matters because the two failures have opposite consequences.

A wrong offset would mean JOL is modelling the layout incorrectly, and everything it says is suspect. A missing injected field means JOL is answering a slightly different question than the one the VM answers: JOL reports the fields the class declares, and the VM reports the fields the object has. For `String` those differ by one byte that HotSpot uses for its own bookkeeping. JOL's instance size for `String` is still 24, which is still correct, because the injected byte fits in padding that was already there.

The practical consequence is small and specific. If you are adding up declared fields to explain an object's size, JOL's numbers are right. If you are trying to account for every byte between offset 0 and the instance size, JOL will leave you a hole on a handful of classes that HotSpot injects into, and `String` is the one you are most likely to look at.

## What JOL says about itself, and why it is misleading here

Every run printed this, in every configuration:

```
# Lilliput VM detected (experimental)
# WARNING | Compressed references base/shifts are guessed by the experiment!
# WARNING | Therefore, computed addresses are just guesses, and ARE NOT RELIABLE.
# WARNING | Make sure to attach Serviceability Agent to get the reliable addresses.
```

Read quickly, that says do not trust this output. Read carefully, it says the *addresses* are guesses. It does not say the offsets are, and the measurement above is the evidence that they are not. A reader who has been told "JOL warns that it is unreliable on compact headers" has been told something true and has drawn the wrong conclusion from it, which is the kind of thing this project exists to take apart.

The name in that banner is worth noticing too. JOL calls this a Lilliput VM, which was the project name before the feature shipped as JEP 450, and it calls it experimental. On the pinned build it is neither: `UseCompactObjectHeaders` is a product flag that defaults to true.

**JOL's own suggested remedy did not work.** The banner says to attach the Serviceability Agent for reliable addresses. On this build the Serviceability Agent refused to attach in every attempt, including with `CAP_SYS_PTRACE` granted, seccomp unconfined and `sudo` available for `-Djol.tryWithSudo=true`, where it failed with a null message rather than a permissions error. Whether that is JOL 0.17 being too old for JDK 27's Serviceability Agent or something about this container is not measured here, so it is not claimed here. What is measured is that the advice in the warning was not available to follow, so anybody using JOL 0.17 on JDK 27 should expect to see that warning permanently.

## What this does not cover

One platform. This is linux-x86_64, because the fastdebug build ran on a Linux box, and `-XX:+PrintFieldLayout` needs a fastdebug build wherever it runs. An osx-arm64 answer needs a second two hour build on a Mac and is not here. Given that field offsets come from a platform independent layout algorithm and the four configurations already vary reference size and header shape, a different answer on macOS would be a surprise. It would also be exactly the kind of surprise this project is supposed to find rather than assume away, so it stays an open gap rather than an implied result.

Twelve classes, not the JDK. The eleven synthetic ones are declared in `JolDump.java` so their shape is controlled rather than borrowed, and they cover an empty object, each primitive alone, mixed primitives and references, and three levels of inheritance. `java.lang.String` is the twelfth because it is real, it is in the module the compact header work touched, and it is the class a reader is most likely to point a tool at. A sweep of every class in `java.base` would find more injected fields. It would not change the answer about offsets.

The comparison is against `PrintFieldLayout`, which is HotSpot telling us what HotSpot did. That is the right authority for this question and it is not an independent one. If the VM's layout code and its layout log disagreed, this probe would not see it.
