# Clean-room 实施账本

依据：`products/harness-everythings/CLEAN-ROOM-BOUNDARY.md` 第 6 节。
本账本只记录需求来源相对路径、新设计决定、公开标准/依赖来源、
实现者未读取声明和相似性审查结论。不记录历史仓库绝对路径、
commit hash、私有文件名或原文摘录。

## 1. 开工断言（首个 Checkpoint，2026-08-16）

- 产品 Spec 已最终 Approved（用户 2026-08-16 确认 D-004、D-009，勾选 Approved）。
- 已读取当前允许清单中的产品合同：根 `AGENTS.md`、根 `SPEC.md`、根 `PLAN.md`、
  根 `TASK-PROMPT.md`、产品 `SPEC.md`、`DOMAIN-PACK-CONTRACT.md`、
  `CLEAN-ROOM-BOUNDARY.md`、`PLAN.md`、`IMPLEMENTATION-HANDOFF.md`、
  `IMPLEMENTATION-PROMPT.md`、`SPEC-REVIEW-GUIDE.md`。
- 未读取、搜索或索引历史 Harness 实现与演进案例
  （`case-studies/harness-evolution/` 及任何历史仓库、prompt、Agent 文件、
  Hook、Schema、测试、trace、工作日志或 Git diff）。
- 候选目录为全新空目录（本次创建），不含复制文件或私有 Git 历史。
- 当前没有 commit、remote、push 或发布授权。
- 当前实施范围为 H1。

## 1.1 H2 Checkpoint（2026-08-16）

- H1 基线复验：Python 3.14.3 与 3.11.15 均通过 142 项旧测试；Schema 自检 12/12；`pip check` 无损坏依赖。
- H2 只消费当前产品合同和本候选工程已有中性测试；没有读取、搜索或索引禁止目录。
- H2 新增只读工作区发现、隐私过滤、五态画像、ApplicationManifest 来源快照和生命周期安全门禁。
- 可信指令默认为空；工作区指令文件只作为 candidate/untrusted 信号，不执行其中的命令。

## 1.2 H3 Checkpoint（2026-08-16）

- H3 修补只使用产品 Spec 的角色、上下文和适配器合同，以及 H2 产生的中性 ProfileRecord 形状；没有读取历史实现、演进材料或私有账本。
- 角色拆分信号限定为所有权、风险、验证和并发边界；语言、框架、扩展名和目录名不作为单独角色依据。完整角色合同、生命周期转换和对账类别均由当前 Schema 冻结。
- 上下文路由只保存角色授权后的来源引用、来源指纹、预算和失效条件；敏感内容、密钥、个人/雇主数据、历史演进来源、prompt、trace 和完整素材不进入路由索引。
- 适配器能力缺失只映射到 serial/manual 或人工 Manifest 路径，不改变 canonical 状态、证据义务和批准边界；工作区 discovery/read-only 与 execution/write 明确分离。
- H3 代码、Schema、测试和 fixture 均位于当前候选工程；`diff/reconcile/doctor/status/upgrade` 的 H3 接入通过临时目录落盘 fixture 验证，未执行任何外部网络动作。

H3 验证记录（Windows 11，2026-08-16）：

```text
Python 3.14.3 `python -m pytest`：退出码 0，170 passed
Python 3.11.15 `.venv311\\Scripts\\python -m pytest`：退出码 0，170 passed
`.venv311\\Scripts\\harness-everythings.exe doctor-schemas`：退出码 0，22/22
Python 3.11.15 `.venv311\\Scripts\\python -m pip check`：退出码 0
`tests/integration/test_h3_lifecycle.py`：退出码 0，5 passed
```

## 2. 需求来源映射（作品集内相对路径）

