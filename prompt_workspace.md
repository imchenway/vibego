# 根目录总规约 AGENTS.md 生成提示词

你将以【严格证据模式（Ultra Strict Evidence Mode）】执行任务：在当前工作区根目录生成/更新
./AGENTS.md（“总规约/工作区规约”），用于跨多个子项目（例如 web 管理端、微信小程序、后端服务）的工程化协作。目标：最低幻觉、可审计、TDD
优先、测试资产驱动。

【必须先做】

0) 确认工作区根目录：执行 `git rev-parse --show-toplevel` 并切到该目录
1) 读取并遵守规约优先级：
    - 先读：$HOME/.config/vibego/AGENTS.md（如存在）
    - 再读：当前工作区内所有 AGENTS.md（包括根目录与子目录，如存在）
    - 冲突处理：以“离当前工作目录最近的 AGENTS.md”为准；若仍冲突或不明确 => 失败关闭（停止并输出冲突点与需要人工裁决的问题）
2) 写入范围限制：
    - 允许：只读侦查命令（ls/tree/find/rg/grep/cat/sed -n/git status 等）
    - 允许写入：仅 `./AGENTS.md`（可选：`./AGENTS.workspace.evidence.json`，仅当规约允许新增文档文件时）
    - 禁止：修改任何业务代码、配置、构建文件、锁文件、CI 文件、脚本内容；禁止 git commit/push

【核心安全原则（违反即失败）】
A) Evidence-first：任何“工作区事实”（子项目清单、目录结构、规约位置、冲突裁决规则）必须有仓库内证据：

- Evidence 格式：path + anchor（关键字/文件名/目录名）；可选 snippet（<=20 行）
  B) Fail-Closed：找不到证据 => 只能写 TODO；禁止用经验/常识推断补全
  C) No Subproject Fact Fabrication：根目录“总规约”禁止写具体子项目的 build/run/test 命令、端口、env
  变量名、框架版本等，除非这些信息在仓库中有明确证据且属于跨项目统一事实
  D) Drill-down Required：任务一旦涉及某个子项目目录下文件，必须先读取该子项目根目录（或离目标文件最近的）AGENTS.md，以及同目录的
  AGENTS.evidence.json（若存在），再进入实现；否则不得实现

【执行流程（必须按顺序）】

1) Repo Snapshot（只读）

- `git status -sb`
- 根目录列表：`ls -la`
- 两层结构：优先 `tree -a -L 2`；无 tree 用 `find . -maxdepth 2 -print`
- 记录所有可能的子项目线索文件/目录：README*、apps/、packages/、web/、frontend/、miniprogram/、backend/、services/ 等（必须保留证据）

2) Workspace Inventory（只读，证据驱动识别“子项目”）

- 识别每个子项目“候选根目录”：满足以下任一条件的目录可视为子项目候选（以证据为准）：
    - 存在 package.json / project.config.json / app.json / pom.xml / build.gradle* / go.mod / pyproject.toml 等 manifest
    - 存在子项目 AGENTS.md
- 对每个候选子项目，输出发现记录：name/path、detected manifests、是否存在子项目 AGENTS.md / AGENTS.evidence.json
- 无法判断类型就标 TODO

3) 生成 ./AGENTS.md（中文，证据表驱动；总规约必须短而硬、可审计）
   文档必须包含并按以下顺序输出（不可缺）：

0) Non-negotiables

- 严格证据模式、失败关闭、写入范围限制
- 规约优先级与冲突裁决（全局 -> 根目录总规约 -> 子项目规约 -> 更近目录规约）
- 涉及哪个目录，就必须下钻读取该目录最近的 AGENTS.md / AGENTS.evidence.json
- 任何命令执行必须显式 `cd <project>`，禁止在根目录盲跑子项目命令
- 测试用例与单元测试属于跨项目“验证资产”，后续需求或修 Bug 不得削弱其防回归能力

1) Workspace Facts Table

- Fact | Value | Evidence
- 工作区根目录识别结果
- 子项目数量/路径
- 规约文件位置索引
- 只写能证实的事实；其余 TODO

2) Workspace Map

- Path | Role | Evidence
- 每个子项目的相对路径、用途（无法证实则 TODO）、manifest 证据
- 共享资源目录（docs / scripts / deploy 等，如有）

3) Cross-Project Guardrails
   3.1 Repo-specific Guardrails（必须 evidence；无证据则 TODO）

- API 契约文档位置、错误码规范位置、统一鉴权策略位置、版本策略位置等
  3.2 Universal Safety Guardrails（通用安全护栏，不作为仓库事实）
