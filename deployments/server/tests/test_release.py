# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("server_release", ROOT / "deployments/server/release.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
VERSION = "v1.5.0-XMW"
TAG = f"server-{VERSION}"


def filename(arch):
    return f"plane-server-linux-{arch}-{VERSION}.tar.gz"


class FakeGitHub:
    def __init__(self):
        self.records = []
        self.files = {}
        self.refs = {}
        self.operations = []
        self.fail_upload = False
        self.fail_publish = False

    def seed(self, tag, arch="amd64", name=VERSION, timestamp="2026-11-11T08:00:00Z"):
        archive = b"previous package"
        self.files[tag] = {
            filename(arch): archive,
            "SHA256SUMS": f"{hashlib.sha256(archive).hexdigest()}  {filename(arch)}\n".encode(),
        }
        record = {
            "id": len(self.records) + 1,
            "tag_name": tag,
            "name": name,
            "assets": [
                {
                    "name": file,
                    "state": "uploaded",
                    "size": len(content),
                    "updated_at": timestamp,
                }
                for file, content in self.files[tag].items()
            ],
        }
        self.records.append(record)
        return record

    def releases(self):
        return list(self.records)

    def download(self, tag, name, destination):
        Path(destination).write_bytes(self.files[tag][name])

    def upload(self, tag, files):
        self.operations.append(("upload", tag))
        if self.fail_upload:
            raise RuntimeError("upload failed")
        record = next(row for row in self.records if row["tag_name"] == tag)
        self.files.setdefault(tag, {}).update({path.name: path.read_bytes() for path in files})
        record["assets"] = [
            {
                "name": file,
                "state": "uploaded",
                "size": len(content),
                "updated_at": "2026-11-12T00:00:00Z",
            }
            for file, content in self.files[tag].items()
        ]

    def api(self, path, method="GET", payload=None):
        self.operations.append((method, path, payload))
        if path == "releases" and method == "POST":
            row = {"id": len(self.records) + 1, **payload, "assets": []}
            self.records.append(row)
            return row
        if path.startswith("git/matching-refs/"):
            return [{"ref": ref} for ref in self.refs]
        if path == "git/refs" and method == "POST":
            self.refs[payload["ref"]] = payload["sha"]
            return {}
        if path.startswith("git/refs/") and method == "PATCH":
            self.refs[path.removeprefix("git/")] = payload["sha"]
            return {}
        record_id = int(path.split("/")[1])
        record = next(row for row in self.records if row["id"] == record_id)
        if method == "GET":
            return record["assets"]
        if method == "DELETE":
            self.records.remove(record)
            return None
        if method == "PATCH":
            if self.fail_publish:
                raise RuntimeError("publish failed")
            record.update(payload)
            return {
                **record,
                "html_url": f"https://github.com/owner/plane/releases/tag/{record['tag_name']}",
            }
        raise AssertionError((method, path))


class VersionReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.client = FakeGitHub()
        self.prepare(b"new package")

    def prepare(self, content):
        (self.directory / filename("amd64")).write_bytes(content)
        (self.directory / "SHA256SUMS").write_text(f"{hashlib.sha256(content).hexdigest()}  {filename('amd64')}\n")
        (self.directory / "RELEASE_NOTES.md").write_text("Updated release notes")

    def publish(self, sha="a" * 40):
        return RELEASE.publish(self.client, VERSION, sha, "amd64", self.directory)

    def test_migrates_duplicate_releases_only_after_publishing_replacement(self):
        self.client.seed(f"server-amd64-{VERSION}-d41bfe04c732")
        self.client.seed(f"server-amd64-{VERSION}-8a2519b423f6")
        unrelated = self.client.seed("v1.5.0")
        previous_version = self.client.seed("server-amd64-v1.4.0-XMW-123456789abc", name="v1.4.0-XMW")
        self.assertEqual(self.publish(), f"https://github.com/owner/plane/releases/tag/{TAG}")
        self.assertEqual(
            {row["tag_name"] for row in self.client.records},
            {TAG, unrelated["tag_name"], previous_version["tag_name"]},
        )
        self.assertEqual(self.client.refs[f"refs/tags/{TAG}"], "a" * 40)
        release = next(row for row in self.client.records if row["tag_name"] == TAG)
        self.assertFalse(release["draft"])
        self.assertEqual(release["body"], "Updated release notes")
        published = next(
            i for i, op in enumerate(self.client.operations) if op[0] == "PATCH" and op[1].startswith("releases/")
        )
        self.assertTrue(all(i > published for i, op in enumerate(self.client.operations) if op[0] == "DELETE"))

    def test_same_version_reuses_release_id_and_url_and_overwrites_package(self):
        first_url = self.publish()
        release_id = self.client.records[0]["id"]
        self.prepare(b"newer package")
        self.assertEqual(self.publish("b" * 40), first_url)
        self.assertEqual(len(self.client.records), 1)
        self.assertEqual(self.client.records[0]["id"], release_id)
        self.assertEqual(self.client.files[TAG][filename("amd64")], b"newer package")
        self.assertEqual(self.client.refs[f"refs/tags/{TAG}"], "b" * 40)

    def test_other_architecture_is_preserved_and_has_combined_checksum(self):
        self.client.seed(f"server-arm64-{VERSION}-d41bfe04c732", arch="arm64")
        self.publish()
        self.assertEqual(len(self.client.records), 1)
        self.assertEqual(self.client.files[TAG][filename("arm64")], b"previous package")
        sums = self.client.files[TAG]["SHA256SUMS"].decode()
        self.assertIn(filename("amd64"), sums)
        self.assertIn(filename("arm64"), sums)
        self.prepare(b"another amd64 package")
        self.publish("c" * 40)
        self.assertIn(filename("arm64"), self.client.files[TAG])

    def test_failed_upload_or_publication_keeps_old_releases(self):
        for failure in ("fail_upload", "fail_publish"):
            with self.subTest(failure=failure):
                self.client = FakeGitHub()
                old = self.client.seed(f"server-amd64-{VERSION}-d41bfe04c732")
                setattr(self.client, failure, True)
                with self.assertRaises(RuntimeError):
                    self.publish()
                self.assertIn(old, self.client.records)
                self.assertFalse(any(operation[0] == "DELETE" for operation in self.client.operations))

    def test_corrupt_archive_stops_before_mutating_github(self):
        (self.directory / filename("amd64")).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.publish()
        self.assertEqual(self.client.operations, [])

    def test_workflow_serializes_shared_version_publication(self):
        workflow = (ROOT / ".github/workflows/server-package.yml").read_text()
        self.assertIn(
            "group: server-release-${{ github.repository }}-${{ needs.validate.outputs.image_tag }}",
            workflow,
        )
        self.assertIn("python3 deployments/server/release.py", workflow)
        self.assertNotIn("${GITHUB_SHA:0:12}", workflow)


if __name__ == "__main__":
    unittest.main()
