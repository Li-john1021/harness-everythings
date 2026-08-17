# Spec 对账矩阵

版本：2026-08-17 本地修补复验。产品 `SPEC.md` 是最高权威；本表记录可观察实现、测试证据和未完成门禁，不把内部测试或子代理审计写成外部复核。

| 规范引用 | 可观察要求 | 实现/证据 | 最高已证明等级 | 状态 | 未满足或限制 |
|---|---|---|---:|---|---|
| SPEC 6-7 | 确定性 `.harness-everythings/`、dry-run、Manifest/前置指纹、生命周期命令 | `core/lifecycle.py`；`tests/integration/test_h2_lifecycle.py`、`test_h3_lifecycle.py`、`tests/unit/test_storage.py` | 4 | pass | 未做 POSIX 实测 |
| SPEC 8 | observed/inferred/user_confirmed/unresolved/disproved 画像，来源、时间、confidence、freshness、sensitivity、稳定指纹 | `core/profile.py`、`core/discovery.py`；H2 fixture | 4 | pass | 真实用户工作区确认未完成 |
| SPEC 9 | 完整 Role 合同、生命周期与用户覆盖优先 | `core/roles.py`、`role.schema.json`；`test_h3_roles_context.py` | 4 | pass | 生产角色质量仍需用户确认 |
| SPEC 10；DOMAIN-PACK-CONTRACT 2,8 | pack 物理隔离、版本/Schema/角色/阶段/validator/能力/路由/指纹校验；未知和越界拒绝 | `domain_packs/*/pack.json`、`core/domain_pack_loader.py`；`test_domain_pack_loader.py`、H4/H5 tests | 4 | pass | 未做独立发行包内资源验证 |
| SPEC 11 | 审核结果结构化、批准深绑定、external_release 独立且落盘后重新验证 | `core/content_domain.py`、`core/domain_packs.py`、`core/lifecycle.py`；H4/H5 integration | 4 | pass | 未完成真实用户生产审批 |
| SPEC 12 | Artifact/Evidence/VerificationResult/Approval 实际记录及指纹绑定 | `core/evidence.py`、`core/domain_packs.py`；H4 unit/integration | 4 | pass | 未完成外部独立复现 |
| SPEC 13 | 上下文按角色/用途授权，来源指纹、预算、拒绝原因，历史/私有/无关来源不广播 | `core/context.py`、`context-routes.schema.json`；H3 context tests | 4 | pass | 未做 POSIX 运行时审计 |
| SPEC 14-15 | 不可信输入、路径/命令边界、失败/暂停/批准约束 | `core/discovery.py`、`storage/paths.py`、`storage/archive.py`；H7 adversarial | 4 | pass | 外部独立安全复核未完成 |
| SPEC 16 | 供应商无关适配器，能力缺失有 serial/manual 退化且批准边界不变 | `adapters/contracts.py`、`adapters/capabilities.py`；H3 tests | 4 | pass | 未验证第三方运行时 |
| SPEC 17 | canonical 字节、幂等、漂移拒绝、用户内容不覆盖 | `core/identity.py`、`storage/atomic.py`、`storage/manifest.py`；storage/integration tests | 4 | pass | Windows 已验证跨进程工作区锁与末次指纹检查；POSIX 进程锁尚未实测 |
| SPEC 18(1-10) | 本地全量、Schema、fixture、H4/H5/H6 集成、三次 replay | 见 `docs/acceptance-matrix.md` 与最终命令记录 | 4 | pass | 当前 Python 全局 `pip check` 有既有冲突 |
| SPEC 18(11-13) | clean-room、Windows 独立包、POSIX、外部独立复核和用户批准 | 受保护审计区的脱敏门禁摘要 | 2 | revise | 无可用打包工具；WSL Python 3.8.10 且无 pytest；没有外部独立复现或真实用户生产确认 |
| PLAN H7 | 未完成外部/包/POSIX 门禁不得 `ready_for_user_approval` | 候选 README、acceptance matrix、clean-room ledger | 4 | revise | 结论必须保持 `revise` |
| CLEAN-ROOM 3,7 | 不读取历史 Harness/case-studies；不执行网络、发布、commit/remote/push | clean-room ledger；本轮命令记录 | 4 | pass | 本表不替代用户对权属的最终批准 |

## 命令证据

实际命令记录：`python -m pytest -q -o addopts='' --disable-warnings` 退出码 0，263 passed（Python 3.14.3）；`.venv311\Scripts\python.exe -m pytest -q -o addopts='' --disable-warnings` 退出码 0，263 passed（Python 3.11.15）；两套 `doctor-schemas` 退出码 0，48/48；两套 `compileall -q src tests` 退出码 0；`.venv311\Scripts\python.exe -m pip check` 退出码 0；`python -m pip check` 退出码 1，原因是既有 `astrbot`/`psutil` 冲突。H4/H5/H6 定向集成 100 passed，H7 对抗 fixture 8 passed，H2/H3/两步写入 33 passed，均退出码 0；Plan Evidence 深绑定、重复内容幂等、symlink 漂移、pack `$ref` 和两个领域包 controlled-recovery fixture 均通过；清理旧构建目录后使用现有 setuptools 离线重建 wheel，退出码 0，包含 18 个当前领域包资源；wheel 隔离安装以及 `--version`、`--help`、`doctor-schemas`、`init new`、`init existing` CLI 探针退出码 0。三次完整 replay 在 H7 audit fixture 内通过。本轮没有安装依赖、提权、联网或改变系统环境。

## 结论

代码、Schema、测试和本地文档对账后，最高已证明验证等级为 4（Windows 临时目录 fixture 和确定性合同测试）。H7 的独立包、POSIX、外部独立复核和真实用户批准仍未证明，因此当前结论是 `revise`，不得恢复 `ready_for_user_approval`，也不得进入发布。
