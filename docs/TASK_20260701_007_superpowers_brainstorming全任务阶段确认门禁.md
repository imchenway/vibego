# TASK_20260701_007 superpowers:brainstorming 全任务阶段确认门禁

## 背景

用户要求把新的全局协作门禁写入模板：任何会进入方案、计划、实现、验证等下一阶段的任务，都必须先经过 `superpowers:brainstorming` 与用户做交互问答，并且只有用户明确确认方案或确认进入下一阶段后，才结束交互问答。

同一会话连续处理多个任务时，每次修改类动作完成并交付后，必须回到新的交互式起点；上一任务的确认不得跨任务复用。

## 变更

- `AGENTS-template.md:15`：将 `superpowers:brainstorming` 从“需求/方案/复杂设计”升级为“任何任务进入下一阶段前”的门禁。
- `AGENTS-template.md:29-31`：新增阶段确认、同目标确认复用边界、修改类动作闭环后的任务边界复位。
- `/Users/david/.config/vibego/agents/current/AGENTS-template.md`：已同步仓库根模板。
- `tests/test_agents_template_migration.py:101-113`：新增回归断言，覆盖阶段门禁、用户确认、任务复位、禁止跨任务复用确认、旧绕过表达反查。

## 验证记录

- RED：`python3.11 -m pytest -q tests/test_agents_template_migration.py` 预期失败，失败点为缺少新阶段门禁文案。
- GREEN：`python3.11 -m pytest -q tests/test_agents_template_migration.py` → `13 passed`。
- 同步验证：`cmp -s AGENTS-template.md /Users/david/.config/vibego/agents/current/AGENTS-template.md` 后输出 `template synced`。
- 静态检查：`git diff --check -- AGENTS-template.md tests/test_agents_template_migration.py docs/TASK_20260701_007_superpowers_brainstorming全任务阶段确认门禁.html docs/TASK_20260701_007_superpowers_brainstorming全任务阶段确认门禁.md` → exit 0。

## 风险与回滚

- 风险：全局门禁会让后续修改类任务更强调交互确认，可能增加对话轮次。
- 回滚：恢复 `AGENTS-template.md` 中 `Skill routing` 与 `Work contract` 的本次 3 条门禁，并同步回活跃模板；同时移除新增测试断言或按新口径调整。

## 待用户执行事项

请重新同步或重启对应 worker，让活跃会话注入最新模板；本轮不改运行时发送前缀逻辑。
