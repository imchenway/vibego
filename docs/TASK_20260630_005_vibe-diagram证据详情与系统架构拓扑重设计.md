# TASK_20260630_005 vibe-diagram 证据详情与系统架构拓扑重设计

## 用户反馈

1. 当前图把证据全部放到底部，显得乱；证据可放到卡片点击详情里。
2. 截图里的图不像系统架构图，无法一眼看出系统架构设计，需要重新设计系统架构图规则。

## 设计决策

- 证据分层：节点内只展示 E#、结论、可信度或状态；原始证据默认进入对应节点点击详情；底部证据区不再是默认结构，只保留跨节点冲突裁决、全局索引或测试矩阵。
- 系统架构图分层：必须首屏出现北向南全局拓扑总览；外部入口、接入/网关、业务服务/Agent、工具与中间件、状态/数据/观测按层成带状排列。
- 系统架构图第一阅读路径：从入口沿主请求流、控制流、数据读写流或兜底流走到数据/观测面；如果第一眼只能看到多列卡片和证据文字，必须重画。

## 代码/规约变更

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`
    - 新增 `证据详情与底部证据区规则`。
    - 强化 `系统架构图规则` 的架构拓扑执行门禁。
- `tests/test_builtin_skills_injection.py`
    - 新增原始证据进入节点详情的回归测试。
    - 新增系统架构图必须是全局拓扑而非分层卡片目录的回归测试。
- 同步更新本机全局 AGENTS managed block。

## 验证

| 验证项     | 命令 / 口径                                                                                                                                                                                                                                                                    | 结果                                                     |
|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| TDD 红灯  | `python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_raw_evidence_should_live_in_node_details_not_bottom_piles tests/test_builtin_skills_injection.py::test_vibe_diagram_system_architecture_must_read_as_global_topology_not_layered_cards` | 修复前失败，确认缺口存在                                           |
| TDD 绿灯  | 同上                                                                                                                                                                                                                                                                         | 修复后通过                                                  |
| 相关回归    | `python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`                                                                                                                                         | `46 passed`                                            |
| HTML 交付 | `HTMLParser().feed(...)`                                                                                                                                                                                                                                                   | 解析通过                                                   |
| 同步验证    | `rg -n "原始证据默认进入对应节点的点击详情\|系统架构图不是组件清单" ...`                                                                                                                                                                                                                               | 仓库 skill、override skill、vibego AGENTS、Codex AGENTS 均命中 |

## 剩余风险

- 本次修订的是制图规则与示例，不直接改某个业务系统的真实架构图。
- 已运行中的旧模型会话可能仍持有旧 skill，需要重启或新开会话让新规则生效。

- 未覆盖：本轮尝试用内置浏览器打开本地 `file://` HTML 时被浏览器安全策略拦截，因此没有声明截图级视觉 QA 通过；已完成
  HTMLParser 与规则命中验证。
