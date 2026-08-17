# 验收追踪矩阵

版本：H7 修补复验（2026-08-17）

验证等级定义见产品 SPEC.md 第 12 节（1 已设计 … 7 外部独立复现）。
每项标注当前最高已证明等级；不得把合成测试描述成生产验证。

## 1. H1 退出门禁（保留证据）

| # | 门禁要求 | 证据（测试/命令） | 最高验证等级 |
|---|---|---|---|
| 1 | 固定输入与时钟产生相同 canonical 字节和指纹 | `tests/unit/test_identity.py::TestCanonicalJson`（键序、往返、NaN/重复键拒绝；原始字节可识别 CRLF/LF 漂移） | 3 单元测试通过 |
| 2 | 无效 Schema、未知版本和非法状态变化被稳定拒绝 | `test_schema_and_views.py::TestSchemaRegistry`、`test_state_machine.py::TestTaskTransitions`、`test_approvals_roles.py` | 3 单元测试通过 |
| 3 | 中断写入不破坏旧数据 | `test_storage.py::TestAtomicWrite`、`test_two_step_write.py::test_runtime_failure_rolls_back_prior_write`（原子独占、批量失败回滚） | 3 单元测试通过 |
| 4 | Windows/POSIX、空格、UTF-8 和非 ASCII 路径覆盖 | `test_storage.py::TestNormalizeRelPath/TestResolveInRoot`、`TestAtomicWrite::test_non_ascii_content`、`test_two_step_write.py`（中文目录/文件名） | 3 单元测试通过（Windows 实测；POSIX CI 待 H7） |
| 5 | 路径穿越、越界符号链接和用户文件覆盖被拒绝 | `TestResolveInRoot::test_symlink_escape_rejected`、`TestPathEscapes`、`TestFixtureByteStability` | 3 单元测试通过 |
| 6 | dry-run 不产生工作区副作用 | `test_two_step_write.py::TestTwoStepWriteFlow::test_dry_run_is_default_safe`、`test_full_flow` 第 1 步 | 3 单元测试通过 |
| 7 | fixture 运行前后逐字节一致 | `TestFixtureByteStability::test_fixture_unchanged_after_all_operations` | 3 单元测试通过 |
| 8 | 未预装 Python 用户路径有方案；未获批不安装 | 本仓库无安装动作；方案见 `docs/python-install-plan.md`（D-004 合同）；独立可执行包验证归 H7 | 1 已设计 |
| 9 | 上下文与依赖扫描无演进案例或历史表面 | `docs/decisions/clean-room-ledger.md` 第 5、6 节；依赖仅标准库 + pytest | 2 已实现（人工自查） |

## 2. H2 退出门禁

| # | 门禁要求 | 证据 | 最高验证等级 |
|---|---|---|---|
| 1 | 五态画像分离观察、推断、用户决定、未决和证伪 | `tests/integration/test_h2_lifecycle.py::TestH2ProfileAndSafety::test_five_states_and_fingerprints` | 3 单元测试通过 |
| 2 | 每项画像记录来源、时间、confidence、freshness、sensitivity 和稳定指纹 | 同上；`profile.py::make_profile_record` | 3 单元测试通过 |
| 3 | 密钥、邮箱、绝对路径在摘要前过滤 | `test_secret_private_and_absolute_path_filtering` | 4 fixture 验证通过 |
| 4 | init new 默认 dry-run，Plan 未批准不生成合同、领域包和角色 | `TestH2Init::test_new_is_dry_run_and_plan_blocks_derivatives` | 4 fixture 验证通过 |
| 5 | init existing 只读发现 Git/脏状态、拓扑和风险，不执行工作区脚本 | `test_existing_is_read_only_and_detects_git_dirty`、`test_nested_repository_and_monorepo_signals` | 4 fixture 验证通过 |
| 6 | 重复初始化幂等，用户覆盖层保持不变 | `test_repeat_init_is_idempotent`、`test_non_ascii_existing_and_user_overlay_survive` | 4 fixture 验证通过 |
| 7 | 来源漂移生成 reconcile 提案，不静默覆盖 | `test_apply_rejects_source_change_and_reconcile_is_dry_run` | 4 fixture 验证通过 |
| 8 | apply 绑定批准指纹和工作区前置指纹 | `test_apply_before_workspace_change_is_rejected` | 4 fixture 验证通过 |
| 9 | 恶意/冲突指令、未知领域和敏感文件升级为未决风险 | `test_untrusted_or_unknown_instructions_are_unresolved` | 4 fixture 验证通过 |
| 10 | retire 只删除注册表证明且哈希匹配的生成内容 | `test_retire_requires_matching_generated_hash`、`test_retire_only_removes_proven_generated_files` | 4 fixture 验证通过 |
| 11 | 生命周期命令面可调用，Schema 自检通过 | CLI contract tests、`doctor-schemas`（48/48） | 3 单元测试通过 |

## 3. 测试命令与结果（2026-08-16，Windows 11）

