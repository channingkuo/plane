#!/usr/bin/env python3
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Replace the managed offline Release for an application version using GitHub CLI."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


class GitHub:
    def __init__(self, repository):
        self.repository = repository

    def api(self, path, method="GET", payload=None, paginate=False):
        command = ["gh", "api", "--method", method, f"repos/{self.repository}/{path}"]
        if paginate:
            command += ["--paginate", "--slurp"]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(payload) if payload is not None else None,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def releases(self):
        return [release for page in self.api("releases?per_page=100", paginate=True) for release in page]

    def download(self, tag, name, destination):
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                self.repository,
                "--pattern",
                name,
                "--output",
                str(destination),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def upload(self, tag, files):
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                tag,
                "--repo",
                self.repository,
                "--clobber",
                *map(str, files),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def managed_release(release, version):
    """Only versioned server releases belong to this publisher; leave other releases alone."""
    return (
        release.get("name") == version
        and re.fullmatch(
            rf"server-(?:(?:amd64|arm64)-)?{re.escape(version)}(?:-[0-9a-f]{{12,40}})?",
            release.get("tag_name", ""),
        )
        is not None
    )


def read_checksums(path, allowed_names):
    checksums = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] not in allowed_names:
            raise ValueError("Unexpected archive in Release checksums")
        checksums[match[2]] = match[1]
    if not checksums:
        raise ValueError("Empty Release checksums")
    return checksums


def publish(client, version, sha, arch, directory):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*-XMW", version):
        raise ValueError("Invalid application version")
    if arch not in {"amd64", "arm64"} or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid source commit or architecture")
    directory = Path(directory)
    names = {cpu: f"plane-server-linux-{cpu}-{version}.tar.gz" for cpu in ("amd64", "arm64")}
    archive = directory / names[arch]
    checksums = read_checksums(directory / "SHA256SUMS", set(names.values()))
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != checksums.get(archive.name):
            raise ValueError("Local archive checksum mismatch")
    notes = (directory / "RELEASE_NOTES.md").read_text()
    tag = f"server-{version}"
    releases = [release for release in client.releases() if managed_release(release, version)]
    current = next((release for release in releases if release["tag_name"] == tag), None)

    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        files = [archive]
        # Keep the most recent other-architecture package in the same version's Release.
        other = names["arm64" if arch == "amd64" else "amd64"]
        candidates = [
            (asset.get("updated_at", ""), release)
            for release in releases
            for asset in release.get("assets", [])
            if asset.get("name") == other and asset.get("state") == "uploaded"
        ]
        if candidates:
            source = max(candidates, key=lambda item: item[0])[1]
            old_checksums = temp / "previous-checksums"
            client.download(source["tag_name"], "SHA256SUMS", old_checksums)
            previous = read_checksums(old_checksums, set(names.values()))
            if other not in previous:
                raise ValueError("Other architecture has no checksum")
            checksums[other] = previous[other]
            if source["tag_name"] != tag:
                other_archive = temp / other
                client.download(source["tag_name"], other, other_archive)
                with other_archive.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != checksums[other]:
                        raise ValueError("Other architecture checksum mismatch")
                files.append(other_archive)
        # Do not retain a checksum for an archive that is absent from the final Release.
        checksums = {
            name: value for name, value in checksums.items() if name in {archive.name, other if candidates else ""}
        }
        checksum_file = temp / "SHA256SUMS"
        checksum_file.write_text("".join(f"{value}  {name}\n" for name, value in sorted(checksums.items())))
        files.append(checksum_file)
        if not current:
            current = client.api(
                "releases",
                "POST",
                {
                    "tag_name": tag,
                    "target_commitish": sha,
                    "name": version,
                    "draft": True,
                    "prerelease": True,
                },
            )
        client.upload(tag, files)
        assets = client.api(f"releases/{current['id']}/assets?per_page=100")
        expected = {archive.name, "SHA256SUMS"} | ({other} if candidates else set())
        if not expected.issubset(
            {asset["name"] for asset in assets if asset.get("state") == "uploaded" and asset.get("size", 0) > 0}
        ):
            raise RuntimeError("Release upload is incomplete; older releases were retained")
        # Move only our stable server-version tag, after the replacement assets are ready.
        references = client.api(f"git/matching-refs/tags/{tag}")
        reference = f"refs/tags/{tag}"
        if any(row["ref"] == reference for row in references):
            client.api(f"git/refs/tags/{tag}", "PATCH", {"sha": sha, "force": True})
        else:
            client.api("git/refs", "POST", {"ref": reference, "sha": sha})
        result = client.api(
            f"releases/{current['id']}",
            "PATCH",
            {
                "name": version,
                "target_commitish": sha,
                "body": notes,
                "draft": False,
                "prerelease": True,
                "make_latest": "false",
            },
        )
        # Clean up only after publishing a usable replacement. Preserve unrelated versions/tags.
        for release in releases:
            if release["id"] != current["id"]:
                client.api(f"releases/{release['id']}", "DELETE")
        return result["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise ValueError("Invalid repository")
    print(publish(GitHub(args.repository), args.version, args.sha, args.arch, args.directory))


if __name__ == "__main__":
    main()
