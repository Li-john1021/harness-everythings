# 真实工作流编排实施记录

状态：`revise`  
记录日期：2026-08-18  
规范来源：作品集工作区中已批准的产品 `SPEC.md` 0.6 与 `PLAN.md` 1.0

本文是候选仓库内的公开实施摘要，不替代产品 Spec。它用于保证独立 clone 后仍能识别当前缺口和下一实施入口。

## 当前问题

当前 CLI 是治理控制面，不是完整工作流编排器。它已经提供工作区发现、Plan、角色、上下文路由、领域包、Artifact、Evidence、Traceability、Approval、checkpoint/handoff、`doctor` 和 `status`，但尚未：

- 启动 Codex、Claude Code 或其他 Agent；
- 让 Worker 领取带租约的任务；
- 将角色专属上下文装配到真实执行会话；
- 消费工具调用、产物、审核和验证事件；
- 驱动失败返工、暂停恢复、预算、取消和重试；
- 通过独立审核和用户批准决定退出。

现有 `_completed_workflow_task` 会在单次函数调用中构造 `ready -> running -> review -> validation -> awaiting_approval -> delivered` 历史。这只能验证 Schema 和记录合同，不能作为真实执行证据。现有测试通过代表治理记录自洽，不代表 Agent 工作流已经运行。

## 目标分层

```text
Harness CLI / Core
  任务、状态、证据、角色所有权、审核、批准和退出条件
          |
          | TaskEnvelope / ExecutionEvent
          v
RuntimeAdapter
  能力探测、会话启动、事件回传、取消、checkpoint 和 resume
          |
          +-- Codex adapter
          +-- Claude Code adapter
          +-- Pi native adapter
          +-- serial/manual fallback
```

Harness 不重新实现通用 Coding Agent。模型会话、基础工具循环和上下文压缩由运行时承担；Harness 只实现供应商无关的治理与编排协议。

## H3.2 实施顺序

1. 定义并版本化 `RuntimeAdapter`、`TaskEnvelope`、`ExecutionEvent`、`Lease` 和 `Checkpoint`。
2. 实现串行参考编排器及以下 CLI：

```text
run
next
claim
heartbeat
complete
fail
review
approve
resume
cancel
events
```

3. 使用 append-only runtime event ledger，支持幂等消费、租约过期、预算、取消、失败、重试和确定性状态重建。
4. 状态只能由真实领取、执行事件、产物、独立审核、验证和批准推进；禁止依据模型自述或单函数构造直接进入 `delivered`。
5. 首先实现 Codex adapter，完成正常流程、故意失败返工、暂停恢复、独立审核和用户批准等待。
6. 随后实现 Claude Code adapter，并通过同一 conformance suite，证明内核不绑定单一供应商。
7. 两个主流适配器稳定后，再实现 Pi 原生运行时或评估 DeepSeek Harness 插件。

## Pi 原生运行时

Pi 保持默认的 `read/write/edit/bash` 四工具。一个薄 extension 负责：

- 启动时调用 Harness 获取当前 `TaskEnvelope`；
- 注入当前角色的最小上下文引用；
- 拦截工具调用并执行路径、命令和批准门禁；
- 使用 subagent extension 启动隔离的实施、审核或验证会话；
- 将工具、产物、失败和完成事件回传 Harness；
- 在会话结束时提交 `complete`、`fail` 或 checkpoint。

Pi 系统提示词保持极短，只要求执行当前任务合同、提交证据、不得自审自批，并由 Harness 决定下一状态。提示词不是安全边界；强制规则必须存在于 CLI、Schema 和 extension 代码中。

## DeepSeek Harness 插件

DeepSeek Harness 插件是 Pi 之后的可选集成或分发形态。它必须复用同一 `RuntimeAdapter` 和事件合同，不得把 DeepSeek 专有状态、提示词或供应商接口写进治理内核，也不得替代 Codex、Claude Code 的首批适配门禁。

## 退出条件

H3.2 只有同时满足以下条件才完成：

- Codex 与 Claude Code 都真实创建执行会话并产生可回溯事件；
- 实施、审核、验证和批准由不同授权主体推进；
- 至少一次故意失败触发返工，修复后重新独立审核；
- 中断后可从持久 checkpoint 恢复；
- 重复事件、过期租约、自审、越权路径、危险命令、预算超限和失效批准被拒绝；
- `doctor/status` 能从落盘事件、Artifact、Evidence 和 Approval 重建并核对状态；
- 独立审计 Agent 按 Spec 对账且没有阻断 finding；
- 现有 H4/H5/H6 必须在真实编排器上重新验收，不能沿用合成 fixture 的完成结论。