```text
命令：Python 3.14.3 `python -m pytest`                退出码 0，170 passed，0 failed
命令：Python 3.11.15 `.venv311\\Scripts\\python -m pytest` 退出码 0，170 passed，0 failed
命令：`.venv311\\Scripts\\harness-everythings.exe --version` 退出码 0，输出 0.1.0
命令：`.venv311\\Scripts\\harness-everythings.exe doctor-schemas` 退出码 0，{"ok": true, "schemas_checked": 48}
命令：`.venv311\\Scripts\\harness-everythings.exe status --workspace <未初始化临时目录>` 退出码 0，`initialized=false`
命令：Python 3.11.15 `.venv311\\Scripts\\python -m pip check` 退出码 0，无损坏依赖
命令：H2 `tests/integration/test_h2_lifecycle.py` 退出码 0，13 passed，0 failed
命令：H3 `tests/unit/test_h3_roles_context.py` 退出码 0，11 passed，0 failed（Python 3.11/3.14）
命令：H3 `tests/integration/test_h3_lifecycle.py` 退出码 0，5 passed，0 failed（Python 3.11）
命令：空目录 `init new --dry-run` 退出码 0，写入文件数 0
```

开发依赖安装（仅开发端）：`python -m pip install pytest`、
`python -m pip install -e .`。未修改系统环境，未提权。

## 4. V1 验收标准追踪（SPEC 第 18 节）

| V1 条款 | 当前状态 |
|---|---|
| 1 schema 校验 + 版本迁移测试 | 校验已实现；迁移测试就绪（1.0 暂无迁移路径，机制已测） |
| 2-5 init/幂等/漂移 | H2 已实现并通过 fixture；用户批准流程仍需真实工作流确认 |
| 6 角色冲突发现 | H3 已实现角色冲突、失去依据和用户覆盖报告 |
| 7-8 领域包闭环 | H4/H5 已通过软件工程与内容脚本合成 fixture；真实用户生产工作流仍未验证 |
| 9 无子代理退化运行 | H3 已实现 serial/manual 退化合同 |
| 10 恶意输入拒绝 | H1 覆盖路径逃逸/用户文件；其余 H7 |
| 11 上下文不加载演进史 | H3 上下文路由拒绝历史/演进来源；真实运行时审计归 H7 |
| 12 clean-room 审核 | 账本建立；独立对抗审核归 H7 |
| 13 独立可执行包 | H7 |

## 5. H3 退出门禁

| # | 门禁要求 | 证据 | 最高验证等级 |
|---|---|---|---|
| 1 | Plan 未批准不生成角色及派生 H3 状态 | `test_plan_approval_blocks_role_generation`、`test_reconcile_is_dry_run_then_applies_all_h3_records` | 4 fixture 验证通过 |
| 2 | 角色冻结合同字段完整、Schema 严格且 role_id 稳定 | `test_role_contract_is_complete_and_stable`、`test_role_schema_rejects_incomplete_contract` | 3 单元测试通过 |
| 3 | 角色生命周期合法转换通过，非法转换拒绝 | `test_role_lifecycle_accepts_only_declared_transitions` | 3 单元测试通过 |
| 4 | 对账完整报告 retained/additions/conflicts/drift/merge/split/deprecation/lost-basis | `test_reconcile_reports_all_categories_and_basis` | 3 单元测试通过 |
| 5 | 用户角色/覆盖层优先且逐字节不变 | `test_reconcile_apply_is_repeatable_and_user_overlay_wins` | 4 fixture 验证通过 |
| 6 | 上下文按角色授权隔离，历史/私有/无关来源默认拒绝 | `test_routes_are_role_specific_and_default_deny`、`test_routes_reject_private_history_and_invalid_budgets` | 3 单元测试通过 |
| 7 | 上下文总预算、非法预算和输入顺序确定性通过 | `test_route_budget_and_canonical_order_are_stable` | 3 单元测试通过 |
| 8 | 运行时能力完整报告、矛盾声明拒绝和 serial/manual 退化通过 | `test_adapter_contract_is_truthful_and_workspace_is_separate`、`test_adapter_rejects_contradictions` | 3 单元测试通过 |
| 9 | diff/reconcile/doctor/status/upgrade 接入 H3 状态并保持 dry-run | `test_reconcile_is_dry_run_then_applies_all_h3_records`、H2 lifecycle tests | 4 fixture 验证通过 |
| 10 | apply 前工作区变化拒绝且失败无部分 H3 写入 | `test_apply_rejects_workspace_change_without_partial_h3_write` | 4 fixture 验证通过 |
| 11 | retire 拒绝哈希不匹配的 H3 生成文件 | `test_retire_rejects_changed_h3_generated_hash` | 4 fixture 验证通过 |

H3 最高验证等级：4（确定性合同测试和临时目录落盘 fixture 通过）。领域包生产闭环、POSIX 实测、真实用户批准闭环和外部独立复现尚未证明。

## 6. H3.1-H7 退出门禁

