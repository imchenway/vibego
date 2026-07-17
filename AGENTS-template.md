# Global Agent Kernel

## Hard boundaries

- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求；
- 读取当前目录下所有的 AGENTS.md；
- 输出 docs、任务文档、设计文档和注释使用简体中文；

## Docs memory

- 输出内容沉淀到当前目录的 `/docs`，不存在则创建该目录；已有任务文档优先续写。
- 用户提供任务编号时，文档命名为 docs/任务编号_任务描述.md；未提供时命名为 docs/TASK_YYYYMMDD_XXX_任务描述.md，XXX 为当天递增编号。

## Skill routing
- 任何用户意图被完全澄清之前必须使用 skill： `grilling` + `domain-modeling`，其组合语义等价于 `grill-with-docs`；将已确定结论直接供后续设计、计划和实现复用。
