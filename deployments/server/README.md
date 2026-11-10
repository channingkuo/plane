# Plane 服务器离线部署包

这个包包含当前提交编译的 Web、Space、Admin、Live、API/Worker 和代理镜像，以及 PostgreSQL、Valkey、RabbitMQ、Silo 对象存储镜像。服务器无需源代码、Node、Python、镜像仓库账号或现场编译，也无需联网拉取镜像。HTTPS 证书申请和邮件等外部功能仍需要相应网络连接。

## 获取构建包

在本仓库的 **Releases** 中打开对应的 `Plane server <commit> (linux/amd64)`，从 Assets 下载 `plane-server-linux-amd64-<commit>.tar.gz` 和 `SHA256SUMS`。Release Notes 包含完整部署、HTTPS、维护和升级说明。

构建成功后自动发布预发布版本，标签格式为 `server-<architecture>-<commit>`，绑定本次构建提交，不覆盖其他正式版本。

当前 `feature/workflow-rules` 分支代码更新会自动构建 amd64（普通 Intel/AMD 服务器）。手动运行可选择 `arm64`（ARM 服务器）；GitHub 的手动按钮需要工作流已存在于默认分支。两种架构使用对应原生 runner，不依赖模拟器。

Release 附件不受 Actions artifact 的 7 天保留期限制。Actions 页面仍提供 `plane-server-*` ZIP 作为 7 天临时备份，下载后需先解压 ZIP；`server-image-*` 是只保留 1 天的镜像中间产物。构建不发布到上游 Docker Hub，也不需要 Docker Hub Secrets。

## 首次部署

服务器需要 Linux、Docker Engine、Docker Compose **2.24 或以上**、Bash、OpenSSL、`sha256sum`。在同一 Docker 主机上部署全部服务，建议至少 4 核 CPU、8 GB 内存及足够的镜像和数据磁盘空间。服务器架构必须与包一致；可用 `uname -m` 检查，`x86_64` 对应 amd64，`aarch64` 对应 arm64。

将本 Release 的 `.tar.gz` 和 `SHA256SUMS` 上传到服务器的同一目录，然后执行：

```bash
sha256sum --check SHA256SUMS
tar -xzf plane-server-linux-*.tar.gz
cd plane-server

# 使用 IP 和端口先部署 HTTP
./deploy.sh init http://192.0.2.10:8080
./deploy.sh up
```

也可直接使用域名启用 HTTPS：

```bash
./deploy.sh init https://plane.example.com
./deploy.sh up
```

将示例 IP/域名替换为实际地址。HTTPS 模式要求域名已解析到服务器、外部 80/443 端口可达，代理自动申请证书。若前面已有 Nginx/Caddy 等 TLS 代理，应在 `.env` 中保留外部 HTTPS 的 `WEB_URL`/`CORS_ALLOWED_ORIGINS`，将 `SITE_ADDRESS` 改为 `:80`，并配置 HTTP 映射端口和 `TRUSTED_PROXIES`；外部代理需转发原始协议及 WebSocket。

`init` 在服务器生成随机数据库、消息队列、对象存储、应用及 Live 密钥，写入权限为 600 的 `.env`。再次执行会保留已有文件。不要将 `.env` 上传到代码仓库或公开分享；构建包只包含空密钥模板。

`up` 会依次：校验镜像校验和与 CPU 架构、导入镜像、启动依赖、暂停应用、同步执行数据库迁移，迁移成功后启动应用并等待就绪。任一迁移失败则停止执行，不启动新版本应用。镜像已经打包，启动使用 `--pull never --no-build`。

部署完成后打开配置的 `WEB_URL`，首次实例设置入口是 `/god-mode/`。项目工作流默认关闭，在项目设置中配置并启用；本包的 API 已包含 `0126_project_workflows` 迁移。

## 日常操作

```bash
./deploy.sh status
./deploy.sh logs api
./deploy.sh logs worker
./deploy.sh stop

# 机器较慢时增加就绪等待时间（秒）
DEPLOY_WAIT_TIMEOUT=600 ./deploy.sh up
```

端口及其他参数在 `.env` 中修改。只暴露代理的 HTTP/HTTPS 端口，数据库、缓存、队列和对象存储不直接映射到主机端口。Compose 项目名默认为 `plane`。

`BUILD_INFO.json` 记录完整源代码提交、架构和 Actions 运行地址；`images.env` 固定全部应用的镜像标签。升级时使用同一提交编译的全部镜像，不混用上游 stable 前端或后端。

## 升级和已有数据

先备份 PostgreSQL、上传文件和 `.env`。将新包解压到新目录，**从旧部署复制 `.env`，保留相同的 `COMPOSE_PROJECT_NAME` 和 Docker 数据卷**，再执行 `./deploy.sh up`。不要用新生成的随机密码替换已有数据库、RabbitMQ 或对象存储密码。

若原有 Plane 不是用此部署包安装的，先核对旧 Compose 项目名、卷名、数据库连接及对象存储设置。项目名不同会创建另一组空卷，不能据此认为旧数据已迁移；应先按原数据卷名称或外部服务连接配置 Compose，再运行迁移。不要将本包直接指向没有备份的旧数据库进行试装。

脚本不会删除数据卷，也不会自动回退数据库迁移。镜像回滚不等于数据库回滚；迁移失败先看 `migrator` 输出并按备份与兼容性方案恢复。需要完整停机时使用 `stop`，不要使用 `docker compose down -v` 清除数据。

## 本仓库中的构建验证

```bash
bash -n deployments/server/deploy.sh
shellcheck deployments/server/deploy.sh
python3 -m unittest discover -s deployments/server/tests -v
```

完整镜像编译由 Actions 使用当前仓库的生产 Dockerfile 执行。构建 job 彼此独立；任何一个失败都不会生成最终可部署包。服务器部署动作由你执行，工作流不会连接你的服务器。
