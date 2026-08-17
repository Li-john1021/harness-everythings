"""harness-everythings：领域无关 Agent 生产治理内核（clean-room 实现）。

包结构：
- core      纯领域逻辑（实体、状态机、指纹、ID）
- storage   确定性存储（原子写、路径边界、ApplicationManifest）
- schemas   版本化 JSON Schema（随包分发）
- views     确定性 Markdown 视图
- adapters  能力注册合同（只描述，不执行）
- cli       命令行入口
"""

__version__ = "0.1.0"

SCHEMA_MAJOR = 1
