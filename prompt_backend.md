# 后端 AGENTS.md 生成提示词

你将以【严格证据模式（Strict Evidence Mode）】执行任务：为当前 Java 后端仓库生成/更新根目录 ./AGENTS.md，用于后续低幻觉、TDD
优先、测试资产驱动的开发协作。

你必须先阅读并遵守：

- $HOME/.config/vibego/AGENTS.md（如存在）
- 当前目录下所有 AGENTS.md（若冲突，以当前目录最近的为准；若仍不明确 => 失败关闭并列出冲突点）

【写入范围限制】

- 允许：只读侦查命令（ls/tree/find/rg/grep/cat/sed -n/git status 等）
- 允许写入：仅 ./AGENTS.md（可选：./AGENTS.evidence.json，仅当规约允许新增文档文件时）
- 禁止：修改任何业务代码/配置/构建文件/依赖锁文件/CI 文件；禁止 git commit/push

【严格证据规则（违反即失败）】

1) 任何“仓库事实”（模块职责、入口类、构建/运行/测试命令、profile、依赖服务、迁移工具、CI 门禁、代码规范）必须给出仓库内 Evidence：
    - Evidence 格式：文件路径 + 锚点（类名/关键字/段落/脚本名）；可选 snippet（<=20 行）
2) 找不到证据：只能写 TODO；禁止用经验/常识推断补全；禁止“通常/一般/应该是/大概率”
3) 所有命令必须标注状态：
    - ✅ Verified：你在本次任务中实际执行且成功（记录命令与关键输出摘要）
    - ⚠️ Unverified：在 README/CI/脚本/构建文件中存在但你未执行（写原因）
    - ❌ Unknown：仓库中找不到可靠来源（写 TODO）
    - 🔴 Failed：你尝试执行但失败（记录失败摘要；不修代码）

【执行步骤（必须按顺序）】
A) Repo Discovery（只读扫描）

- 列出根目录与两层结构（tree 或 find）
- 读取：README.md（如有）、构建文件（pom.xml / build.gradle*）、wrapper（mvnw/gradlew）、CI（.github/workflows / Jenkinsfile /
  .gitlab-ci.yml）、docker-compose/Makefile/scripts（如有）
- 搜索入口类：@SpringBootApplication 或 main 方法
- 搜索配置：application*.yml/properties、bootstrap.*、nacos/apollo/consul 等关键字
- 搜索数据访问与迁移：JPA/MyBatis/MyBatis-Plus、Flyway/Liquibase、SQL migration 目录
- 搜索质量工具：checkstyle/spotbugs/pmd/spotless/jacoco/formatter 等
- 搜索测试：src/test、testcontainers、集成测试标记、mock 框架

B) Facts Extraction（形成证据索引）

- 形成 facts 与 commands 的 evidence 索引：每条都有 evidence[]
- 冲突裁决优先级：CI/脚本 > README > 构建配置 > 代码
- 仍冲突 => 写入 Conflicts，并对该结论失败关闭（不下定论）

C) 生成 ./AGENTS.md（中文，证据表驱动）
AGENTS.md 必须严格按顺序包含（不可缺）：

0) Non-negotiables

- 严格证据模式、Fail-Closed、写入范围限制、冲突裁决规则
- 若与现实冲突，先更新本文件再继续开发
- 测试用例与单元测试属于“验证资产”，后续需求与修 Bug 均不得削弱其回归保护能力

1) Facts Table

- Fact | Value | Evidence

2) Repo Map

- Path | Responsibility | Evidence
- 无证据写 TODO

3) Commands Table

- Purpose | Command | Status | Evidence | Notes
- 必须覆盖：build、test、run（local profile）、lint/format（如有）、db migrate（如有）、docker compose（如有）
- 命令来源必须可追溯（CI/README/scripts/pom/gradle）

4) Config & Environments

- profiles、配置文件位置、配置中心、环境变量、依赖服务、敏感信息规则
- 不得凭空列 env 名

5) Coding Standards

- 从现有仓库归纳：包结构/分层、异常策略、日志字段、DTO/返回体规范、事务边界
- 每条必须 evidence，否则 TODO

6) Database & Migration

- ORM/Mapper、迁移工具、索引/回滚约束
- 每条必须 evidence，否则 TODO

7) Testing & Quality Gates（必须拆成两层）
   7.1 Current Evidence

- 当前测试框架
- 当前覆盖率工具
- 当前 coverage 阈值（若存在）
- 当前 test/typecheck/build 证据命令
- 当前 CI / quality gate 现状
  7.2 Required Engineering Gate（工程强制门禁，不当作仓库现状事实）
- baseline test 必须全绿后，才能开始新需求实现
- baseline coverage 目标：默认 100%（优先 line + branch；若工具不支持 branch，则 line=100% 且测试策略覆盖所有分支与异常路径）
- 新需求必须遵循 TDD：
    1. 先写测试
    2. 先运行并确认因“功能未实现”而失败
    3. 再写生产代码
    4. 再跑全量 test + coverage
    5. 连续两次通过且 coverage 恢复到 100%
- 不得为了达标新增或扩大 exclusions
- 测试覆盖场景必须尽可能多而全：正常/边界/异常/状态/幂等/权限/交互/并发（适用时）

8) Definition of Done

- 必须通过哪些命令
- 何时需要补测试 / 迁移 / 文档
- 明确写入：
    - 新需求实现前：baseline 必须全绿
    - 新需求必须先补测试并确认失败
    - 实现后必须双轮通过
    - 若无法达到 Required Engineering Gate，必须 fail-closed

9) Vibe Coding Workflow（适配 PLAN / YOLO）

- 明确工作流仍为：vibe -> design -> develop
- 但 develop 阶段必须强制执行：
    - 影响面分析
    - baseline gate
    - TDD gate
    - implementation gate
    - self-test gate
    - bounded auto-repair loop（默认最多 5 轮）

10) Guardrails
    10.1 Repo-specific Guardrails（必须 evidence；无证据则 TODO）

- 公共 API 兼容策略、错误码规范、数据库变更流程、配置中心/密钥管理、日志与追踪字段要求、分层依赖约束等
  10.2 Universal Safety Guardrails（通用安全护栏，不当作仓库事实）
- 禁止未经明确要求：新增/升级依赖、改构建链/CI、改 public API 合约、改默认配置与生产参数、提交密钥、进行大范围重命名/重构
- 任何涉及 DB schema 的变更必须有迁移与回滚说明（若仓库无迁移工具 => 写 TODO 并停）
- 不得削弱测试资产与回归保护能力

11) Common Playbooks

- 新增接口 / 改 DB / 加定时任务或消息 / 排查线上问题
- 若仓库无证据则 TODO
- 每个 playbook 必须体现：先基线 -> 先补测试 -> 再实现 -> 再双轮验证

12) TODO & Known Issues

- 所有未知点/冲突点/失败命令集中列出：缺什么证据、建议补哪份文件/输出

（可选）若规约允许，生成 ./AGENTS.evidence.json：

- 将 Facts / Commands / Testing & Quality Gates / Guardrails 的 evidence 结构化输出
- 对测试部分同时记录：
    - current_evidence_gate
    - required_engineering_gate

D) Minimal Verification

- 在不引入任何改动前提下，尝试执行最轻量的校验命令（优先 wrapper 的 build/test 或 -v）
- 成功才标 ✅ Verified；失败标 🔴 Failed，不修代码，只写 Known Issues，并更新命令状态

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
- 执行过的侦查/验证命令清单与结果摘要（成功/失败）
- TODO/冲突/未知点清单（每条必须写“缺少的证据来源”）
  现在开始执行。