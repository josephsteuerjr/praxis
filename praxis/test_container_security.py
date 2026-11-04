"""Offline build-contract tests; no apt/pip network or Docker daemon required."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent
CHECKER = ROOT / "scripts/check-system-security-versions.sh"


class ContainerManifestTests(unittest.TestCase):
    def test_runtime_suite_and_guard_wired_after_install(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM rust:1.85-slim-bookworm AS hands-builder", dockerfile)
        self.assertIn("FROM python:3.12-slim-trixie\n", dockerfile)
        self.assertIn(
            "COPY scripts/check-system-security-versions.sh "
            "/usr/local/bin/check-system-security-versions\n", dockerfile
        )
        apt_run = dockerfile.split("RUN apt-get update", 1)[1].split("\nCOPY", 1)[0]
        self.assertLess(apt_run.index("apt-get install"),
                        apt_run.index("&& sh /usr/local/bin/check-system-security-versions"))
        self.assertIn("requirements-apt.txt", apt_run)
        packages = [line.split("#", 1)[0].strip() for line in
                    (ROOT / "requirements-apt.txt").read_text().splitlines()]
        self.assertIn("ca-certificates", packages)
        self.assertIn("libbrotli1", packages)

    def test_python_floors_reject_old_and_allow_future_updates(self):
        requirements = {}
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                req = Requirement(line)
                requirements[req.name.lower()] = req.specifier
        for name, old, floor, future in (
            ("brotli", "1.2.0", "1.2.1", "1.3.0"),
            ("certifi", "2026.10.18", "2026.10.19", "2027.1.1"),
        ):
            with self.subTest(package=name):
                specifier = requirements[name]
                self.assertNotIn(old, specifier)
                self.assertIn(floor, specifier)
                self.assertIn(future, specifier)


@unittest.skipUnless(shutil.which("dpkg") and shutil.which("sh"), "requires Debian dpkg and sh")
class SystemVersionGuardTests(unittest.TestCase):
    def run_guard(self, ca="20250419+deb13u1", brotli="1.1.0-2+deb13u1", status="installed"):
        # Substitute only package inventory; compare using the real Debian algorithm.
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "dpkg-query"
            query.write_text('''#!/bin/sh
case "$2" in
    '-f=${db:Status-Status}') printf '%s' "$TEST_STATUS" ;;
    '-f=${Version}')
        case "$3" in
            ca-certificates) printf '%s' "$TEST_CA" ;;
            libbrotli1) printf '%s' "$TEST_BROTLI" ;;
            *) exit 1 ;;
        esac ;;
    *) exit 1 ;;
esac
''')
            query.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                       TEST_CA=ca, TEST_BROTLI=brotli, TEST_STATUS=status)
            return subprocess.run(["sh", str(CHECKER)], env=env, capture_output=True,
                                  text=True, timeout=5)

    def test_exact_security_floors_pass(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_future_security_updates_pass(self):
        result = self.run_guard(ca="20250419+deb13u2", brotli="1.1.0-2+deb13u10")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_stale_package_fails(self):
        for package, versions in (
            ("ca-certificates", {"ca": "20250419"}),
            ("libbrotli1", {"brotli": "1.1.0-2"}),
        ):
            with self.subTest(package=package):
                result = self.run_guard(**versions)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(package, result.stderr)
                self.assertIn("below security minimum", result.stderr)

    def test_noninstalled_and_missing_packages_fail(self):
        result = self.run_guard(status="config-files")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not installed", result.stderr)
        result = self.run_guard(ca="")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
