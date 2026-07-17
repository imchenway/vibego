# Global Agent Kernel

## Hard boundaries

- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求；
- 读取当前目录下所有的 AGENTS.md；
- 输出 docs、任务文档、设计文档和注释使用简体中文；

## Docs memory

- 输出内容沉淀到当前目录的 `/docs`，不存在则创建该目录；已有任务文档优先续写。
- 用户提供任务编号时，文档命名为 docs/任务编号_任务描述.md；未提供时命名为 docs/TASK_YYYYMMDD_XXX_任务描述.md，XXX 为当天递增编号。

## Skill routing

- 命中下方 `Vibe / HTML trigger matrix` 时，必须使用 vibe-diagram；

## Vibe / HTML trigger matrix

- 触发：当回答需要说明一个实现对象如何在系统中产生、流转、转换、影响结果，或需要呈现跨角色/模块的因果、时序、状态、数据、证据、前后差异与验收关系时，必须使用
  vibe-diagram；如果纯文本会退化成路径、函数、字段或证据 bullet 清单，也必须改成图。
- 不触发：一个短句即可讲清的定义、翻译改写、简单命令、安装升级、轻量取舍，或用户明确不要图。
- 命中后优先生成/更新项目内单文件 HTML；分析、证据、风险、回滚和测试矩阵写入 HTML 或 docs；聊天只给链接/路径和下一步，链接文字使用
  HTML 内部 `<h1>`。
- HTML 触发、内容承载和聊天信封以 `Vibe / HTML trigger matrix` 为准；Codex 默认给可点击 `file://` 链接和绝对路径兜底。
