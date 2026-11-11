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
import sys
import tarfile
import tempfile

COMPONENTS = ("web", "space", "admin", "live", "backend", "proxy")
ARCHIVES = (*COMPONENTS, "dependencies")
REPO_ROOT = Path(__file__).resolve().parents[2]


def image_tag():
    """Use the application's real version for all self-hosted component tags."""
    version = json.loads((REPO_ROOT / "package.json").read_text()).get("version")
    if not isinstance(version, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",
        version,
    ):
        raise ValueError("Expected a semantic application version in package.json")
    tag = f"v{version}-XMW"
    if len(tag) > 128:
        raise ValueError("Application version exceeds the Docker tag length limit")
    return tag


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(images_dir, output_dir, sha, arch, repository, run_id):
    version = image_tag()
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
    archive = output_dir / f"plane-server-linux-{arch}-{version}.tar.gz"
    with tempfile.TemporaryDirectory(dir=output_dir) as temp_dir:
        root = Path(temp_dir) / "plane-server"
        (root / "images").mkdir(parents=True)
        source = REPO_ROOT / "deployments/server"
        for name in ("compose.server.yml", ".env.example", "deploy.sh", "README.md"):
            shutil.copy2(source / name, root / name)
        shutil.copy2(
            REPO_ROOT / "deployments/cli/community/docker-compose.yml",
            root / "docker-compose.yml",
        )
        shutil.copy2(REPO_ROOT / "LICENSE.txt", root / "LICENSE.txt")
        images = {name: f"plane-selfhost/{name}:{version}" for name in COMPONENTS}
        (root / "images.env").write_text(
            f"PLANE_ARCH={arch}\nPLANE_VERSION={version}\n"
            + "".join(f"PLANE_{name.upper()}_IMAGE={image}\n" for name, image in images.items())
        )
        (root / "BUILD_INFO.json").write_text(
            json.dumps(
                {
                    "version": version,
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
    readme = (REPO_ROOT / "deployments/server/README.md").read_text()
    deployment = readme.split("## 首次部署", 1)[1].split("## 本仓库中的构建验证", 1)[0]
    notes = (
        f"# Plane {version} 服务器离线部署包（linux/{arch}）\n\n"
        f"- 应用镜像版本：`{version}`\n"
        f"- 源代码提交：[`{sha}`](https://github.com/{repository}/commit/{sha})\n"
        f"- 构建运行：https://github.com/{repository}/actions/runs/{run_id}\n"
        f"- CPU 架构：`linux/{arch}`\n"
        f"- 下载本 Release 的 **`{archive.name}`** 和 **`SHA256SUMS`**。\n\n"
        "包含 Web、Space、Admin、Live、API/Worker、代理及 PostgreSQL、Valkey、RabbitMQ、Silo 镜像。"
        "不需要服务器现场编译或登录镜像仓库。\n\n"
        "同版本的后续成功构建会覆盖本 Release 的同名附件，并更新这里的源代码提交和构建记录。"
        "下载时请选择服务器对应架构；其他架构的附件会保留。\n\n"
        "## 项目工作流功能\n\n"
        "- 待评审收集项同时出现在工作项对应状态列。\n"
        "- 看板卡片顶部 ID 支持完整父级路径，祖先可点击，当前项字号较小。\n"
        "- 配置测试任务初始状态及触发路径后，完成开发自动生成测试子任务。\n"
        "- 项目成员默认查看本人负责项及全部待评审、已评审项，可切换全量。\n"
        "- 项目总览包含状态汇总和全项目工作项时间线。\n\n"
        f"## 首次部署{deployment}"
    )
    (output_dir / "RELEASE_NOTES.md").write_text(notes)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-image-tag",
        action="store_true",
        help="Print the tag derived from package.json and exit",
    )
    if "--print-image-tag" in sys.argv[1:]:
        parser.parse_args()
        print(image_tag())
        return
    parser.add_argument("--sha", required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        package(
            args.images_dir,
            args.output_dir,
            args.sha,
            args.arch,
            args.repository,
            args.run_id,
        )
    )


if __name__ == "__main__":
    main()
