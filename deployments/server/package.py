#!/usr/bin/env python3
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Create a standalone, checksummed server package from CI's Docker archives."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

COMPONENTS = ("web", "space", "admin", "live", "backend", "proxy")
ARCHIVES = (*COMPONENTS, "dependencies")
REPO_ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(images_dir, output_dir, sha, arch, repository, run_id):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full 40-character commit SHA")
    if arch not in {"amd64", "arm64"}:
        raise ValueError("Expected amd64 or arm64 architecture")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not str(run_id).isdigit():
        raise ValueError("Invalid repository or Actions run ID")
    images_dir, output_dir = Path(images_dir), Path(output_dir)
    for name in ARCHIVES:
        path = images_dir / f"{name}.tar.gz"
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty Docker archive: {path.name}")
        with path.open("rb") as stream:
            if stream.read(2) != b"\x1f\x8b":
                raise ValueError(f"Docker archive must be gzip compressed: {path.name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"plane-server-linux-{arch}-{sha[:12]}.tar.gz"
    with tempfile.TemporaryDirectory(dir=output_dir) as temp_dir:
        root = Path(temp_dir) / "plane-server"
        (root / "images").mkdir(parents=True)
        source = REPO_ROOT / "deployments/server"
        for name in ("compose.server.yml", ".env.example", "deploy.sh", "README.md"):
            shutil.copy2(source / name, root / name)
        shutil.copy2(REPO_ROOT / "deployments/cli/community/docker-compose.yml", root / "docker-compose.yml")
        shutil.copy2(REPO_ROOT / "LICENSE.txt", root / "LICENSE.txt")
        images = {name: f"plane-selfhost/{name}:{sha}" for name in COMPONENTS}
        (root / "images.env").write_text(
            f"PLANE_ARCH={arch}\n" + "".join(f"PLANE_{name.upper()}_IMAGE={image}\n" for name, image in images.items())
        )
        (root / "BUILD_INFO.json").write_text(
            json.dumps(
                {
                    "commit": sha,
                    "architecture": f"linux/{arch}",
                    "repository": repository,
                    "source": f"https://github.com/{repository}/tree/{sha}",
                    "workflow_run": f"https://github.com/{repository}/actions/runs/{run_id}",
                    "images": images,
                },
                indent=2,
            )
            + "\n"
        )
        checksums = []
        for name in ARCHIVES:
            target = root / "images" / f"{name}.tar.gz"
            shutil.copy2(images_dir / target.name, target)
            checksums.append(f"{digest(target)}  images/{target.name}\n")
        (root / "SHA256SUMS").write_text("".join(checksums))
        # Only the explicitly assembled directory is archived; no checkout .env or source credentials.
        with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
            tar.add(root, arcname="plane-server")
    (output_dir / "SHA256SUMS").write_text(f"{digest(archive)}  {archive.name}\n")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(package(args.images_dir, args.output_dir, args.sha, args.arch, args.repository, args.run_id))


if __name__ == "__main__":
    main()
