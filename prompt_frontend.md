# 前端 AGENTS.md 生成提示词

你将以【严格证据模式（Strict Evidence Mode）】为当前前端仓库生成/更新根目录 ./AGENTS.md，用于后续低幻觉、TDD 优先、测试资产驱动的开发协作。

你必须先阅读并遵守：

- $HOME/.config/vibego/AGENTS.md（如存在）
- 当前目录下所有 AGENTS.md（若冲突，以当前目录最近的为准；若仍不明确 => 失败关闭并列出冲突点）

【写入范围限制】

- 允许：只读侦查（ls/tree/find/rg/cat/sed -n/git status）
- 允许写入：仅 ./AGENTS.md（可选 ./AGENTS.evidence.json）
- 禁止：修改源码/配置/依赖；禁止 git commit/push

【严格证据规则】

- 仓库事实必须有 Evidence（路径+锚点/片段）；无证据只写 TODO；禁止推断补全
- 命令必须标注状态：✅Verified / ⚠️Unverified / ❌Unknown / 🔴Failed

【前端专项侦查清单（按证据优先级）】
A) 入口与框架识别（只读）

- package 管理器与版本：package.json、pnpm-lock.yaml/yarn.lock/package-lock.json、.nvmrc、.node-version、volta 配置、engines
  字段
- 框架/构建工具：Next/Nuxt/Vite/CRA/Vue CLI/Remix/Angular/Svelte 等
- monorepo：pnpm-workspace.yaml、turbo.json、nx.json、lerna.json、workspace:* 依赖、apps/packages 结构
- 类型系统：tsconfig*.json、eslint、prettier、stylelint、commitlint、lint-staged、husky
- 测试：vitest/jest/mocha/playwright/cypress、storybook、msw、test setup 文件
- CI 门禁：.github/workflows / GitLab CI / Jenkins（优先级最高）
- 环境变量：.env.example、next.config.*、vite.config.*、nuxt.config.*、public runtime config（必须证据，不得假设变量名）

B) 生成 ./AGENTS.md（中文，证据表驱动）
AGENTS.md 必须包含：

0) Non-negotiables

- 严格证据模式、写入范围限制、Fail-Closed
- 测试用例与单元测试属于“验证资产”，不得为了当前需求削弱其长期回归价值
- 若与现实冲突，先更新本文件再继续开发

1) Facts Table

- Node 版本、包管理器、monorepo 工具、框架、语言 TS/JS、测试工具、CI
- 必须 evidence

2) Repo Map

- apps / packages / src / components / pages / routes / api 等真实路径与职责
- 无证据写 TODO

3) Commands Table

- 以 package.json scripts 与 CI 为准：
    - install（含是否必须 --frozen-lockfile / --immutable）
    - dev / start
    - build
    - lint / format
    - test / test:watch
    - e2e（playwright/cypress）
    - typecheck（tsc / vue-tsc）
    - storybook（如有）
- 每条命令都要 status + evidence + notes

4) Config & Environments

- .env 规则、运行时/构建时变量、代理与 API base、发布环境差异
- 不得凭空列 env 变量名

5) Coding Standards

- 目录约束、组件规范、状态管理、请求封装、错误处理、通用工具类、日志/埋点策略
- 均需 evidence 或 TODO

6) Testing & Quality Gates（必须拆成两层）
   6.1 Current Evidence

- 当前测试框架（例如 Vitest/Jest）
- 当前覆盖率工具与当前阈值（若存在）
- 当前 e2e 现状
- 当前 typecheck / build / test 命令与状态
- 当前 CI gate 现状
  6.2 Required Engineering Gate（工程强制门禁，不当作仓库现状事实）
- baseline test 必须全绿后，才能开始新需求实现
- baseline coverage 目标：默认 100%（优先 line + branch；若工具不支持 branch，则 line=100% 且测试策略覆盖所有分支与异常路径）
- 若当前仓库现有阈值低于 100%，必须在本节明确标注：
    - “Current Evidence Threshold” 与 “Required Engineering Gate” 不一致
    - 后续需要在 baseline hardening 阶段补齐测试并将门禁提升到 100%
- 新需求必须遵循 TDD：
    1. 先写测试
    2. 先运行并确认因“功能未实现”而失败
    3. 再写代码
    4. 再跑受影响测试
    5. 再跑全量 test + coverage + typecheck/build（如适用）
    6. 连续两次通过且 coverage 恢复到 100%
- 不得为了达标新增或扩大 exclusions
- 测试覆盖场景必须尽可能多而全：
    - 正常路径
    - 边界值
    - 异常分支
    - 状态切换
    - 重复操作 / 幂等
    - 请求失败 / 重试
    - 权限 / 路由守卫
    - 关键交互与不变量
    - 组件间协作（适用时）

7) Definition of Done

- 新需求实现前：baseline 必须全绿
- 新需求必须先补测试并确认失败
- 实现后必须双轮通过
- coverage 恢复到 Required Engineering Gate（默认 100%）
- 若无法达到目标，必须 fail-closed，并输出阻塞原因与证据；不得擅自扩大 exclusions

8) Vibe Workflow（适配 PLAN / YOLO）

- 明确工作流仍为：vibe -> design -> develop
- develop 阶段必须强制执行：
    - 影响面分析
    - baseline gate
    - TDD gate
    - implementation gate
    - self-test gate
    - bounded auto-repair loop（默认最多 5 轮）

9) Guardrails

- 禁止：随意升级依赖、改构建链、改路由约定、改 env 语义、引入新状态管理、弱化测试资产、扩大 coverage exclusions，除非明确要求并有证据/批准
- 对公共页面行为、共享组件契约、请求层语义的改变，必须把测试资产一起更新

10) TODO & Known Issues

- 缺口清单：Node 版本不明、dev 命令不明、e2e 未配置、threshold 与 Required Gate 不一致等
- 每条 TODO 必须写缺失的证据来源

（可选）生成 ./AGENTS.evidence.json：

- 结构化输出 facts / commands / testing_gates / known_issues
- 对测试部分必须同时记录：
    - current_evidence_gate
    - required_engineering_gate

C) Minimal Verification

- 在不修改任何文件前提下，优先跑最轻量命令（例如：typecheck / test / build 中最轻量者）
- 失败不修代码：写 Known Issues，并将该命令标为 Unverified/Failed

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

- 写入文件列表
- 侦查/验证命令与结果摘要
- TODO 清单（每条 TODO 必须写缺失的证据来源）
  现在开始执行。
