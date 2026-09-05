# A disassembler for the JIT lessons, and what it costs to have one

[The capability probe](capability.md) found that `-XX:+PrintAssembly` is accepted on all four platforms, turns on `DebugNonSafepoints`, prints an nmethod header for every compile, and then says `Loading hsdis library failed` and prints no instructions. Every JIT lesson that wants to show a reader what C2 emitted needs that to be a yes somewhere, and the capability matrix says `host.hsdis` is a no on every platform this project can reach. So this probe builds hsdis from the pinned JDK source, once per backend the JDK documents, and asks each result what it cost, what it drags in, where the VM will load it from, and what it prints. The full result is [the generated page](../generated/hsdis.md). This is what it means.

## The headline

**Building it is not the hard part.** On an eight core container, one JDK `configure` takes about twenty seconds and `make build-hsdis` takes between two and five, for every backend. The expensive step is the shallow clone of the JDK at the pinned tag, which took forty six seconds and is the only reason this probe takes minutes rather than seconds. Nothing here needs a JDK build: `build-hsdis` builds the library and stops, so none of the hours a full `make images` would take are in these numbers.

**The VM will load the library from anywhere.** All three backends worked from beside `libjvm.so`, from the JDK's `lib` directory, and from a directory on `LD_LIBRARY_PATH` with the JDK left untouched. That last one is the one that matters, because it means a reader does not need write access to their JDK, and this project does not have to ask anybody to modify an installed runtime to see a disassembly.

**The choice between the three is a licence choice rather than a technical one.** All three disassembled the same compiled method and produced the same 293 lines of instructions, in three different syntaxes. What differs is what comes with them. Capstone links two libraries, one of which is the C runtime. binutils links `libbfd` and `libopcodes` from `libbinutils`, whose copyright file offers the GPL at version 3 or later, and the JDK's own README says in as many words that a build using that backend may not be distributable, at src/utils/hsdis/README.md:73@jdk-27+35. LLVM links sixteen libraries from fourteen packages, including ICU and libxml2, because it links the whole of `libLLVM`.

**The fallback named in the issue does not exist on a product build.** `-XX:+PrintOptoAssembly` is accepted, prints 330 `C2-compiled nmethod` banners, and prints exactly one other line, which is the program's own output. It is not a smaller disassembler, it is a printer with its body compiled out. Getting a body under it needs a debug JVM, which is [issue #5](https://github.com/tamnd/jvm-internals/issues/5) and not this probe.

## What a reader gets today, and why the fallback is worth naming

With no backend at all, `-XX:CompileCommand=print,Bench::sum` prints 233 lines: the two nmethod headers, the complaint, and two `[MachCode]` sections holding the machine code as hex. The hex is real and it is the right bytes, and 94 of those lines start with an address, which is why the probe counts `[MachCode]` sections rather than address lines to decide whether anything was disassembled. A check that only looked for address shaped lines would call the fallback a success. That is the same mistake as calling `-XX:+PrintAssembly` supported because it exits zero.

## What the licence table can and cannot tell you

The generated page prints, for each library the built hsdis links against, the package that owns it on the measured machine and what that package's own copyright file declares. It is a table of what the packages say, not a conclusion. Three things in it are worth pointing at before anybody reads a decision out of it.

The obvious one is binutils. Its copyright file is not in Debian's machine readable format and names no licence in a parseable form, so the probe records the version its text offers, which is version 3 or later. That agrees with the JDK's own warning, which is the point of measuring it rather than repeating it.

The one that would have gone wrong is LLVM. The package that `configure` needs is `llvm-dev`, and `llvm-dev`'s copyright file declares GPL-2+, because that file covers the Debian packaging of a metapackage. The library the artifact actually links is `libLLVM.so.18.1`, which belongs to `libllvm18`, whose copyright file names `APACHE-2-LLVM-EXCEPTIONS` first. Reading the licence off the package you installed rather than off the library you shipped would have produced exactly the wrong answer here. That is why the probe resolves every `ldd` line to a path, asks the package manager which package owns that path, and reads that package's copyright file.

The one nobody expects is capstone. Its copyright file names `BSD-3-clause`, `BSD_LLVM` and `GPL-2+`, in that order, for different file groups within the package. "Capstone is BSD licensed" is a thing this measurement does not support on its own, and which file group the GPL-2+ covers is a question for whoever reads the file rather than for this page.

## What this means for issue #8

A disassembler is available to this project on Linux, at a build cost of about a minute, loadable without touching the JDK, and the container that builds it is `probes/hsdis/Dockerfile`, which also answers the devcontainer half of the issue: the E1 row of the README's environment table can have a real `hsdis` in it.

What is not answered here is whether this project should distribute a built library, and that question is not the same for the three backends. It is a licence question with a warning from the JDK attached to one of the three answers, and the honest output of a probe is the table rather than the decision.

## Where it ran

One environment, and this is a smaller sample than the other probes in this directory.

| name | platform | account | notes |
|---|---|---|---|
| `linux-x64` | linux-x86_64 | root in a container | Ubuntu 24.04, gcc 13.3.0, eight cores |

macOS on arm64 is not measured, and it is the gap that matters most, because it is what most readers of this project are on. It needs its own run: a different artifact name, a different set of packages, and a codesigning question that does not exist on Linux. Windows is not measured for the same reasons as everywhere else in this directory.

The container runs as root, which is unremarkable here in a way it was not for [the type database probe](sa-types.md). Nothing in this measurement is about permission. Installing a dozen development packages is what a build environment is, and the point of doing it in a container is that they land nowhere near the machine the rest of the repository is built on.

## Running it

```
docker build -t jvx-hsdis probes/hsdis
docker run --rm -v "$PWD:/repo:ro" -v /tmp/hsdisout:/out -v /tmp/hsdiswork:/work \
    jvx-hsdis python3 /repo/probes/hsdis/run.py --out /out/linux-x64.json
python tools/gen_hsdis.py
```

Give it five minutes and ten gigabytes of scratch, most of it the JDK clone. The repository is mounted read only, so nothing the probe does can land in the checkout, and the JDK it tries the libraries in is a copy rather than the pinned one every other probe measures. Run it with an empty work directory when the numbers matter: a second run against a work directory that already has a configured build tree reports a build time of one second, which is true and useless.

## What is still open

The macOS run. The decision about shipping a built library, which the table above informs and does not make. Whether a Colab notebook can have a disassembler at all, which is a different question again, because a notebook cannot spend a minute of a reader's time building one and would need a prebuilt artifact, which is the distribution question in a different hat. And the body under `-XX:+PrintOptoAssembly`, which needs the debug build in issue #5.
