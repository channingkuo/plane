# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("server_package", ROOT / "deployments/server/package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)
SHA = "a" * 40
VERSION = f"v{json.loads((ROOT / 'package.json').read_text())['version']}-XMW"


class ServerPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "input"
        self.images.mkdir()
        for name in PACKAGE.ARCHIVES:
            (self.images / f"{name}.tar.gz").write_bytes(gzip.compress(f"test image {name}".encode()))
        self.output = self.root / "release"
        archive = PACKAGE.package(self.images, self.output, SHA, "amd64", "owner/plane", "123")
        with tarfile.open(archive) as tar:
            tar.extractall(self.root / "unpacked", filter="data")
        self.bundle = self.root / "unpacked/plane-server"
        self.log = self.root / "docker-calls.jsonl"
        binaries = self.root / "bin"
        binaries.mkdir()
        fake_docker = binaries / "docker"
        fake_docker.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['DOCKER_TEST_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'info': print(os.environ.get('DOCKER_TEST_ARCH', 'x86_64'))
if 'run' in sys.argv and 'migrator' in sys.argv and os.environ.get('FAIL_MIGRATION') == '1': sys.exit(23)
""")
        fake_docker.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "DOCKER_TEST_LOG": str(self.log),
        }

    def run_script(self, *args, **env):
        return subprocess.run(
            [str(self.bundle / "deploy.sh"), *args],
            cwd=self.bundle,
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def init(self, url="http://192.0.2.10:8080"):
        result = self.run_script("init", url)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(
            line.split("=", 1)
            for line in (self.bundle / ".env").read_text().splitlines()
            if line and not line.startswith("#")
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_bundle_pins_all_images_and_excludes_runtime_secrets(self):
        names = {p.name for p in (self.bundle / "images").iterdir()}
        self.assertEqual(names, {f"{name}.tar.gz" for name in PACKAGE.ARCHIVES})
        self.assertFalse((self.bundle / ".env").exists())
        manifest = json.loads((self.bundle / "BUILD_INFO.json").read_text())
        self.assertEqual(manifest["commit"], SHA)
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(set(manifest["images"]), set(PACKAGE.COMPONENTS))
        self.assertEqual(
            manifest["images"],
            {name: f"plane-selfhost/{name}:{VERSION}" for name in PACKAGE.COMPONENTS},
        )
        images_env = (self.bundle / "images.env").read_text()
        self.assertIn(f"PLANE_VERSION={VERSION}\n", images_env)
        for name in PACKAGE.COMPONENTS:
            self.assertIn(
                f"PLANE_{name.upper()}_IMAGE=plane-selfhost/{name}:{VERSION}\n",
                images_env,
            )
        self.assertNotIn(SHA, images_env)
        checked = subprocess.run(["sha256sum", "--check", "SHA256SUMS"], cwd=self.bundle, capture_output=True)
        self.assertEqual(checked.returncode, 0)
        self.assertTrue(os.access(self.bundle / "deploy.sh", os.X_OK))
        for secret in (
            "SECRET_KEY",
            "LIVE_SERVER_SECRET_KEY",
            "POSTGRES_PASSWORD",
            "AWS_SECRET_ACCESS_KEY",
        ):
            self.assertIn(secret + "=\n", (self.bundle / ".env.example").read_text())

    def test_release_notes_include_provenance_and_complete_deployment_instructions(
        self,
    ):
        notes = (self.output / "RELEASE_NOTES.md").read_text()
        self.assertIn(SHA, notes)
        self.assertIn("https://github.com/owner/plane/actions/runs/123", notes)
        self.assertIn(f"plane-server-linux-amd64-{VERSION}.tar.gz", notes)
        self.assertIn(f"应用镜像版本：`{VERSION}`", notes)
        for instruction in (
            "sha256sum --check SHA256SUMS",
            "./deploy.sh init",
            "./deploy.sh up",
            "## 升级和已有数据",
        ):
            self.assertIn(instruction, notes)
        self.assertNotIn("artifact ZIP", notes)
        self.assertNotIn("## 本仓库中的构建验证", notes)

    def test_image_tag_follows_real_application_version(self):
        with patch.object(PACKAGE, "REPO_ROOT", self.root):
            for version in ("1.5.0", "1.5.1", "1.5.2", "2.0.0", "1.6.0-rc.1"):
                with self.subTest(version=version):
                    (self.root / "package.json").write_text(json.dumps({"version": version}))
                    self.assertEqual(PACKAGE.image_tag(), f"v{version}-XMW")
            for version in (
                None,
                150,
                "",
                "latest",
                "v1.5.0",
                "01.5.0",
                "1.5.0\nBAD=1",
                "../1.5.0",
            ):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    (self.root / "package.json").write_text(json.dumps({"version": version}))
                    PACKAGE.image_tag()

    def test_ci_build_and_bundle_use_the_same_version_source(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "deployments/server/package.py"),
                "--print-image-tag",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), VERSION)
        workflow = (ROOT / ".github/workflows/server-package.yml").read_text()
        self.assertIn("python3 deployments/server/package.py --print-image-tag", workflow)
        self.assertIn(
            "tags: plane-selfhost/${{ matrix.name }}:${{ needs.validate.outputs.image_tag }}",
            workflow,
        )
        self.assertNotIn("tags: plane-selfhost/${{ matrix.name }}:${{ github.sha }}", workflow)

    def test_incomplete_image_set_cannot_produce_release(self):
        (self.images / "backend.tar.gz").unlink()
        with self.assertRaisesRegex(ValueError, "backend"):
            PACKAGE.package(self.images, self.root / "failed", SHA, "amd64", "owner/plane", "123")
        self.assertFalse((self.root / "failed").exists())

    def test_package_rejects_unsafe_metadata(self):
        for sha, arch, repository in [
            ("../bad", "amd64", "owner/repo"),
            (SHA, "unknown", "owner/repo"),
            (SHA, "amd64", "bad\nrepo"),
        ]:
            with (
                self.subTest(sha=sha, arch=arch, repository=repository),
                self.assertRaises(ValueError),
            ):
                PACKAGE.package(self.images, self.root / "failed", sha, arch, repository, "123")

    def test_init_generates_matching_secrets_without_overwriting_configuration(self):
        values = self.init()
        self.assertEqual(values["LISTEN_HTTP_PORT"], "8080")
        self.assertEqual(values["SITE_ADDRESS"], ":80")
        self.assertIn(":" + values["POSTGRES_PASSWORD"] + "@", values["DATABASE_URL"])
        self.assertIn(":" + values["RABBITMQ_PASSWORD"] + "@", values["AMQP_URL"])
        self.assertNotEqual(values["SECRET_KEY"], values["LIVE_SERVER_SECRET_KEY"])
        self.assertEqual(len(values["SECRET_KEY"]), 64)
        self.assertEqual(stat.S_IMODE((self.bundle / ".env").stat().st_mode), 0o600)
        original = (self.bundle / ".env").read_bytes()
        result = self.run_script("init", "https://another.example.com")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.bundle / ".env").read_bytes(), original)

    def test_https_init_and_invalid_urls(self):
        for url in (
            "https://plane.example.com/path",
            "ftp://plane.example.com",
            "http://host:99999",
            "http://host\nSECRET_KEY=bad",
        ):
            with self.subTest(url=url):
                self.assertNotEqual(self.run_script("init", url).returncode, 0)
                self.assertFalse((self.bundle / ".env").exists())
        values = self.init("https://plane.example.com")
        self.assertEqual(values["SITE_ADDRESS"], "plane.example.com")
        self.assertEqual(values["WEB_URL"], "https://plane.example.com")

    def test_failed_migration_never_starts_new_application(self):
        self.init()
        result = self.run_script("up", FAIL_MIGRATION="1")
        self.assertEqual(result.returncode, 23, result.stderr)
        calls = self.calls()
        self.assertTrue(any("run" in args and "migrator" in args for args in calls))
        self.assertFalse(any("up" in args and "api" in args for args in calls))
        self.assertFalse(any("down" in args for args in calls))

    def test_startup_imports_every_image_and_migrates_before_application(self):
        self.init()
        result = self.run_script("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(sum(args[0] == "load" for args in calls), len(PACKAGE.ARCHIVES))
        migration = next(i for i, args in enumerate(calls) if "run" in args and "migrator" in args)
        application = next(i for i, args in enumerate(calls) if "up" in args and "api" in args)
        self.assertLess(migration, application)
        self.assertIn("never", calls[application])
        self.assertIn("--no-build", calls[application])
        self.assertFalse(any("pull" == args[0] for args in calls))

    def test_wrong_architecture_and_corrupt_image_fail_before_import(self):
        self.init()
        result = self.run_script("load", DOCKER_TEST_ARCH="aarch64")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(args[0] == "load" for args in self.calls()))
        (self.bundle / "images/web.tar.gz").write_bytes(b"corrupt image")
        result = self.run_script("load")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(args[0] == "load" for args in self.calls()))

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI not installed")
    def test_real_compose_configuration_uses_only_packaged_images(self):
        self.init()
        command = [
            shutil.which("docker"),
            "compose",
            "--env-file",
            ".env",
            "--env-file",
            "images.env",
            "-f",
            "docker-compose.yml",
            "-f",
            "compose.server.yml",
            "config",
            "--format",
            "json",
        ]
        result = subprocess.run(command, cwd=self.bundle, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        services = config["services"]
        for name in (
            "web",
            "space",
            "admin",
            "live",
            "api",
            "worker",
            "beat-worker",
            "proxy",
        ):
            self.assertIn("plane-selfhost/", services[name]["image"])
            self.assertTrue(services[name]["image"].endswith(":" + VERSION))
            self.assertNotIn("build", services[name])
        self.assertNotIn("migrator", services)  # Maintenance profile is explicitly run by deploy.sh.
        workflow = (ROOT / ".github/workflows/server-package.yml").read_text()
        for name in ("plane-db", "plane-redis", "plane-mq", "plane-minio"):
            self.assertIn(services[name]["image"], workflow)


if __name__ == "__main__":
    unittest.main()