| 里程碑 | 退出门禁 | 证据 | 结果 |
|---|---|---|---|
| H3.1 | Plan 批准绑定 Plan ID、内容指纹、范围、所有者和证据；伪造、过期、篡改、自批被拒绝；H3 五类生成指纹由 doctor 重算 | `tests/unit/test_approvals_roles.py`、`tests/integration/test_h3_lifecycle.py` | 28 项目标测试通过，两套 Python |
| H4 | 软件包清单、严格 Plan Approval、无虚构引用的追踪矩阵、Artifact/Evidence/Review/VerificationResult/Approval 深绑定、失败验证阻断，以及 external_release 落盘后复核 | `tests/unit/test_h4_domain_pack.py`、`tests/integration/test_h4_lifecycle.py` | H4 unit/integration 通过，两套 Python |
| H5 | brief/variant/review/Approval 深绑定、实际内容硬约束、唯一选中变体、内容生命周期，以及可后置追加并持续复核的 external_release 独立批准 | `tests/unit/test_h5_content_pack.py`、`tests/integration/test_h5_lifecycle.py` | H5 unit/integration 通过，两套 Python |
| H6 | append-only Artifact/Evidence/Evaluation 事件、确定性 ledger 视图、checkpoint/handoff 恢复、评价人工消费、no-change GovernanceEffect 和引用完整性 | `tests/unit/test_h6_evidence_governance.py`、`tests/integration/test_h6_lifecycle.py` | H6 unit/integration 通过，两套 Python |
| H7 | 全量测试、三次完整 canonical replay、隐私/路径/压缩包边界、对抗输入、Windows CLI、依赖检查和 clean-room 自查 | `tests/integration/test_h7_audit.py`、`tests/integration/test_h7_adversarial.py`、Windows 命令记录 | 263/263；Schema 48/48；独立包/POSIX/外部独立复核未验证；结论 `revise` |

实际记录（Windows 11，2026-08-17）：`python -m pytest -q -o addopts='' --disable-warnings` 退出码 0，263 passed；`.venv311\Scripts\python.exe -m pytest -q -o addopts='' --disable-warnings` 退出码 0，263 passed；当前 Python 与 `.venv311` 的 `doctor-schemas` 均退出码 0，48/48；两套 `compileall -q src tests` 退出码 0；`.venv311\Scripts\python.exe -m pip check` 退出码 0。当前全局 Python 的 `python -m pip check` 退出码 1，已有 `astrbot 4.25.2` 要求 `psutil<7.2.0` 而环境为 `psutil 7.2.2`，本轮未安装或修改依赖。H4/H5/H6 定向集成退出码 0，100 passed；H7 对抗 fixture 退出码 0，8 passed；H2/H3/两步写入退出码 0，33 passed；Plan Evidence 深绑定、重复内容幂等、symlink 漂移、pack `$ref` 和两个领域包 controlled-recovery fixture 均退出码 0；清理旧构建目录后的离线 wheel 构建退出码 0，包内当前领域包资源 18 项；wheel 隔离安装和 `--version`、`--help`、`doctor-schemas`、`init new`、`init existing` CLI 探针均退出码 0。当前 Python 版本为 3.14.3，`.venv311` 为 3.11.15。WSL 为 Python 3.8.10 且没有 pytest，未安装依赖，因此 POSIX 未验证。

本轮新增合同证据：`tests/unit/test_domain_pack_loader.py` 验证中性 pack 扩展、路径边界和不兼容版本；`tests/unit/test_storage.py::test_manifest_sink_rejects_mutation_after_preflight` 验证预检后确定性用户修改被原子写入拒绝且用户字节保留。领域包不再由 `core/domain_packs.py` 构造，manifest 覆盖写携带 `expected_before_fingerprint`。
Windows 临时隔离环境复现：使用现有 Python 3.11 执行 `python -m venv <temp>`，随后 `pip install --no-deps --no-build-isolation .`、`harness-everythings --version` 和 `doctor-schemas`，全部退出码 0；未安装第三方依赖，未生成面向终端用户的独立发布包。

## 7. 残余风险

- POSIX 平台仅靠纯 Python 语义保证，未在 Linux 实测（H7 补）。
- schema 校验为内建最小实现，非 JSON Schema 全集（已记录于 schema-registry.md）。
- `os.replace` 更新与 hard link 独占创建均限制在同目录；跨卷场景不适用。
- 本机检测 `pyinstaller`、`nuitka`、`briefcase`、`pyoxidizer`、`uv` 均不可用；未联网、未安装依赖、未生成面向未安装 Python 用户的独立发布包。下一步需用户授权安装/提供打包工具后再执行隔离环境 `--version`、`doctor-schemas`、`init/diff/status` 验证。
- Git 只读探针依赖本机 Git 可用性；不可用时仅保留文件系统观察信号。
- `upgrade` 在当前 1.0 -> 1.0 场景只生成 no-change 结论，真实 schema 迁移归后续版本。
- Python 3.14 在 Windows 临时 Git fixture 清理时出现一次 pytest cleanup warning，但退出码为 0；Python 3.11 无该 warning。
- H2 尚未证明 POSIX 实测、历史回放、真实用户批准闭环或外部独立复现。
- H3 角色、上下文与适配器合同已接入现有生命周期；H4/H5 的真实记录闭环和 H6 追加事件/视图已接入 `diff/reconcile/doctor/status/retire`。仍未完成真实用户生产确认、独立包验证、POSIX 验证和外部独立复核。
