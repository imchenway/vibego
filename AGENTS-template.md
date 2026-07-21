# Global Agent Kernel

## Hard boundaries

- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求；
- 通读并沉定项目目录下的所有 AGENTS.md、PROJECT-STYLE.md、CODE-GUIDELINES.md、DESIGN.md，厘清并自动维护好部署架构、系统架构、代码风格与通用组件、 UI/UX设计等；
- 输出 docs、任务文档、设计文档和注释使用简体中文；

## Docs memory
- 通过任务编号或描述定位任务的历史时，必须优先使用当前目录 /docs 中最新、最完整的主任务文档，而非仅依赖用户提示词片段；
- 为保留长期记忆，任务阶段切换时都需要旁路更新 `/docs`，不存在则创建该目录；已有任务文档优先续写。
- 用户提供任务编号时，文档命名为 docs/任务编号_任务描述.md；未提供时命名为 docs/TASK_YYYYMMDD_XXX_任务描述.md，XXX 为当天递增编号。

## Skill routing
- 每个新任务都必须无条件先执行 `grilling` + `domain-modeling`，不得因自行判断用户意图已经清晰而跳过，勇于挑战质疑用户需求，识别 XY 问题、路径弊端和更优解。无论任何设计或建议都需要明确指明优缺点；必须完成这两个 skill 规定的澄清与用户确认流程后，才能进入设计、计划或实现。
