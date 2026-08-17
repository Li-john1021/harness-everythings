# 架构总览（H7）

## 1. 分层

```text
cli        命令入口、参数合同、稳定错误类别与退出码
core       canonical 序列化/指纹、实体封套与 ID、任务/批准/角色状态机、
           schema 注册表、画像与只读生命周期协调
storage    路径边界、原子写、ApplicationManifest 两步写入
adapters   运行时/工作区能力注册合同（只描述，不执行）
views      确定性 Markdown 视图（不增加 canonical 外结论）
schemas    版本化 JSON Schema（随包分发，v1/）
core       画像状态、隐私过滤、只读发现和生命周期协调
```

依赖方向：cli -> core/storage/views/adapters；storage -> core；
任何层不依赖 cli。

## 2. 写入安全模型

所有落盘唯一入口 `storage.atomic.write_atomic`：

1. `storage.paths.resolve_in_root` 规范化并验证边界（拒绝穿越/越界符号链接）。
2. canonical 字节写入同目录 `.hxtmp-` 临时文件 + fsync。
3. 新文件通过同目录 hard link 原子独占创建；已绑定旧指纹的更新通过
   `os.replace` 原子替换；失败清理临时文件。

业务写入走两步合同（`storage.manifest`）：

```text
build_manifest（dry-run，捕获前置指纹与目标指纹）
  -> 用户批准（记录清单指纹）
  -> apply_manifest（重验工作区指纹 + 独占/旧指纹门禁 + 批量回滚）
```

新目标默认独占；覆盖必须声明并匹配 `expected_before_fingerprint`。任何目标
预检失败时零写入；运行期后续写入失败时回滚本批次已写目标，回滚前再次
核对写后指纹，避免覆盖并发外部修改。

## 3. 确定性

- canonical JSON：sort_keys、ensure_ascii=False、紧凑分隔符，拒绝 NaN
  与重复键；JSON 序列化结果不受平台换行影响。
- 指纹：SHA-256，hex 带 `sha256:` 前缀；文件漂移使用原始字节指纹，
  CRLF/LF 变化会被识别为变化。
- 稳定 ID：`namespace:sha256[:16]`，由逻辑种子派生（非序号、非时间）。
- 时间：全部由调用方注入固定时钟字符串；库内无 `datetime.now()`。

## 4. H2 工作区画像与生命周期

- `core.discovery` 只读取文件系统元数据、有界文本和固定 Git 只读探针，不执行工作区脚本。
- `core.profile` 将事实、推断、用户确认、未决和证伪分离，并为每项记录来源、时间、置信度、新鲜度、敏感级别与稳定指纹。
- `core.lifecycle` 先生成画像、未决问题、权威映射和最小 Plan，再通过 ApplicationManifest 提案写入。
- Plan 未批准时不生成输出合同、领域包或角色；用户覆盖层位于独立目录，生命周期命令不写入该目录。
- 来源指纹漂移只产生 diff/reconcile 提案。`retire` 只处理注册表证明且原始哈希仍匹配的生成文件。

## 5. H1/H2 明确不包含

模型调用、Hook、异步队列、网络发布、角色自动生成、
领域生产逻辑、历史 Harness 兼容层（见 IMPLEMENTATION-PROMPT 第 6 节）。

## 6. H3 角色与上下文

- 角色生成由 `core.roles` 完成：Plan 未批准时返回 `blocked` 注册表；批准后生成完整冻结合同。稳定 `role_id` 只由角色语义种子派生，不由时间、语言、框架、扩展名或目录名派生。
- 角色生命周期由显式转换表约束，所有转换附带 `evidence_ref` 和稳定转换指纹。`reconcile_roles` 在应用前分别产出 retained、additions、conflicts、drift、merge_candidates、split_candidates、deprecations 和 lost_basis。
- 用户角色与 `.harness-everythings/roles/user/` 覆盖层只读且优先；自动生成层不会覆盖它们。所有自动生成文件通过 `generated-files` 注册表和 ApplicationManifest 管理。
- `core.context` 按显式 `authorized_role_ids`、角色名、所有者或用途筛选来源，默认拒绝无归属来源。路由只保存引用、来源指纹、敏感级别、预计 token、最大预算、失效条件和拒绝原因，不复制原始内容。
- `core.adapters` 将运行时能力与工作区能力分开。运行时完整报告 11 项能力及 serial/manual 退化；工作区分别声明 discovery、write、可选 Git，以及普通目录、非 Git、脏 Git、嵌套仓库、monorepo 和素材集合能力。
- `diff` 比较角色注册表、上下文路由和适配器状态；`reconcile` 默认 dry-run 并生成治理提案；`doctor` 校验 Schema、所有权、生命周期、上下文预算/边界和适配器退化；`status` 汇总 H3 状态；`upgrade` 复用同一 Manifest 绑定。

