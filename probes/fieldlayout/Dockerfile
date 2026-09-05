# The machine the field layout measurement is made on.
#
# Issue #5 needs a fastdebug JDK, because `PrintFieldLayout` is a develop flag and #33
# measured that it is in no product build on any of four platforms. So this image is a
# JDK build environment and nothing else: the same packages `doc/building.md` asks for
# on an apt based Linux, with no disassembler backends, because what this probe reads is
# the VM's own layout log rather than anything it emits as code.
#
# A container rather than a machine, for the same reason as the hsdis probe. A JDK build
# environment is a dozen development packages, and building the JDK writes about ten
# gigabytes, and neither belongs on a box somebody else is using.
#
# The base is ubuntu:24.04 by tag rather than by digest, because the probe records the
# digest it actually ran on into its results file. Pinning here as well would make the
# results say what this file says instead of what the run did.
#
#   docker build -t jvx-fieldlayout probes/fieldlayout
#   docker run --rm -v "$PWD:/repo:ro" -v /tmp/fieldlayout:/work jvx-fieldlayout \
#       python3 /repo/probes/fieldlayout/build.py
#   docker run --rm -v "$PWD:/repo:ro" -v /tmp/fieldlayout:/work jvx-fieldlayout \
#       python3 /repo/probes/fieldlayout/run.py --out /work/linux-x64.json
FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
      build-essential autoconf zip unzip file ca-certificates curl git python3 \
      libfreetype6-dev libfontconfig-dev libcups2-dev libasound2-dev libffi-dev \
      libx11-dev libxext-dev libxrender-dev libxrandr-dev libxtst-dev libxt-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
