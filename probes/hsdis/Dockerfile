# The machine the hsdis measurement is made on.
#
# A container rather than a laptop, for two reasons. Building hsdis needs a JDK build
# environment, which is a dozen development packages nobody wants installed on a machine
# they were not planning to build OpenJDK on. And the question on issue #8 is partly
# whether a disassembler can live in the devcontainer image, which is this shape exactly.
#
# The base is ubuntu:24.04 by tag rather than by digest, because the probe records the
# digest it actually ran on into its results file. Pinning here as well would make the
# results say what this file says instead of what the run did.
#
#   docker build -t jvx-hsdis probes/hsdis
#   docker run --rm -v "$PWD:/repo:ro" -v /tmp/hsdis:/out jvx-hsdis \
#       python3 /repo/probes/hsdis/run.py --out /out/linux-x64.json
FROM ubuntu:24.04

# The first block is what doc/building.md asks for on an apt based Linux, and the second
# is the three disassembler backends. They are separate because which of the second block
# is present is the thing being measured, and mixing them into one list would hide that
# the JDK build environment does not bring a disassembler with it.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
      build-essential autoconf zip unzip file ca-certificates curl git python3 \
      libfreetype6-dev libfontconfig-dev libcups2-dev libasound2-dev libffi-dev \
      libx11-dev libxext-dev libxrender-dev libxrandr-dev libxtst-dev libxt-dev \
 && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
      binutils-dev libcapstone-dev llvm-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