## 7. H3.1 批准与指纹绑定

- Plan 的 `approval_state` 只是声明，真正派生必须读取 `.harness-everythings/approvals/plan-approval.json`，校验 Plan ID、内容指纹、`work_product` 范围、目标所有者、证据引用、批准指纹和自批规则。
- Plan 内容、批准范围或目标发生变化时旧批准自动失效；应用仍由 ApplicationManifest、批准指纹和工作区前置指纹共同约束。
- doctor 会重新计算角色合同、注册表、角色对账、上下文路由和适配器状态指纹。

## 8. H4/H5 领域包

- `core.domain_packs` 提供版本化软件工程与内容脚本包，领域包未在批准 Plan 中明确启用时返回 `not-enabled`。
- H4 生成软件输出合同、未虚构引用的需求/验收追踪矩阵和 build/test/static/manual/hardware 验证记录；矩阵与验证结果按 `row_ref`/`row_kind` 对应真实 requirement 或 acceptance 行，不预先生成证据引用。Artifact 保存合同/内容/记录指纹及来源边界，Review、VerificationResult 和 work_product Approval 逐记录深绑定，只有真实记录且全部 passed 才能完成 delivery。
- H5 生成 brief、硬约束内容合同、显式 claim_refs 的变体集合和结构化审核记录；审核绑定当前 brief、唯一选中变体、实际长度、结构、平台、禁用内容、来源和 CTA，批准保存 brief/variant/review 指纹，创建批准与 external_release 批准分离。外部发布批准可以在作品批准后单独追加，`doctor/status` 会持续复核其决策、所有者、证据和作品批准绑定。
- H4/H5 的 `submit_*_workflow` 只生成 dry-run ApplicationManifest；应用前再次校验 Plan/brief/contract、所有记录指纹和工作区前置指纹，旧内容或审核变化会使批准回到待审核状态。
- 所有领域生成记录通过 ApplicationManifest dry-run 写入，外部发布批准不由软件交付或内容创作批准隐式授予。

## 9. H6/H7 证据与门禁

- Artifact/Evidence 使用内容指纹；checkpoint/handoff 保存未完成项和恢复前置条件；评价默认未消费，只有人工 user 动作才能消费。
- H6 将 Artifact、Evidence、Evaluation 的追加事件与 generated ledger 视图分离；`append_h6_events` 只追加事件，reconcile 从事件确定性投影视图，不清空已有记录。Checkpoint/Handoff 由最新事件派生并保存自身指纹，未消费 Evaluation 不得宣称完成，`no-change` 治理效果显式落盘。
- H7 对抗 fixture 覆盖密钥、个人信息、prompt injection、恶意/冲突指令、上下文泄漏、预算膨胀、路径与压缩包穿越，并执行三次完整 canonical replay。Windows 全量已复验；本机无独立打包工具，WSL 仅 Python 3.8.10 且无 pytest，POSIX 和无 Python 独立包保持未验证，结论为 `revise`。

## 10. 领域包与写入边界

- `domain_packs/<pack-id>/pack.json` 是独立版本化资源；`domain_pack_loader.py` 只做通用边界、Schema、版本、引用和指纹校验，不在内核复制角色、阶段或 validator 定义。未知、越界、符号链接、不解析的 `$ref` 和不兼容 pack 一律拒绝；normal-closure 与 controlled-failure-recovery 由确定性阶段轨迹执行器验证。
- 每个 pack 同时提供 `output-contract.schema.json`、`stages.json`、`roles/`、`validators/`、`context-routes.json` 和 `fixtures/`；loader 读取并交叉比对这些物理资源，pack manifest 不能单独替代合同目录。
- `storage.atomic.write_lock()` 覆盖 manifest 预检、目标写入和回滚；已有文件的 `expected_before_fingerprint` 传递到原子替换边界并在替换前重新读取。retire 在删除前再次核对生成清单哈希，用户修改不会被静默删除。
