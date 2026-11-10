# 项目工作流实现与验证记录

- 实施日期：2026-11-10；分支：`feature/workflow-rules`。
- 实施基线：`6f0f8e944fa2727f947e9436294146a5d2e5c84b`。开始时已 fetch 并核对与远端无领先或落后，未回退至 Plan 的旧基线。
- 需求：[Spec](./project-workflow-spec.md)；阶段清单：[Plan](./project-workflow-plan.md)；操作说明：[使用说明](./project-workflow-usage.md)。
- 本地环境：Python 3.12.14、uv 0.12.21、Node 24.22.1、pnpm 11.25.0。仓库声明 Node 至少 22.22.0、pnpm 11.10.0；CI 沿用仓库版本文件及 Corepack，未改写这些版本约定。

## 实现结构

新增 `db.0126_project_workflows`，包含每项目配置、分支路径、目标字段条件、可信工作项上下文、聚合版本及同步审计模型。没有改写已有迁移，也没有推断历史评审事实。

`services/workflow/` 的职责如下：

| 模块                       | 职责                                                       |
| -------------------------- | ---------------------------------------------------------- |
| `rules.py`                 | 纯规则：状态、角色、路径、字段及日期校验                   |
| `configuration.py`         | 工作空间配置权限、实际项目角色、版本、配置保存和兼容性校验 |
| `adapters.py`、`intake.py` | 现有内部/公开 Serializer 适配、Intake 提交和幂等接收       |
| `execution.py`             | 认证后进入项目锁和事务、写入前后验证、全部后代聚合及审计   |
| `batch.py`                 | 批量日期和属性预检、整批持久化、错误项定位                 |
| `queries.py`               | 只读能力查询及预检，复用实际执行的规则                     |
| `tasks.py`                 | 提交后派发任务，不将异步活动当作事实记录                   |
| `seeding.py`               | 新建示例项目事务内记录可信来源，不用于历史回填             |

HTTP 事务适配器只包装已枚举的变更视图：先完成原 DRF 认证及权限，再锁项目，调用原处理器，校验最终工作项树并聚合。配置保存使用自己的项目锁服务。链接、反应和其他无关资源保留原处理路径。Space 使用 anchor 解析项目；工作空间草稿入口按 ID 顺序锁相关项目。后台自动关闭和归档显式使用相同事务服务。

批量属性在所有 Serializer 验证完成后才保存；对整批中的父子完成约束使用最终树校验，允许在同一批内完成父项和其全部后代。仅该服务推迟前置的后代检查，角色、路径、字段及事务后的后代校验始终执行，没有客户端绕过参数。

普通任务可与 Intake 提交复用状态 ID，但可信业务分支保持不变。非 Triage 待评审项通过正式工作项 Manager 从正式读取路径隔离，来源不会从状态、类型或标签反推。

## 写入入口核对

路径相对 `apps/api/plane/`。实际受控 HTTP 类名集中登记在 `execution.WORKFLOW_MUTATION_VIEWS`，后续新增入口必须补登记或显式服务接入。

| 实际入口                                                                     | 接入和边界                                                                                                                                                                                | 测试证据                                           |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `app/views/issue/base.py`：`IssueViewSet` 创建/修改/删除，批量日期、批量删除 | 原 Serializer 权限与统一项目事务；日期合并显式 null；整批失败回滚                                                                                                                         | 初始状态、字段/M2M、批量日期、来源及删除测试       |
| `app/views/workflow.py`：`WorkflowBulkUpdateEndpoint`                        | 补齐客户端已有但 CE 未注册的属性路由；启用项目最多 500 项，全部预检；模块/周期组合明确拒绝；关闭项目保持原 404                                                                            | 两个批量属性合约测试                               |
| `api/views/issue.py`：公开创建/修改/删除                                     | Token 的实际用户身份、公开字段别名、原资源范围和相同事务                                                                                                                                  | 公开 API 工作流测试及既有权限回归                  |
| `app/views/intake/base.py`、`api/views/intake.py`、`space/views/intake.py`   | 配置提交状态、原 Issue 接收、可信上下文、真实项目管理员、嵌套修改校验                                                                                                                     | Intake 接收/并发、非 Triage、Space、未接收父项测试 |
| `app/views/workspace/draft.py`：草稿发布                                     | 草稿保存不改变正式规则；发布重新校验目标项目、初始状态和字段，失败保留草稿                                                                                                                | `test_draft_publication_preserves_failed_draft`    |
| `app/views/issue/sub_issue.py` 及 Serializer 的 parent 修改                  | 同项目、无环、不能向未接收需求拆分；重算新旧父链                                                                                                                                          | 循环、未接收父项、嵌套、换父项测试                 |
| `app/views/issue/archive.py`：单项和批量归档/取消归档                        | 归档后代仍参与聚合                                                                                                                                                                        | 归档取消子项测试与既有归档回归                     |
| `app/views/state/base.py`、`api/views/state.py`                              | 同项目锁；配置引用删除和非法分组拒绝；合法分组变更同步聚合及完成时间                                                                                                                      | 改名/非法分组、合法分组重算测试                    |
| `bgtasks/issue_automation_task.py`                                           | 实际执行人、路径和目标字段校验；失败跳过并审计；聚合无通用豁免                                                                                                                            | 自动化 actor/路径合约测试                          |
| `bgtasks/dummy_data_task.py`、`workspace_seed_task.py`                       | 只创建新项目；整个新项目构建在事务中，结束时写可信来源                                                                                                                                    | 完整既有回归；不作为历史迁移机制                   |
| 导入、工作项复制、跨项目移动、软删除恢复                                     | 当前 CE 未注册工作项导入/移动/复制/恢复路由；名称为 importer 的 external 路由实际为 AI 助手，不写工作项。草稿选择目标项目已单独接入；不能因客户端代码或通用恢复函数存在就声称具备这些 API | 路由及 ORM 静态核对；未来新增这些入口必须重新接入  |
| 活动/版本同步、序号修复命令、资产复制                                        | 活动仅更新工作项 `updated_at`；版本模型不是业务 Issue；序号修复不改受控字段；资产复制不创建工作项                                                                                         | 静态核对及原测试回归                               |

