# TASK_20260630_021：vibe-diagram superpowers 式 skill 拆分改造方案

## 结论

推荐选择 **B：薄内核 + 按图型引用文件 + 同步器只注入索引/内核**。

不要直接把 `vibe-diagram` 拆成多个 `data/skills/*/SKILL.md`。当前同步链路会把每个内置 `SKILL.md` 全文注入
AGENTS；如果直接拆，可能从“一个 619 行常驻 skill”变成“多个常驻长 skill”，效果更差。

## 为什么 superpowers 能承载很多 skill

- superpowers 主要靠 **元数据发现 + 按需加载正文**：`using-superpowers/SKILL.md` 说明 Claude / Copilot / Gemini 都是先看到可发现的
  skill，再在命中时加载全文。
- `writing-skills/SKILL.md` 要求重引用拆文件：heavy reference 可放 supporting file；频繁加载内容要极短，并通过
  cross-reference 避免重复。
- skill 变更按 TDD 做：先看 agent 失败，再写规则，再验证 agent 是否会遵守。

## 当前 vibe-diagram 的瓶颈

- `vibego_cli/data/skills/vibe-diagram/SKILL.md` 当前约 619 行、36 个二级章节。
- `scripts/models/common.sh::render_builtin_skills` 和 `vibego_cli/agents_sync.py::render_builtin_skills` 当前都会把
  `*/SKILL.md` 全文拼进 AGENTS。
- `pyproject.toml` 当前 package-data 只包含 `data/skills/*/SKILL.md` 与 `data/skills/*/agents/*.yaml`，若新增 references
  目录，需要补包发布规则。
- `tests/test_builtin_skills_injection.py` 现在大量断言单一 `SKILL.md` 必须包含所有规则，重构时要同步改为“内核断言 +
  引用文件断言 + 同步输出断言”。

## 推荐架构

1. `AGENTS-template.md`：保留何时触发、HTML-first、交付信封、技能路由。
2. `vibe-diagram/SKILL.md`：保留薄内核，目标约 180-250 行：
    - 交付铁律；
    - 自动路由；
    - 共同红线：不是卡片堆、节点承载信息、箭头/防重叠、HTML/CSS 可访问性；
    - 图型选择后必须加载对应引用文件；
    - 输出前自检。
3. `vibe-diagram/references/*.md`：每个图型一个文件：
    - `system-architecture.md`
    - `business-architecture.md`
    - `business-flow.md`
    - `code-sequence.md`
    - `state-data-model.md`
    - `fault-debugging.md`
    - `feature-iteration.md`
    - `page-mockup.md`
    - `technical-design.md`
    - `decision-communication.md`
4. 同步器：只注入 `SKILL.md` 薄内核与 references 索引，不把全部 references 拼进 AGENTS。
5. 测试：改成“压力场景”验证，而不是只靠关键词堆断言。

## 方案对比

| 方案 | 做法                        | 优点                             | 缺点              | 建议      |
|----|---------------------------|--------------------------------|-----------------|---------|
| A  | 仍保留单文件，只压缩到 250 行左右       | 风险最低，改动小                       | 后续继续膨胀，图型规则互相挤压 | 可作为临时止血 |
| B  | 薄内核 + references + 同步器索引化 | 最接近 superpowers；上下文更轻；每类图可独立优化 | 需要改同步、打包、测试     | 推荐      |
| C  | 拆成多个内置 `SKILL.md` 但不改同步器  | 看似模块化                          | 当前同步会全量注入，可能更糟  | 不推荐     |

## 建议实施顺序

1. 先写失败测试：证明当前同步器会全文注入、references 未打包、内核未强制加载对应图型引用。
2. 改同步器：AGENTS 只注入薄内核与 references 索引。
3. 改打包规则：把 `data/skills/*/references/*.md` 放进包。
4. 拆 `vibe-diagram/SKILL.md`：先保留内核，再迁移每类图型规则。
5. 更新测试：每个图型引用文件保留自己的压力规则；内核只保留共性门禁。
6. 同步 AGENTS 并跑回归。

## 需要用户确认

推荐按 **B** 做。确认后再进入 TDD 实施，不在本轮直接改代码。
