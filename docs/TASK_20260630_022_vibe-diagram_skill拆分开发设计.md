# TASK_20260630_022：vibe-diagram skill 拆分开发设计

## 设计结论

按 **薄内核 + 图型 reference + TDD 迁移** 实施。核心目标是：AGENTS
常驻内容只负责触发、路由、交付和共性红线；系统架构图、业务架构图、故障排查、功能迭代、页面设计稿等重规则按图型读取对应
reference。

本设计只定义开发方案，未修改实现。

## 目标

1. 保留现有 HTML-first 与 Codex/Telegram 双端交付约束。
2. 把 `vibe-diagram/SKILL.md` 收敛为薄内核，目标不超过 300 行。
3. 将每类图的长规则迁移到 `vibe-diagram/references/*.md`。
4. 所有迁移先写失败测试，再移动内容，再跑回归。
5. 避免把多个长 `SKILL.md` 全量注入 AGENTS。

## 非目标

- 不新增外部依赖。
- 不做真实 Skill tool/plugin 动态加载器；当前阶段通过 AGENTS 内核指令要求模型读取本地 reference 文件。
- 不重写所有历史示例 HTML，只保证未来规则与测试闭环。

## 目标文件结构

```text
vibego_cli/data/skills/vibe-diagram/
  SKILL.md                         # 薄内核：交付、路由、共性门禁、reference 索引
  references/
    system-architecture.md
    business-architecture.md
    business-flow.md
    code-sequence.md
    state-data-model.md
    fault-debugging.md
    feature-iteration.md
    page-mockup.md
    technical-design.md
    decision-communication.md
```

## 关键实现点

### 1. 薄内核

`SKILL.md` 保留：

- frontmatter；
- HTML 交付铁律；
- 自动路由表；
- 图型到 reference 的索引；
- 必须读取对应 reference 的硬门禁；
- 共性图形语法门禁；
- 节点信息承载、箭头、防重叠、CSS/可访问性、自检。

### 2. references

每个 reference 只管一种图型，结构统一：

- 何时使用；
- 这个图应该长成什么样；
- 主骨架；
- 布局和箭头规则；
- 节点内容规则；
- 常见失败样式；
- 输出前自检。

### 3. 同步与打包

- 当前同步器会注入 `SKILL.md` 全文；薄内核后这件事变成可接受。
- `references/*.md` 不由同步器全文注入，而由薄内核索引并按需读取。
- `pyproject.toml` 需要补 `data/skills/*/references/*.md`，确保包发布后引用文件存在。
- `MANIFEST.in` 已有 `recursive-include vibego_cli/data/skills *`，原则上覆盖 references，但仍需要测试确认。

### 4. 测试迁移

现有测试不能继续要求所有规则都在单一 `SKILL.md`。改为三层测试：

1. 内核测试：断言薄内核存在交付、路由、共性红线、reference 索引、硬门禁。
2. reference 测试：断言每类图的专属规则在对应文件里。
3. 同步/打包测试：断言 AGENTS 只包含薄内核，不包含 reference 全文；打包清单包含 references。

## TDD 顺序

1. RED：新增测试，要求 `references/*.md` 存在、核心 skill 不超过 300 行、同步输出不包含 reference 全文。
2. RED：迁移系统架构、业务架构、故障排查、功能迭代等专属断言到对应 reference，先看失败。
3. GREEN：创建 references 目录并迁移内容。
4. GREEN：压缩 `SKILL.md` 为薄内核。
5. GREEN：补 package-data。
6. REFACTOR：去重、统一 reference 模板、保留关键短语，防止测试只靠脆弱文案。
7. VERIFY：跑相关 pytest、agents-sync、内容检查。

## 验收标准

- `vibe-diagram/SKILL.md` ≤ 300 行，仍包含 HTML-first、路由、共性红线、自检。
- 10 个 reference 文件存在，每个文件只服务一种图型。
- 触发某类图时，薄内核明确要求读取对应 reference；读取失败必须 fail-closed，不画伪专业图。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`
通过。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 成功。
- 同步后的 AGENTS 中能看到薄内核与 reference 索引，但不会出现全部 reference 正文。

## 风险与回滚

| 风险              | 影响           | 规避                      | 回滚               |
|-----------------|--------------|-------------------------|------------------|
| reference 未打包   | pipx 升级后读取失败 | package-data + 测试       | 恢复单文件 `SKILL.md` |
| 模型忘记读 reference | 仍可能画成卡片堆     | 内核写硬门禁 + 测试覆盖关键短语       | 临时把对应图型规则放回内核    |
| 过度拆分导致查找成本高     | 规则不好维护       | 每个 reference 保持固定模板与短自检 | 合并相邻图型 reference |
| 同步器行为误伤 AGENTS  | 全局提示词异常      | 先改测试，再 agents-sync 验证   | 回退同步器改动          |

## 待用户确认

是否按本设计进入 TDD 实施。