| 实现内容 | 来源（相对路径 + 章节） |
|---|---|
| 实体清单（Workspace 等 12 类） | `products/harness-everythings/IMPLEMENTATION-HANDOFF.md` 第 5 节 |
| canonical JSON + Markdown 确定性视图（D-005） | `products/harness-everythings/SPEC.md` 第 6、17 节 |
| 任务状态机与预算/重试/幂等/所有权 | `products/harness-everythings/SPEC.md` 第 15 节 |
| 批准双状态分离、角色自批禁止 | `products/harness-everythings/SPEC.md` 第 11、14 节 |
| 角色生命周期枚举 | `products/harness-everythings/SPEC.md` 第 9 节 |
| 路径边界、原子写、两步写入 | `products/harness-everythings/IMPLEMENTATION-HANDOFF.md` 第 6、7 节 |
| adapter capability 注册与退化路径 | `products/harness-everythings/SPEC.md` 第 16 节 |
| 错误类别与退出码冻结 | `products/harness-everythings/IMPLEMENTATION-HANDOFF.md` 第 6 节 |
| CLI 命令面与 dry-run 默认 | `products/harness-everythings/SPEC.md` 第 7.1 节 |
| 验证等级枚举（7 级） | `products/harness-everythings/SPEC.md` 第 12 节 |

## 3. 新设计决定（clean-room 原创，非历史复刻）

| 决定 | 理由 |
|---|---|
| 稳定 ID = `namespace:sha256前16位`（由种子派生） | 满足"重复初始化得到同一 ID"，不引入递增序号与平台差异 |
| canonical JSON：`sort_keys + ensure_ascii=False + 紧凑分隔符`；文件漂移使用原始字节指纹 | 固定输入产生相同 JSON；CRLF/LF 等真实字节变化不能被指纹掩盖 |
| schema_version `1.0` 存于包内 `schemas/v1/` 目录 | 目录名与版本号解耦，未来 `1.1` 可同置 `v1/` 或新目录 |
| 迁移注册表默认禁回退，反向须显式 `allow_reverse` | Spec 第 17 节要求前向迁移与回退显式受控 |
| 原子写 = 同目录 `.hxtmp-` + fsync；新文件 hard link 独占，更新绑定旧指纹后 `os.replace`；批量失败回滚 | 防止并发覆盖、半写 JSON 和部分 apply |
| 清单指纹 = `content_fingerprint(manifest.to_record())` | 批准与清单一一对应；apply 前重验工作区指纹 |
| 工作区指纹只计算清单声明路径（不整树扫描） | 避免平台目录序差异破坏确定性；H2 画像阶段再扩展 |
| 错误类别 10 类 + 退出码 2-11（0 为成功） | HANDOFF 建议类别；编号在本仓库冻结 |
| 能力键 11 项 + 每项 serial/manual 退化路径映射 | Spec 第 16 节"每项首选能力都必须有退化路径" |
| 五态画像、来源指纹和隐私过滤 | `products/harness-everythings/SPEC.md` 第 7.3、8、14 节；H2 新设计，未使用工作区文件内容作为指令 |
| init/reconcile 来源快照与 retire 注册表指纹 | `products/harness-everythings/SPEC.md` 第 7、17、18 节；基于现有 ApplicationManifest 扩展 |
| `failed` 只能经显式人工证据回到 `proposed` | 防止静默复活；Spec 第 15 节重试三类策略 |
| 任务增加 `paused` canonical 状态，暂停要求 checkpoint/handoff 证据 | 落实 Spec 第 15 节暂停、恢复与切换前 checkpoint 合同 |

## 3.1 H1 独立复核修正（2026-08-16）

- 状态转换现在核对记录的 `from_state`，拒绝伪造历史。
- ApplicationManifest 幂等键包含目标 payload 指纹；重复规范化路径被拒绝。
- 新目标默认原子独占创建；覆盖必须绑定旧字节指纹。
- 批量 apply 在运行期失败时回滚已写目标，并保护回滚期间的外部修改。
- 批准合同增加目标所有者，阻止角色批准自己拥有的对象。
- CLI 冻结 `init new` / `init existing`，任务状态机补齐暂停/恢复。
- Python 3.11.15 与 3.14.3 均通过 142 项测试。

## 3.2 H3.1-H7 复核记录

