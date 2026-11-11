# Plane 服务器离线部署包

这个包包含当前提交编译的 Web、Space、Admin、Live、API/Worker 和代理镜像，以及 PostgreSQL、Valkey、RabbitMQ、Silo 对象存储镜像。服务器无需源代码、Node、Python、镜像仓库账号或现场编译，也无需联网拉取镜像。HTTPS 证书申请和邮件等外部功能仍需要相应网络连接。

## 获取构建包

在本仓库的 **Releases** 中打开对应的版本，例如 `v1.5.0-XMW`，从 Assets 下载 `plane-server-linux-amd64-v1.5.0-XMW.tar.gz` 和 `SHA256SUMS`。Release 标题只使用版本号，CPU 架构见附件名称和 Release Notes；Release Notes 包含完整部署、HTTPS、维护和升级说明。

每个版本只保留一个预发布 Release，固定标签为 `server-<version>-XMW`，例如 `server-v1.5.0-XMW`。同版本再次构建成功后，覆盖对应架构的同名附件、更新说明和源代码提交，下载地址保持不变。其他架构的最新附件会保留在同一个 Release 中，`SHA256SUMS` 覆盖当前可下载的所有架构。

发布完成后自动删除该版本旧的、带架构或提交后缀的重复 Release；其他版本和普通 Git 标签保留。只有新附件上传并验证成功后才清理旧 Release；构建失败不修改既有 Release，发布失败不清理旧的重复 Release。应用镜像标签仍使用真实版本号加 `-XMW`；本次构建的确切代码见 Release Notes 和包内 `BUILD_INFO.json`。

当前 `feature/workflow-rules` 分支代码更新会自动构建 amd64（普通 Intel/AMD 服务器）。手动运行可选择 `arm64`（ARM 服务器）；GitHub 的手动按钮需要工作流已存在于默认分支。两种架构使用对应原生 runner，不依赖模拟器。

Release 附件不受 Actions artifact 的 7 天保留期限制。Actions 页面仍提供 `plane-server-*` ZIP 作为 7 天临时备份，下载后需先解压 ZIP；`server-image-*` 是只保留 1 天的镜像中间产物。构建不发布到上游 Docker Hub，也不需要 Docker Hub Secrets。

## 首次部署

服务器需要 Linux、Docker Engine、Docker Compose **2.24 或以上**、Bash、OpenSSL、`sha256sum`。在同一 Docker 主机上部署全部服务，建议至少 4 核 CPU、8 GB 内存及足够的镜像和数据磁盘空间。服务器架构必须与包一致；可用 `uname -m` 检查，`x86_64` 对应 amd64，`aarch64` 对应 arm64。

将本 Release 的 `.tar.gz` 和 `SHA256SUMS` 上传到服务器的同一目录，然后执行：

```bash
sha256sum --check --ignore-missing SHA256SUMS
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

`BUILD_INFO.json` 记录应用版本、完整源代码提交、架构和 Actions 运行地址；`images.env` 固定全部应用的镜像标签。升级时使用同一提交编译的全部镜像，不混用上游 stable 前端或后端。

## 镜像名称和版本

六个应用镜像的名称固定为 `plane-selfhost/admin`、`plane-selfhost/backend`、`plane-selfhost/live`、`plane-selfhost/proxy`、`plane-selfhost/space`、`plane-selfhost/web`。打包脚本读取仓库根目录 `package.json` 中的真实版本号，统一添加 `v` 前缀和 `-XMW` 后缀，例如：

```text
plane-selfhost/admin:v1.5.0-XMW
plane-selfhost/backend:v1.5.0-XMW
plane-selfhost/live:v1.5.0-XMW
plane-selfhost/proxy:v1.5.0-XMW
plane-selfhost/space:v1.5.0-XMW
plane-selfhost/web:v1.5.0-XMW
```

当源码版本更新为 `1.5.1`、`1.5.2` 时，下一次构建会自动使用 `v1.5.1-XMW`、`v1.5.2-XMW`，无需手工修改六个镜像标签。仅修改代码而未升级源码版本时，重建仍使用相同标签；`docker load` 会更新本机该标签指向的镜像，通过 `BUILD_INFO.json` 和校验和区分具体构建。需要保留历史构建时，请同时保留原离线包。

`./deploy.sh up` 会按新包的 `images.env` 加载并启动这些镜像。旧包使用哈希标签，只有使用新构建的离线包后才会采用新标签；不要只修改旧包的 `images.env`，否则它与镜像归档内的标签不一致。

### 服务器上哪些镜像需要保留

| 镜像                                                                                                               | 此部署包是否需要 | 用途                                                            |
| ------------------------------------------------------------------------------------------------------------------ | ---------------- | --------------------------------------------------------------- |
| 六个 `plane-selfhost/*:<version>-XMW`                                                                              | 需要             | 当前应用服务；API、Worker、Beat 和迁移共用 backend 镜像         |
| `makeplane/plane-admin`、`plane-backend`、`plane-frontend`、`plane-live`、`plane-proxy`、`plane-space` 的 `v1.4.3` | 不依赖           | 旧版官方应用镜像，不是此部署包的运行依赖                        |
| `postgres:15.7-alpine`                                                                                             | 默认需要         | PostgreSQL 数据库                                               |
| `valkey/valkey:7.2.11-alpine`                                                                                      | 默认需要         | 缓存及 Redis 兼容服务                                           |
| `rabbitmq:3.13.6-management-alpine`                                                                                | 默认需要         | 后台任务消息队列                                                |
| `pgsty/silo:RELEASE.2026-09-16T00-00-00Z`                                                                          | 默认需要         | S3 兼容对象存储，保存上传附件；Compose 服务仍名为 `plane-minio` |

默认 Compose 会启动以上四种依赖服务。仅在已改好 Compose 及应用连接配置、实际使用外部服务时，才可能不再需要对应本地服务镜像。

清理旧应用镜像前，先检查包括已停止容器在内的引用：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}'
```

确认没有容器依赖且无需保留它们回滚后，可以用 `docker image rm <完整镜像名:标签>` 删除旧的官方应用镜像或旧哈希标签。部署脚本不会自动清理旧镜像；不要使用强制删除或清理数据卷的命令。

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