未把直接数据库写入、运维脚本或未来扩展入口宣称为自动受保护。Spec 的业务写入范围由上述真实入口和显式服务覆盖。

## AC 证据映射

下表测试函数位于 `apps/api/plane/tests/contract/app/test_project_workflow_app.py`，省略 `test_` 前缀；纯规则文件为 `tests/unit/utils/test_workflow_rules.py`。浏览器文件为 `apps/web/e2e/workflow.spec.ts`，组件及 store 测试位于其实现旁。

| AC     | 主要自动化证据                                                                                                                                                                                  |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01、02 | `configuration_round_trip_and_stale_revision`、`project_admin_cannot_edit_configuration`、`workspace_admin_need_not_join_project`、`cross_project_state_reference_rejected`；配置页组件权限测试 |
| 03、04 | `intake_acceptance_on_same_issue_and_admin_boundary`，验证原 Issue、接收事实和真实项目角色                                                                                                      |
| 05     | 上述重复接收断言及 `concurrent_acceptance_keeps_one_receipt`                                                                                                                                    |
| 06     | `initial_state_and_durable_source`、`public_api_uses_initial_state_and_shared_transition_rules`、`draft_publication_preserves_failed_draft`；子项与拖拽测试使用同一创建约束                     |
| 07     | `non_triage_intake_submission_remains_outside_formal_queries`                                                                                                                                   |
| 08     | `shared_state_does_not_convert_task_into_reviewed_requirement`、Intake 接收测试、关闭/重新启用测试                                                                                              |
| 09     | `pending_parent_and_parent_cycle_are_rejected`、接收后子项创建和聚合测试                                                                                                                        |
| 10、11 | 纯规则路径/角色矩阵、公开 API、`member_can_trigger_parent_reopen_without_parent_manual_permission`；拖拽预检和服务端权限变化浏览器测试                                                          |
| 12、13 | `final_fields_and_failed_m2m_update_roll_back`、`suspended_assignee_must_be_repaired_before_transition`、只读预检、批量日期测试；浏览器同一次提交补齐字段                                       |
| 14、22 | `parent_completion_reopening_and_nested_descendants`、`concurrent_last_children_complete_only_once`；真实 PostgreSQL 独立连接和同步屏障                                                         |
| 15、16 | 归档 Cancelled 后代测试、嵌套后代测试；浏览器拒绝强制完成并展示可读子项链接                                                                                                                     |
| 17     | `empty_requirement_needs_explicit_admin_transition`；浏览器取消和确认空需求完成                                                                                                                 |
| 18、19 | `member_can_trigger_parent_reopen_without_parent_manual_permission`、`automatic_reopen_missing_fields_rolls_back_child`                                                                         |
| 20     | `automatic_completion_blocked_until_parent_fields_filled`；详情受阻摘要与补字段操作                                                                                                             |
| 21     | `parent_completion_reopening_and_nested_descendants`                                                                                                                                            |
| 23     | `batch_dates_failure_is_atomic`、`batch_state_updates_validate_all_items_and_commit_final_tree`，含提交后活动签名校验                                                                           |
| 24     | 公开 API、Space、`automation_obeys_real_actor_role_and_configured_paths`、`batch_unsupported_and_foreign_ids_are_rejected_without_writes`                                                       |
| 25     | `state_rename_preserves_binding_and_group_change_is_guarded`、`legal_group_change_recomputes_parent`                                                                                            |
| 26     | `archived_cancelled_descendant_does_not_count_as_completed`、`reparent_recalculates_both_parents_and_delete_does_not_complete_empty_parent`                                                     |
| 27     | `base-issues.workflow.test.ts` 的状态、列表、计数、空日期和删除失败恢复；浏览器真实 403 后恢复状态，拖拽取消不提交                                                                              |
| 28     | `concurrent_configuration_save_rejects_stale_writer`；组件及浏览器 409 保留输入                                                                                                                 |
| 29     | `workflow_disable_keeps_provenance_and_restore_requires_validation`；完整原有回归测试                                                                                                           |
| 30     | `manual_reopen_survives_unrelated_edit`、`manual_reopen_is_not_reversed_by_switching_between_completed_states`                                                                                  |

