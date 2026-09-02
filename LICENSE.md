# Licence

This repository holds three kinds of thing and they carry three different licences. The split is not an accident of history, it is designed so that a reader can take what they wrote here and use it.

## Prose, diagrams, animations and notebook narrative

Creative Commons Attribution 4.0 International, in `LICENSE-CONTENT.txt`.

That covers `lessons/**/*.md`, `blueprints/`, `README.md`, `ROADMAP.md`, every animation and every diagram. Share it, translate it, teach from it, sell a course built on it. Keep the attribution.

## Code

Apache License 2.0, in `LICENSE-CODE.txt`.

That covers `jvx/`, `jvxagent/`, `jvxmanim/`, `jvxwidgets/`, `tools/`, `capstones/`, `conformance/`, every `grade.py`, and every notebook cell.

Apache-2.0 rather than a copyleft licence on purpose. An instrumentation agent, a JVMTI agent, a JMH benchmark, a jcstress test or a jtreg test written while working through this material carries no obligation and can go into a proprietary codebase with no licence analysis. Several boss fights are designed to produce exactly that.

## Anything derived from OpenJDK

GNU General Public License version 2 with the Classpath Exception, which is OpenJDK's own licence.

That covers `patches/` and `vendor/`, and nothing else. A patch to the template interpreter is a derivative work of HotSpot and stays under HotSpot's terms. Every file in those two directories carries a header saying so, and CI fails a build that finds a GPL header anywhere outside them.

## Why the Classpath Exception matters here

The Classpath Exception is the reason ordinary lesson code is unencumbered. Code that merely links against the JDK, which is every notebook cell in this project, does not become a derivative work of the JDK. So the boundary that matters is not "did you use Java", it is "did you copy or modify OpenJDK source", and that boundary is drawn at the edge of `patches/` and `vendor/`.

## Not covered

OpenJDK itself is not in this repository. It arrives as a pinned tarball or a git checkout at the tag in `docs/pin.json` and stays under its own licence.

The Java Virtual Machine Specification and the Java Language Specification are Oracle's, quoted by section under fair use, and linked rather than reproduced. This project treats them as normative and never paraphrases them in place of quoting them.

Recorded output belongs to whoever generated it. A `-Xlog` trace, a JFR recording, a heap dump or a disassembly listing in `replays/` and `workloads/recorded/` is what the JVM said about a program we wrote, and the programs are written for the course.