- H3.1 使用批准合同中的批准边界和稳定指纹要求，未读取历史实现；新增 Plan 批准记录、领域包、证据和内容合同均为本轮从零实现。
- H4/H5 只使用当前产品 Spec、Plan、DOMAIN-PACK-CONTRACT 和 clean-room 清单，未复制历史 prompt、Agent 文件、Hook、Schema、trace 或工作日志。
- H6/H7 使用标准库、当前候选工程和合成 fixture；三次完整代表性工作流 canonical 字节一致，对抗 fixture 覆盖密钥/个人信息/不可信指令/上下文边界/路径与压缩包穿越，未读取历史 Harness 或 case-studies。
- Windows 11 本地复验：当前 Python 3.14.3 与 Python 3.11.15 各 263 项测试，`doctor-schemas` 48/48；两套 `compileall -q src tests` 退出码 0；`.venv311` 的 `pip check` 退出码 0。当前全局 Python 的 `pip check` 退出码 1，已有 `astrbot 4.25.2` 要求 `psutil<7.2.0` 而环境为 `psutil 7.2.2`，本轮未安装或修改依赖。本机没有独立打包工具；清理旧构建目录后，现有 setuptools 离线 wheel 构建退出 0，包含 18 个当前领域包资源；wheel 隔离安装和 CLI 探针退出码均为 0。WSL 只有 Python 3.8.10 且没有 pytest，未安装依赖，POSIX 未验证。
- 当前结论：`revise`。H4/H5/H6 批准深绑定与真实生命周期闭环已复验；软件与内容 external_release 的后置追加、落盘复核和篡改失效路径已加入集成验证。H7 无 Python 独立包、POSIX 实测和外部独立复核仍未完成；不是外部发布或许可证批准。

## 3.3 领域包与原子写修补记录（2026-08-17）

- 领域包角色、阶段和 validator 已移入 `src/harness_everythings/domain_packs/` 的独立版本化 `pack.json`；通用加载器验证资源路径、Schema、内核版本、引用唯一性和 canonical 指纹。中性扩展 fixture 在不修改内核 pack 分支的情况下通过，未读取历史实现。
- `ApplicationManifest` 现在由同一 `write_lock` 覆盖预检、写入、回滚；已有目标的旧指纹传递到 atomic sink 并在替换前再次检查。确定性漂移 fixture 证明用户修改被拒绝，retire 也在删除边界再次核对生成文件哈希。
- 本轮不改变 H7 未完成事实：独立 Windows 包、无 Python 隔离验证、POSIX 实测、外部独立复核和真实用户生产确认仍未完成；结论保持 `revise`。

## 4. 使用的公开标准与第三方依赖

| 项 | 来源/许可证 | 用途 |
|---|---|---|
| JSON Schema draft 2020-12（`$schema` 声明） | 公开标准 | schema 文件格式声明；运行时校验为内建最小实现 |
| Python 3.11+ 标准库（json/hashlib/pathlib/argparse/dataclasses/importlib.resources/tempfile） | PSF License | 全部运行时功能，零第三方运行依赖 |
| pytest（开发依赖） | MIT | 测试运行器；不进入运行时 |

未复制任何无许可证代码片段。

## 5. 实现者未读取声明

本实施 Agent 声明：在整个 H1 实施过程中未打开、搜索、索引或读取：

- `case-studies/harness-evolution/`
- 任何历史 Development Harness、Delta 或雇主相关仓库
- 任何历史 prompt、Agent 文件、Hook、Schema、测试、trace、工作日志或 Git diff

所有代码、schema、测试、fixture 均为本次从零编写。

## 6. 相似性审查结论

- 2026-08-16 首次自查：包结构（core/storage/schemas/adapters/views/cli）
  来自 HANDOFF 第 4 节建议结构；字段命名（如 `entity_id`、`source_ref`、
  `fact_key`、`retry_policy`、`generation_origin`）为本仓库新造英文命名，
  未参照任何历史实现词汇。
- 枚举值（任务状态、验证等级、敏感等级）逐字来自产品 Spec 中文合同的
  英文直译，无历史专有术语。
- 未发现与历史实现的已知相似表面；后续里程碑每次交付后复查本节。
- H2 复查：新增模块只使用 Python 标准库、固定 Git 只读命令、当前候选工程的 Schema 和中性 fixture；未引入历史标识符、提示词或运行轨迹。
- H3 复查：角色、上下文和适配器模块未读取历史实现；测试中的历史路径仅作为拒绝边界字符串，不作为运行时输入或产品行为来源。
