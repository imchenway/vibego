# TASK_20260629_004_vibe-diagram 文本溢出与响应瘦身

## 任务背景

- 用户反馈：`docs/TASK_20260629_003_worker启动失败日志截断.html` 中节点文字溢出边框；同时 HTML 之外的聊天响应仍然过长。
- 根本目标：HTML 图要成为主要信息载体，聊天只做交付信封、验证摘要和待执行动作；图内正文必须在桌面与 390px 移动宽度下可读。

## 现状与根因

1. HTML 溢出根因：当前图使用 SVG `<text>` 直接承载长中文句子。SVG `<text>` 默认不自动换行，节点矩形宽度固定，导致长句越过边框。
2. 聊天过长根因：已有协议只对 HTML-only 场景强制“文本信封”，但普通 HTML 图交付没有明确压缩聊天回复；AGENTS 的 bug
   修复/TDD/最终字段等要求会把测试矩阵、影响点和原因再次展开到聊天里。
3. 不建议通过大幅删除 AGENTS.md 降噪：AGENTS 负责触发、阶段门禁和交付边界；真正应该瘦身的是“HTML 交付后的聊天输出契约”。

## 修法

- 重绘 `TASK_20260629_003_worker启动失败日志截断.html`：移除 SVG `<text>` 主图，改用 HTML/CSS flow node、decision node 和
  end/start event，统一使用 `overflow-wrap: anywhere`、自适应高度和移动端纵向布局。
- 更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增“HTML 图交付后的文本压缩规则”和“SVG 节点文字规则”。
- 更新 `AGENTS-template.md`：新增“HTML 图交付后的文本压缩规则”，明确普通 HTML 图交付也要短回复。
- 更新 `AGENTS.md` Facts Table：补充当前仓库事实与测试锚点。
- 现场重启暴露 `Dispatcher.start_polling()` 在 Telegram 网络超时时会让 master 进程退出；同轮补充 `_run_master_polling()`
  重试保护，避免修复部署后 master 假启动。

## 受影响目录/文件

- `AGENTS-template.md`：新增 HTML 图交付后聊天瘦身契约。
- `AGENTS.md`：补充证据表，保持项目事实与模板/skill 现实一致。
- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增交付后文本压缩与 SVG 文本换行门禁。
- `docs/TASK_20260629_003_worker启动失败日志截断.html`：重绘为可换行 CSS 节点主图。
- `master.py`：补充 master polling 启动网络超时重试保护。
- `tests/test_agents_template_migration.py`：新增 AGENTS 模板文本压缩契约测试。
- `tests/test_builtin_skills_injection.py`：新增 skill 文本压缩、SVG 文本换行和当前 HTML 可换行节点测试。
- `tests/test_master_network_resilience.py`：新增 master polling 启动网络超时不退出的回归测试。

## 契约变更

- 普通 HTML 图交付：最终聊天回复只保留 HTML 路径/链接、验证摘要和待执行动作；不得重复展开 HTML 已承载的分析、证据链、测试矩阵、风险回滚。
- HTML-only 模式：仍是更严格的信封模式，不受本次放宽或替代。
- SVG 文本：禁止把长句直接放进单个 SVG `<text>` 节点；必须用 `<tspan>` 分行、`foreignObject`、短编号标签，或优先改用 HTML/CSS
  节点。
- AGENTS.md：不做大幅删减，只新增“输出瘦身边界”，避免削弱 TDD、证据、阶段门禁等长期约束。

## 测试矩阵

| 场景                                 | 测试                                                                            | 预期                                           |
|------------------------------------|-------------------------------------------------------------------------------|----------------------------------------------|
| AGENTS 模板要求 HTML 后短回复              | `test_agents_template_compresses_text_after_html_delivery`                    | 包含交付信封、验证摘要、待执行动作规则                          |
| vibe-diagram skill 要求普通 HTML 交付短回复 | `test_vibe_diagram_delivery_reply_must_stay_concise`                          | 不再只靠 HTML-only 模式约束                          |
| SVG 文本溢出门禁                         | `test_vibe_diagram_svg_text_wrapping_rules`                                   | 禁止长句直接进单个 SVG `<text>`                       |
| 当前 HTML 图不再使用 SVG text             | `test_worker_start_failure_diagram_uses_wrapping_html_nodes`                  | 含 `.flow-node` / `.decision-node`，无 `<text>` |
| Master polling 启动阶段 Telegram 超时    | `test_master_polling_retries_after_startup_network_timeout`                   | master 不直接退出，延迟后重试                           |
| 既有 worker 启动失败修复回归                 | `tests/test_worker_health_boot_id.py tests/test_master_network_resilience.py` | 不回退日志修复                                      |
| HTML 桌面/移动端可视化自检                   | Playwright + 系统 Chrome，1280px 与 390px                                         | 无横向溢出、无 SVG `<text>`、无节点 overflow            |

## 实施顺序

1. TDD 红灯：先新增四个测试，确认协议与当前 HTML 图均不满足。
2. 更新模板与 skill：补足普通 HTML 图交付后短回复与 SVG 文本规则。
3. 重绘 HTML：移除 SVG `<text>`，改为 CSS 可换行节点与移动端纵向布局。
4. 回归验证：运行新增测试、vibe-diagram 相关测试、worker 启动失败相关测试、语法检查与 doctor。
5. 同步运行环境并重启：将源码修复同步到当前 pipx 运行环境后重启 master；若 Telegram 网络仍不可达，worker 启动失败必须显示本次
   boot 根因。

## 风险与回滚

- 风险：聊天回复过短可能让用户误以为缺少细节。缓解：完整分析进入 HTML/docs，聊天提供路径。
- 风险：CSS 图不如 SVG 精确。缓解：流程关系用 DOM 顺序与箭头节点表达，优先保证文字可读与移动端适配。
- 风险：Telegram 网络持续不可达时，master 会保活并重试，worker 仍可能启动失败；这属于外部网络状态，不应被伪装为已修复。
- 回滚：恢复 `SKILL.md`、`AGENTS-template.md`、`AGENTS.md` 与 HTML 文件到上一版；测试会重新暴露文字过多和 SVG 溢出问题。

## 完成状态

- [x] 新增失败测试并观察红灯。
- [x] 更新 AGENTS 模板与 vibe-diagram skill。
- [x] 重绘当前启动失败 HTML 图，避免 SVG 文本溢出。
- [x] 完成聚焦测试、语法检查、doctor、桌面/移动 HTML 可视化自检。
- [x] 已同步 pipx 当前运行环境并重启 master。
- [ ] worker 仍因 Telegram 网络请求失败停在 stopped；本轮已保证失败提示显示本次 boot 根因，需用户检查代理或网络策略后重试启动。
