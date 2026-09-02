#!/usr/bin/env python3
"""Put the pinned JDK on a machine, and refuse to put anything else there.

Every number this project publishes was measured on one JDK build. A probe run against
whatever `java` happened to be on PATH is not a result, it is a rumour, so this is how
the pinned build gets onto a machine: the URL and the SHA256 come out of `docs/pin.json`,
the download is checked against the hash before it is unpacked, and a mismatch is a hard
stop rather than a warning.

  python tools/fetch_jdk.py                 install into ~/.cache/jvx and print JAVA_HOME
  python tools/fetch_jdk.py --where         print the path it would use and exit
  python tools/fetch_jdk.py --dir /opt/jvx  somewhere else

Already installed and matching the pin is a no-op, so this is safe to run on every CI
build and cheap to run in a loop over several machines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = ROOT / "docs" / "pin.json"
DEFAULT_DIR = pathlib.Path.home() / ".cache" / "jvx"

# What the download is called on each platform, keyed the way the pin file keys it.
PLATFORMS = {
    ("linux", "x86_64"): "linux-x64",
    ("linux", "amd64"): "linux-x64",
    ("linux", "aarch64"): "linux-aarch64",
    ("linux", "arm64"): "linux-aarch64",
    ("darwin", "arm64"): "macos-aarch64",
    ("windows", "amd64"): "windows-x64",
    ("windows", "x86_64"): "windows-x64",
}


def pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def this_platform() -> str:
    key = (platform.system().lower(), platform.machine().lower())
    if key not in PLATFORMS:
        # Naming both halves, because "unsupported platform" with no detail is the least
        # useful error a setup script can produce.
        sys.exit(
            f"no pinned JDK for {key[0]} {key[1]}. "
            f"The pin file has {', '.join(sorted(set(PLATFORMS.values())))}."
        )
    return PLATFORMS[key]


def home_inside(directory: pathlib.Path) -> pathlib.Path:
    """Where java lives once the archive is unpacked, which is not the same on macOS."""
    unpacked = directory / "jdk-27"
    if (unpacked / "Contents" / "Home").is_dir():
        return unpacked / "Contents" / "Home"
    return unpacked


def digest(path: pathlib.Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        # A JDK is a couple of hundred megabytes and reading it into a string to hash it
        # would work fine and is still the wrong habit to write down.
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def works(home: pathlib.Path, wanted_build: str) -> bool:
    binary = home / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
    if not binary.is_file():
        return False
    try:
        done = subprocess.run(
            [str(binary), "-version"], capture_output=True, text=True, timeout=120
        )
    except OSError:
        return False
    return wanted_build in (done.stdout + done.stderr)


def install(directory: pathlib.Path, quiet: bool = False) -> pathlib.Path:
    values = pin()
    name = this_platform()
    filename, wanted = values["jdk_downloads"][name]
    url = f"{values['jdk_download_base']}/{filename}"
    home = home_inside(directory)

    if works(home, values["jdk_build"]):
        if not quiet:
            print(f"{values['jdk_build']} is already at {home}", file=sys.stderr)
        return home

    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        archive = pathlib.Path(raw) / filename
        if not quiet:
            print(f"fetching {url}", file=sys.stderr)
        with urllib.request.urlopen(url) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)

        found = digest(archive)
        if found != wanted:
            sys.exit(
                f"the download does not match the pin.\n"
                f"  wanted {wanted}\n"
                f"  got    {found}\n"
                f"Nothing was unpacked. Either the pin file is stale or that is not the "
                f"file it names, and neither of those should be worked around here."
            )

        # Unpack beside the final location and move it into place, so an interrupted run
        # cannot leave half a JDK somewhere that looks installed.
        staging = pathlib.Path(raw) / "unpacked"
        staging.mkdir()
        if filename.endswith(".zip"):
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(staging)
        else:
            with tarfile.open(archive) as tarred:
                tarred.extractall(staging, filter="data")

        inner = next(p for p in staging.iterdir() if p.is_dir())
        target = directory / "jdk-27"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(inner), str(target))

    home = home_inside(directory)
    if not works(home, values["jdk_build"]):
        sys.exit(f"unpacked to {home} and it does not report {values['jdk_build']}")
    return home


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    ap.add_argument("--where", action="store_true", help="print the path and do nothing")
    args = ap.parse_args(argv)

    if args.where:
        print(home_inside(args.dir))
        return 0

    # The path on stdout and everything else on stderr, so a caller can say
    # JAVA_HOME=$(python tools/fetch_jdk.py) and get a path and not a progress report.
    print(install(args.dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