- 禁止未经明确许可：新增/升级依赖、改构建链/CI、改 public API 合约、改默认配置与生产参数、提交密钥、进行大范围重命名/重构
- DB schema 变更必须：迁移 + 回滚说明 + 兼容策略（若迁移工具/流程不明 => TODO + 停止实现）
- 跨端变更必须：后端兼容前端/小程序或提供版本化策略；不得“先改后端再说”
- 不得削弱测试资产与回归保护能力

4) Cross-Project TDD Workflow（这是核心，必须非常硬）

- 工作流仍然是：vibe -> design -> develop
- 无论 PLAN 还是 YOLO，只要进入 develop，都必须执行以下跨项目门禁：
  4.1 Impact Analysis
    - 先识别受影响子项目与目录
    - 逐一引用对应子项目 AGENTS.md / AGENTS.evidence.json 路径
      4.2 Baseline Gate
    - 在每个受影响子项目中，优先执行其 evidence.json 中 status=✅Verified 的 test / coverage / typecheck / build 命令
    - 若任一受影响子项目 baseline 未通过，或未达到其 AGENTS.md 中 `Required Engineering Gate`，禁止开始新需求实现，必须先修复
      baseline
      4.3 TDD Gate
    - 新需求必须先补测试并确认失败，再写代码
    - 未完成“先失败”验证，不得进入生产代码实现
      4.4 Implementation Gate
    - 仅在明确目录下做最小改动
    - 禁止跨项目顺手修改无关文件
      4.5 Self-Test Gate
    - 每个受影响子项目在各自目录执行各自 test + coverage + typecheck/build（如适用）
    - 连续两次通过且 coverage 达到其 Required Engineering Gate
      4.6 Auto-Repair Loop
    - 允许自动修复，但必须有界
    - 默认最多 5 轮
    - 超过 5 轮仍未达标 => fail-closed，输出阻塞清单

5) Contract & Integration

- 只写流程与要求，不写具体 API 细节（除非仓库有统一契约文件证据）
- 必须包含：
    - 当 API / 字段变更：要求提供兼容策略（版本化 / 可选字段 / 默认值）
    - 当鉴权 / 签名变更：要求三端同步与回归
    - 当配置项 / 环境变量变更：必须同步更新对应项目文档与示例（若存在）

6) Command Orchestration Rules

- 根目录只定义规则，不定义子项目具体命令：
    - 所有命令必须写成：`(cd <project> && <command>)`
    - 优先使用子项目 AGENTS.evidence.json 中 status=✅Verified 的命令
    - 若命令 Unknown / Unverified：必须先补证据或在 PLAN 中提出最小验证计划
- 若仓库存在根目录统一脚本（Makefile / scripts），可列出（需 evidence），并声明其适用范围

7) Documentation & Evidence Maintenance

- 规定何时必须更新根目录总规约：新增/移动子项目、子项目规约路径变化、跨项目联调方式变化
- 规定子项目 evidence.json 漂移处理：更新与审计流程
- 可选建议：CI 增加 evidence 校验，但不得要求立即改 CI

8) TODO & Known Issues

- 汇总所有 TODO：子项目类型无法确认、缺少子项目规约、规约冲突点、需要人工裁决项
- 每条 TODO 必须写：缺少的证据来源（哪个文件/哪个命令输出）

（可选）若规约允许新增文档文件，生成 `./AGENTS.workspace.evidence.json`：

- 结构化输出 Workspace Facts / Map / Guardrails / TDD Workflow 的 evidence

4) Minimal Verification（可选，且必须零副作用）

- 仅验证工作区层面的内容：例如确认子项目规约文件存在（ls），确认 manifest 存在（ls）
- 禁止执行会触发依赖安装/构建/联网的操作，除非规约明确允许
- 失败则记录为 Known Issues，不修任何文件

【保留约束（强制）】

- 本次只允许“增量编辑”AGENTS.md，禁止整文件重写。
- 未经我明确授权，禁止删除或改写任何已有章节内容。
- 若必须调整结构，先将旧内容原样迁移到“附录/Legacy”后再新增
  内容。
- 交付时必须给出“保留性自检”：
    1) 原有一级/二级标题是否仍存在；
    2) 是否有删除段落（必须为无）；
    3) 变更仅限新增/追加的位置。

【交付输出】

- 写入/修改文件列表（至少 ./AGENTS.md）
- 执行过的只读侦查/验证命令摘要
- TODO/冲突/未知点清单（每条写缺失证据来源）
  现在开始执行。