映射表示已保留具体证据，不表示穷举所有 UI 布局、浏览器、数据规模及每个入口的所有组合。Space 提交通过真实 API 合约验证，没有单独启动 Space 前端浏览器。

## 运行与结果

使用本机独立 Docker PostgreSQL 15.7、Valkey 7.2.11、RabbitMQ 3.13.6 及 Silo 测试存储，API 测试运行于锁定的 uv 虚拟环境，未使用生产数据。默认 pytest 的 `--nomigrations` 之外，另外执行真实迁移。

| 检查                                                | 结果                                                                        |
| --------------------------------------------------- | --------------------------------------------------------------------------- |
| 完整 API 回归                                       | **1543 passed**，包含新增批量属性及 PostgreSQL 并发测试                     |
| 最终工作流、关系读取及权限定向回归                  | **109 passed**；覆盖最后补充的非 Triage 正式列表、展开关联和关系入口隔离    |
| 工作流规则与接口单独回归                            | 54 passed（18 规则、36 接口），也包含在上述定向回归内                       |
| Web 组件及 store 测试                               | **8 passed**                                                                |
| 共享 `@plane/services` 测试                         | **13 passed**                                                               |
| 真实 Web + API + Chromium                           | **5 passed**：配置/版本、父子聚合、字段补齐、空需求确认、权限变化回滚和拖拽 |
| 空数据库真实迁移                                    | 全部迁移及 `db.0126_project_workflows` 成功                                 |
| 0125 基线数据升级到 0126                            | 原工作项保留、没有历史来源回填、配置默认关闭，断言通过                      |
| Django `check` / `makemigrations --check --dry-run` | 无问题 / 无模型漂移                                                         |
| API Ruff                                            | 本次全部新增、修改 Python 文件通过                                          |
| Web 构建                                            | 成功生成 `build/client/index.html`                                          |
| Web、types、constants、i18n 类型和 lint             | 17 个 Turbo 任务成功；lint 沿用仓库警告额度，零错误                         |
| Web、types、constants、i18n 格式检查                | 4 个包全部通过；新增文档和 CI YAML 另行格式检查通过                         |

后端首轮完整回归的提交后通知断言、测试存储环境以及认证前事务介入问题均已修复，并经过原失败集合和完整回归验证。测试副作用捕获现在发生在提交后，不在生产代码中为测试提前发送消息。

关键复现命令：

```bash
# 在独立测试服务及 apps/api 环境变量准备完成后
cd apps/api
uv run --no-sync pytest
uv run --no-sync pytest plane/tests/contract/app/test_project_workflow_app.py plane/tests/unit/utils/test_workflow_rules.py
uv run --no-sync python manage.py check
uv run --no-sync python manage.py makemigrations --check --dry-run

# 仓库根目录
pnpm turbo run build --filter='web^...'
pnpm turbo run check:types check:lint --filter=web --filter=@plane/types --filter=@plane/constants --filter=@plane/i18n
pnpm --filter web test
pnpm --filter web build
pnpm --filter @plane/services test
pnpm --filter web test:e2e
```

浏览器启动、隔离数据及固定测试密钥见 [E2E README](../apps/web/e2e/README.md)。新增 `.github/workflows/project-workflow-tests.yml` 在 PR 或手动触发时运行真实迁移、完整 API、Web 检查/构建和浏览器测试；本次只在本地执行验证，没有声称 GitHub Actions 已运行。

保留的运行边界：同项目写入串行、项目树快照随项目规模增长，尚未进行规模吞吐及锁等待压测。Web 现有工具链会输出 Vite 配置及 Tailwind 模块类型提示；lint 使用仓库原有警告额度，并非整个仓库零警告。迁移和默认关闭策略不等于已经部署到任何外部环境。
