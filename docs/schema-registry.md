# Schema 注册表（H7）

版本：H7 修补复验（2026-08-17）

本轮扩展至 48 个 v1 Schema：领域包清单、软件输出合同、追踪矩阵、验证结果、软件 review/delivery state、内容 brief/合同/变体/审核、H6 ledger、评价、治理效果和 checkpoint 均为严格 `additionalProperties=false` 合同；领域包清单同时冻结 capabilities/context_routes，资源本身位于独立版本化 pack 目录并由通用 loader 校验；新增 Artifact/Evidence/Verification binding、append-only event ledger 和 content delivery state 合同。`doctor-schemas` 实际退出码 0，加载 48/48。

## 1. 版本

| schema_version | 存储目录 | 状态 |
|---|---|---|
| 1.0 | `src/harness_everythings/schemas/v1/` | 当前版本 |

迁移：`1.0` 内暂无迁移；未来 `1.x` 通过 `register_migration` 注册，
回退必须显式 `allow_reverse=True` 并记录 ADR。

## 2. 实体清单（22 类）

| 实体 | schema 文件 | 稳定标识 | 必需字段要点 |
|---|---|---|---|
| Workspace | workspace.schema.json | `entity_id`（namespace `workspace`） | 名称、kind、生命周期、启用领域包、配置版本 |
| ProfileRecord | profile-record.schema.json | `entity_id` | status 五态、sensitivity 六级、fact_key/value、可选 confidence/freshness |
| Plan | plan.schema.json | `entity_id` | goals/scope/decisions/risks/stages/acceptance_strategy/approval_state |
| OutputContract | output-contract.schema.json | `entity_id` | derived_from_plan、可观察要求、验收条件 |
| Role | role.schema.json | `role_id`（与 `entity_id` 相同） | 独立合同版本、使命/权限、owns/forbids、能力、输入输出引用、产物/证据义务、验证、停止条件、依赖、并发边界、来源、生命周期历史 |
| Task | task.schema.json | `entity_id` | canonical 十态（含 paused）、owner_role、budget、retry_policy 三类、幂等键、transitions |
| Artifact | artifact.schema.json | `entity_id` | 八种产物类型、内容指纹、敏感等级 |
| Evidence | evidence.schema.json | `entity_id` | actor/action、结论五类、支撑引用、验证七级 |
| Approval | approval.schema.json | `entity_id` | scope 两态分离（work_product/external_release）、决策三态 |
| Handoff | handoff.schema.json | `entity_id` | checkpoint、未完成项、恢复前提、接手者 |
| GovernanceProposal | governance-proposal.schema.json | `entity_id` | 提案、证据引用、风险、回退计划、批准状态 |
| ApplicationManifest | application-manifest.schema.json | 清单指纹 | 工作区前置指纹、包含 payload 的幂等键、逐项写入（rel/exclusive/expected_before_fingerprint/target_fingerprint） |
| WorkspaceProfile | workspace-profile.schema.json | workspace_ref + source_fingerprint | 画像记录集合、来源指纹和生成时间 |
| UnresolvedItems | unresolved.schema.json | workspace_ref + source_fingerprint | 未决问题和证据引用 |
| AuthorityMap | authority-map.schema.json | workspace_ref + source_fingerprint | trusted/candidate/untrusted_content 与用户决策门禁 |
| GeneratedFiles | generated-files.schema.json | registry_fingerprint | 可 retire 的生成文件及其原始哈希 |
| RoleRegistry | role-registry.schema.json | source_fingerprint + role IDs | 严格嵌入完整 Role 合同；Plan 未批准时为 `blocked` |
| RoleReconciliation | role-reconciliation.schema.json | fingerprint | retained/additions/conflicts/drift/merge/split/deprecations/lost-basis 及稳定依据 |
| ContextRoutes | context-routes.schema.json | routing_fingerprint | 角色专属来源引用/指纹、敏感级别、预计 token、总预算、失效条件和拒绝原因 |
| ContextRoute | context-route.schema.json | route_id | 单角色上下文包的 owner、purpose、预算和拒绝来源合同 |
| AdapterContract | adapter-contract.schema.json | adapter_id | 运行时/工作区能力、缺失能力退化、状态/证据/批准边界；工作区另列 discovery/write/Git/目录类型 |
| AdapterState | adapter-state.schema.json | state_fingerprint | 运行时适配器与工作区适配器的已校验组合状态 |

## 2.1 H4-H6 新增记录

`domain-pack-manifest`、`software-output-contract`、`traceability`、`verification-result`、`verification-results`、`software-review`、`software-delivery-state`、`content-brief`、`content-output-contract`、`content-variant`、`content-variants`、`content-review`、`content-review-check`、`content-delivery-state`、`artifact-ledger`、`evidence-ledger`、`evaluation-ledger`、`evaluation`、`artifact-binding`、`evidence-binding`、`verification-binding`、`artifact-events`、`evidence-events`、`evaluation-events`、`governance-effect` 和 `checkpoint` 已加入 CLI `doctor-schemas`，生成器会在落盘前实际调用对应校验。`verification_matrix`/`verification-result` 使用 `row_ref` 与 `row_kind` 覆盖 requirement 和 acceptance 两类真实追踪行；`handoff` 强制保存内容指纹；H4/H5 的 Artifact、Evidence、Review、VerificationResult、Approval 和 delivery 记录在 workflow 落盘前逐项重验。领域包 `$ref` 必须解析到 pack 边界内的真实 schema；normal-closure 和 controlled-failure-recovery fixture 通过可执行阶段轨迹验证失败、not_run 和恢复结果。

## 3. 公共封套字段

所有实体（ApplicationManifest 除外，其为运行时结构）必带：
`schema_version`、`entity_id`、`entity_type`、`created_at`、`updated_at`、`source_ref`。

## 4. 校验器说明

内建最小校验（无第三方依赖）：type/required/enum/const/additionalProperties、minLength/maxLength、minItems/maxItems 和 pattern；关键批准边界的非法指纹、空目标和空证据列表会被拒绝。
不支持 JSON Schema 全集；schema 文件中的高级关键字不参与运行时校验。

## 5. 变更纪律

- 新增字段：次要版本（1.x），旧记录必须仍可校验通过或提供迁移。
- 破坏性变更：主版本（2.0），必须注册迁移路径并更新本表。

## 6. H2/H3 运行时检查

- `doctor-schemas` 当前加载 48 个 v1 Schema。
- Profile bundle 中的每条 `ProfileRecord` 另行按 `profile-record.schema.json` 校验。
- `generated-files` 的注册表指纹必须匹配其内容；`retire` 在删除前还会重新验证每个目标文件哈希和工作区前置指纹。
- ApplicationManifest 可声明 `snapshot_workspace` 与 `snapshot_excludes`，用于 init/reconcile 的来源漂移门禁。
- 角色注册表、角色对账、上下文路由和适配器状态是严格提案/索引合同；未通过 Plan 批准时不落盘派生角色、路由或适配器状态。
- `context-routes` 通过 `$ref` 校验每个 `context-route`；路由只含引用和指纹，不嵌入原始内容，且每条路由的 `estimated_tokens <= max_token_budget`。
- 领域包 manifest 之外，loader 还要求并交叉比对 `output-contract.schema.json`、`stages.json`、`roles/roles.json`、`validators/validators.json`、`context-routes.json` 和 `fixtures/`，缺失、符号链接、内容不一致或越界资源拒绝加载。
