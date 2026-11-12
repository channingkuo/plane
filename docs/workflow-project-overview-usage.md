# 工作流与项目总览增强：使用及验证

基于 `feature/workflow-rules`。需求与边界见 [Spec](./workflow-project-overview-spec.md)，开发顺序见 [Plan](./workflow-project-overview-plan.md)。

## 使用

1. 更新前后端前，运行数据库迁移：`cd apps/api && uv run python manage.py migrate`。本次新增 `db.0127_workflow_test_tasks`。现有工作流保持原配置，自动测试任务默认关闭。
2. 进入项目设置 → 工作流，选择“测试任务初始状态”。在任务分支的“开发中 → 完成开发”路径勾选“完成开发时创建测试子任务”，保存配置。路径使用状态 ID，修改状态名称不会使自动化失效。两个开发状态应属于 Started 分组，最终完成仍使用 Completed。
3. 完成该流转后，系统在当前工作项下生成 `【自动生成测试任务】原标题` 子任务。源工作项重复提交、退回后再次完成开发不会重复生成；自动生成的测试任务也不会递归生成测试任务。缺少测试初始状态的必填字段时，整个流转失败，需补齐后重试。
4. 待评审收集项同时出现在工作项布局中，可打开查看，仍通过 Intake 接收操作完成评审。展示与评审授权是两件事。
5. 项目成员进入工作项时，默认查看自己负责的工作项及全部待评审、已评审项。可通过“工作项范围”切换为全部工作项；切换布局保留选择，离开后重新进入恢复成员默认。
6. 项目导航（侧栏和页签模式）中的“总览”：管理员查看全项目；成员查看全部需求（包括待评审、已评审）和自己负责的开发、测试工作项。状态统计、总数、无日期数量和甘特图使用同一范围，不继承工作项页面的“全部工作项”切换或个人筛选。无日期项仍在列表中，可以打开后编辑。

## 自动化验证

后端使用本机独立 PostgreSQL 15.7 与 Valkey 7.2.11，锁定的 Python 依赖来自 `uv.lock`。测试消息派发使用 `AMQP_URL=memory://`，不连接外部服务。生产数据库与用户数据未参与测试。

后端定向用例：

```bash
cd apps/api
uv run python -m pytest \
  plane/tests/contract/app/test_workflow_project_overview_app.py \
  plane/tests/contract/app/test_project_workflow_app.py \
  plane/tests/unit/utils/test_workflow_rules.py \
  plane/tests/contract/app/test_issue_list_guest_scope_app.py \
  plane/tests/contract/app/test_issue_id_project_scope_app.py \
  plane/tests/contract/app/test_state_admin_scope_app.py \
  plane/tests/contract/app/test_guest_issue_subresource_scope_app.py \
  plane/tests/contract/app/test_issue_read_only_fields_app.py \
  plane/tests/contract/app/test_sub_issue_cross_project_scope_app.py
```

真实浏览器使用隔离项目及管理员、项目成员两个会话，覆盖待评审看板、逐级 ID 导航、详情页完成开发后生成测试子任务、总览时间线，以及成员五种布局默认范围和全量切换。

按 [现有 E2E 说明](../apps/web/e2e/README.md) 设置相同测试密钥、数据库、Web/API URL 并启动 API；然后在 `apps/api` 执行：

```bash
WORKFLOW_E2E_ALLOW_SEED=1 .venv/bin/python ../web/e2e/seed-overview.py
```

该命令只用于隔离测试库，创建独立工作空间并把会话写入 `/tmp/plane-overview-browser-fixture.json`；不要提交会话文件。启动生产 Web 静态资源服务后，从仓库根目录执行：

```bash
pnpm --filter web exec playwright test e2e/project-overview.spec.ts
pnpm --filter web exec vitest run --config vitest.config.ts --maxWorkers=2
pnpm --filter web check:types
pnpm --filter web build
```

构建及启动时使用相同 `VITE_API_BASE_URL`、`VITE_WEB_BASE_URL`。本次环境中仓库已有 `serve-handler` 依赖出现 `pathToRegExp.compile is not a function`，浏览器验证改用仅在本地运行的 Python 静态服务器提供相同生产构建及 SPA 回退；没有修改应用依赖。

数据库额外执行真实 `migrate`、Django `check` 和 `makemigrations --check --dry-run`。最终执行结果见 Plan 的实施结果。
