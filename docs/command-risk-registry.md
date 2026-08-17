# 命令风险注册表

版本：H4-H6 闭环修补（2026-08-17）

## 1. 命令面

| 命令 | 风险等级 | dry-run 默认 | 批准策略 | 实现状态 |
|---|---|---|---|---|
| `init` | write | 是 | 清单批准后 apply | H2 |
| `inspect` | read | 否 | 无需批准 | H2 |
| `diff` | read | 否 | 无需批准 | H2 |
| `reconcile` | write | 是 | 清单批准后 apply | H2 |
| `doctor` | read | 否 | 无需批准 | H2 |
| `doctor-schemas` | read | 否 | 无需批准 | H4-H6（48 个 Schema） |
| `status` | read | 否 | 无需批准 | H2 |
| `upgrade` | write | 是 | 可回退 Plan 批准 | H2（无变更时 no-change） |
| `retire` | write | 是 | 注册表指纹 + 显式批准 + 哈希匹配 | H2 |

写入命令公共合同：`--dry-run`（默认开）、`--no-dry-run` 不直接生效——
真正写入必须经 ApplicationManifest + 用户批准 + apply 三步。

## 1.1 新增核心 API

| API | 风险等级 | dry-run 默认 | 批准策略 | 实现状态 |
|---|---|---|---|---|
| `submit_software_workflow` | write | 是 | 真实 Artifact/Evidence/Review/VerificationResult 通过深绑定后生成 Manifest，再由用户批准 apply | H4 |
| `submit_content_workflow` | write | 是 | 当前 brief/variant/review 与 work-product Approval 深绑定后生成 Manifest，再由用户批准 apply | H5 |
| `append_h6_events` | write | 是 | 只追加事件；Manifest 绑定现有工作区指纹，reconcile 后由用户批准应用视图 | H6 |

## 2. 退出码（冻结）

| 码 | 类别 |
|---|---|
| 0 | OK |
| 2 | invalid_input |
| 3 | schema_incompatible |
| 4 | boundary_escape |
| 5 | approval_missing |
| 6 | fingerprint_conflict |
| 7 | user_file_conflict |
| 8 | capability_missing |
| 9 | verification_failed |
| 10 | external_tool_failed |
| 11 | internal_invariant |

## 3. H2 安全边界

- 不执行任何工作区脚本；Git 仅使用固定只读探针；不联网；不调用模型。
- 唯一写路径 = `storage.atomic.write_atomic`（边界检查 + 原子替换）。
- `doctor-schemas`、`inspect`、`diff`、`doctor`、`status` 为纯读命令。
- 画像摘要在生成结果前过滤密钥、邮箱、绝对路径和敏感文件内容；可信指令为空，候选指令必须由用户确认。
