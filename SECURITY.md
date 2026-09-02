# Security

This is a teaching repository, so the interesting security surface is not a login form or a database. It is that this project asks you to run things.

A lesson hands you a Colab bootstrap cell, a kernel installed from Maven Central, a JVMTI agent compiled as a shared library, a container image, and eventually a patched JDK built from source. All of that runs with your privileges on a machine you care about. A project whose entire pitch is "you can go and check" does not get to wave that away, so this page says what is done about it and how to tell us when it is not enough.

## What to report

Report anything that would let a lesson, a notebook, a workflow or a published artifact run code that the reader did not intend to run. Concretely, that includes a dependency pulled without a pinned version or a verified hash, a bootstrap cell that fetches from a URL nobody controls, a workflow that mixes untrusted input into a shell command or hands a write scoped token to something that does not need it, a published container image or tarball that cannot be traced back to the commit it was built from, and a credential of any kind committed to the tree.

Also report a lesson that teaches an unsafe habit without saying so. Telling a reader to pipe a script into a shell, or to disable verification to make an example work, is a defect here even when nothing is compromised, because the reader will carry the habit somewhere it matters.

Two things are explicitly not vulnerabilities in this repository. The first is a lesson that deliberately builds a malformed class file, crashes a JVM, or corrupts a heap on purpose. That is the subject matter, it is labelled, and it runs in a throwaway VM. The second is a bug in OpenJDK itself. Those go to Oracle through the process at <https://openjdk.org/groups/vulnerability/report>, not here, and please do not open a public issue against this repository describing one.

## How to report

Use GitHub's private vulnerability reporting on the [Security tab](https://github.com/tamnd/jvm-internals/security/advisories/new). That opens a private thread with the maintainers and gives us a way to credit you and to publish an advisory when it is fixed.

Do not open a public issue for something that is exploitable against readers until it has been fixed.

Expect an acknowledgement within a week. This is a small project and there is no on call rotation, so that is a realistic number rather than an aspirational one. If something is being actively exploited against readers, say so in the first line and it moves to the front of the queue.

## What we do about it

Every GitHub Action is pinned to a commit SHA with the version in a trailing comment, because a tag is a moving pointer and a pinned tag is not pinned. Every workflow declares `contents: read` at the top level and asks for anything more per job, with a comment on the same line saying what the scope is for. Every tool downloaded inside a workflow is fetched at a fixed version and checked against a recorded SHA-256 before it is run.

Dependabot proposes action bumps weekly with a seven day cooldown, so a compromised release has time to be yanked before this repository pulls it. OpenSSF Scorecard runs weekly and on every push to `main`, publishes its result to the public API, and uploads SARIF to code scanning. actionlint and zizmor run on every pull request at the pedantic persona, and a finding fails the run rather than filing an alert nobody opens.

None of that is a guarantee. It is a set of checks whose output is public, which is the most this project can honestly claim.

## Supply chain, once there is something to ship

The published artifacts do not exist yet. When they do, each will carry a provenance attestation tying it to the commit and the workflow run that produced it, and the verification command will be printed next to the download rather than buried in a document. A reader who cannot check where a binary came from is being asked to trust us, and this project is not built on being trusted.
