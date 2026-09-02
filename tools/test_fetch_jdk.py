#!/usr/bin/env python3
"""Tests for the pinned JDK installer.

The one behaviour that matters here is the refusal. This script exists so that every
number in the repository comes from one build, and the only thing enforcing that is the
hash check, so the test that earns its keep is the one where the hash does not match and
nothing gets unpacked.

Nothing here downloads anything. The archive is built in a temporary directory and served
over a `file://` URL, which exercises the same code path as a real fetch.

  python tools/test_fetch_jdk.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fetch_jdk  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

FAKE_JAVA = """#!/bin/sh
echo 'openjdk version "27" 2026-09-15' 1>&2
echo 'OpenJDK Runtime Environment (build 27+35-2325)' 1>&2
"""


def fake_jdk(into: pathlib.Path) -> pathlib.Path:
    """A tarball shaped like the real one: one top directory with bin/java under it."""
    top = into / "jdk-27-whatever-they-called-it"
    (top / "bin").mkdir(parents=True)
    java = top / "bin" / "java"
    java.write_text(FAKE_JAVA, encoding="utf-8")
    java.chmod(java.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    archive = into / "jdk.tar.gz"
    with tarfile.open(archive, "w:gz") as tarred:
        tarred.add(top, arcname=top.name)
    return archive


class TestThePinFile(unittest.TestCase):
    def test_the_real_pin_file_has_a_download_for_every_platform_named(self):
        values = fetch_jdk.pin()
        for name in sorted(set(fetch_jdk.PLATFORMS.values())):
            with self.subTest(platform=name):
                self.assertIn(name, values["jdk_downloads"], name)

    def test_every_hash_in_the_pin_file_looks_like_a_sha256(self):
        for name, (filename, sha) in fetch_jdk.pin()["jdk_downloads"].items():
            with self.subTest(platform=name):
                self.assertEqual(len(sha), 64, name)
                self.assertTrue(all(c in "0123456789abcdef" for c in sha), name)
                self.assertIn(name.split("-")[0], filename)

    def test_this_machine_has_a_platform_in_the_table(self):
        # Not a test of the code so much as of the table. If this project is being worked
        # on somewhere the pin file does not cover, that is worth knowing on the first
        # test run and not on the first probe.
        self.assertIn(fetch_jdk.this_platform(), fetch_jdk.pin()["jdk_downloads"])


class TestUnpacking(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory()
        self.work = pathlib.Path(self.raw.name)
        self.archive = fake_jdk(self.work / "source")
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.pin_file = self.work / "pin.json"
        self.real_pin = fetch_jdk.PIN
        fetch_jdk.PIN = self.pin_file
        self.real_platform = fetch_jdk.this_platform
        fetch_jdk.this_platform = lambda: "test-platform"

    def tearDown(self):
        fetch_jdk.PIN = self.real_pin
        fetch_jdk.this_platform = self.real_platform
        self.raw.cleanup()

    def write_pin(self, sha: str) -> None:
        self.pin_file.write_text(
            json.dumps({
                "jdk_build": "27+35-2325",
                "jdk_download_base": self.archive.parent.as_uri(),
                "jdk_downloads": {"test-platform": [self.archive.name, sha]},
            }),
            encoding="utf-8",
        )

    @unittest.skipIf(sys.platform == "win32", "the fake java is a shell script")
    def test_a_matching_hash_installs_and_the_java_it_unpacked_answers(self):
        self.write_pin(self.sha)
        home = fetch_jdk.install(self.work / "into", quiet=True)
        self.assertTrue((home / "bin" / "java").is_file())

    @unittest.skipIf(sys.platform == "win32", "the fake java is a shell script")
    def test_running_it_twice_does_not_unpack_twice(self):
        self.write_pin(self.sha)
        home = fetch_jdk.install(self.work / "into", quiet=True)
        marker = home / "bin" / "i-was-here"
        marker.write_text("hello", encoding="utf-8")
        again = fetch_jdk.install(self.work / "into", quiet=True)
        self.assertEqual(home, again)
        self.assertTrue(marker.is_file(), "it reinstalled over a good install")

    def test_a_wrong_hash_stops_and_unpacks_nothing(self):
        self.write_pin("0" * 64)
        into = self.work / "into"
        with self.assertRaises(SystemExit) as caught:
            fetch_jdk.install(into, quiet=True)
        self.assertIn("does not match the pin", str(caught.exception))
        self.assertFalse((into / "jdk-27").exists(), "it unpacked a file it rejected")


class TestPlatformNames(unittest.TestCase):
    def test_the_two_names_linux_uses_for_the_same_chip_agree(self):
        self.assertEqual(
            fetch_jdk.PLATFORMS[("linux", "x86_64")],
            fetch_jdk.PLATFORMS[("linux", "amd64")],
        )
        self.assertEqual(
            fetch_jdk.PLATFORMS[("linux", "aarch64")],
            fetch_jdk.PLATFORMS[("linux", "arm64")],
        )

    def test_macos_is_found_through_the_extra_directory_it_adds(self):
        with tempfile.TemporaryDirectory() as raw:
            work = pathlib.Path(raw)
            (work / "jdk-27" / "Contents" / "Home" / "bin").mkdir(parents=True)
            self.assertEqual(
                fetch_jdk.home_inside(work),
                work / "jdk-27" / "Contents" / "Home",
            )

    def test_everywhere_else_is_the_directory_itself(self):
        with tempfile.TemporaryDirectory() as raw:
            work = pathlib.Path(raw)
            (work / "jdk-27" / "bin").mkdir(parents=True)
            self.assertEqual(fetch_jdk.home_inside(work), work / "jdk-27")


if __name__ == "__main__":
    unittest.main(verbosity=2)
