# harness-everythings

领域无关的 Agent 生产治理内核。clean-room 实现：仅消费当前已批准的产品合同（`products/harness-everythings/SPEC.md` 等），不包含任何历史实现材料。

当前状态：治理控制面的 H4/H5/H6 合同和 H7 对抗 fixture 已完成本地修补复验，但真实工作流编排尚未实现。当前 CLI 不启动 Agent、不分派任务，也不驱动真实审核、返工或跨会话恢复；现有闭环测试只证明记录合同自洽。当前结论为 `revise`。

下一优先级是 H3.2：实现供应商无关的编排协议，先适配 Codex 和 Claude Code 并完成真实工作流，再实现 Pi 四工具加 subagent extension 的原生运行时；DeepSeek Harness 插件作为后续可选集成形态。实施边界和退出条件见 [`docs/runtime-orchestration.md`](docs/runtime-orchestration.md)。

## 运行要求

- 开发端：Python 3.11+，无第三方运行依赖（仅标准库）。
- 最终用户：优先使用独立可执行包（不要求预装 Python）。
- 源码模式缺少 Python 3.11+ 时：只展示安装计划，获得用户明确批准后才可安装，禁止静默安装或提权。

## 开发命令

```text
python -m venv .venv
Windows 安装开发环境：.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Windows 运行测试：.\.venv\Scripts\python.exe -m pytest
校验 Schema：.\.venv\Scripts\harness-everythings.exe doctor-schemas
CLI：.\.venv\Scripts\harness-everythings.exe --help
```

H2 命令示例：

```text
.\.venv\Scripts\harness-everythings.exe init new --workspace <path> --now <固定时间>
.\.venv\Scripts\harness-everythings.exe init existing --workspace <path> --now <固定时间>
.\.venv\Scripts\harness-everythings.exe inspect --workspace <path>
.\.venv\Scripts\harness-everythings.exe diff --workspace <path>
.\.venv\Scripts\harness-everythings.exe reconcile --workspace <path>
.\.venv\Scripts\harness-everythings.exe doctor --workspace <path>
.\.venv\Scripts\harness-everythings.exe status --workspace <path>
.\.venv\Scripts\harness-everythings.exe upgrade --workspace <path>
.\.venv\Scripts\harness-everythings.exe retire --workspace <path>
```

写入命令仍默认 dry-run。`--no-dry-run` 必须配合外部用户批准 JSON，批准内容必须绑定本次 ApplicationManifest 指纹；没有批准不会写入或删除。

H3 接入的生命周期输出：`diff` 比较角色注册表、上下文路由和适配器状态；`reconcile` 默认只生成提案；`doctor` 检查 Schema、所有权、预算、来源边界和能力退化；`status` 汇总角色、路由、适配器和未决问题。Plan 未批准时不生成派生角色、上下文或适配器状态。

领域包位于 `src/harness_everythings/domain_packs/<pack-id>/`，每个 `pack.json` 独立版本化并由通用加载器验证 Schema、路径、版本、角色/阶段/validator 引用和指纹；新增中性 pack 不需要修改内核角色分支。`ApplicationManifest` 的批量写入在同一互斥临界区内重新验证每个目标的旧指纹，漂移即拒绝并回滚。

本轮修补后：Python 3.14.3 与 3.11.15 各 `263 passed`；两套环境 `doctor-schemas` 均为 `48/48`。隔离 Python 3.11 环境的 `pip check` 退出码 0；当前全局 Python 的 `pip check` 退出码 1，因已有 `astrbot 4.25.2` 要求 `psutil<7.2.0` 而环境为 `psutil 7.2.2`，本轮未安装或修改依赖。H4/H5/H6 定向集成 `100 passed`，H7 对抗 fixture `8 passed`，H2/H3/两步写入 `33 passed`；Plan Evidence 深绑定、重复内容幂等、symlink 漂移、pack `$ref` 和领域包失败恢复也已执行。软件与内容外部发布批准会在落盘后由 `doctor/status` 重新验证，篡改或失效记录不会显示为已批准。清理旧构建目录后使用现有 setuptools 离线重建 wheel，退出码 0，包含 18 个当前声明的领域包资源；wheel 的隔离安装与 `--version`、`doctor-schemas`、`init new`、`init existing` CLI 探针均退出 0。未发现独立打包工具，WSL 仅 Python 3.8.10 且无 pytest；无 Python 独立包、POSIX 和外部独立复核仍未验证，结论保持 `revise`。

## 里程碑

H1 -> H2 -> H3 -> H3.2（真实编排）-> 重新验收 H4/H5/H6 -> H7，见 `docs/` 与产品 PLAN.md。
