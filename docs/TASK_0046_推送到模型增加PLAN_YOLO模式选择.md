# /TASK_0046 推送到模型增加 PLAN/YOLO 模式选择（Vibe + Design）

> 前置关联文档：
> - `docs/TASK_0037_推送缺陷的附件问题.md`（当前推送到模型的交互与补充阶段行为）
> - `docs/TASK_0041_模型回复底部快捷回复按钮.md`（“已推送到模型”回显风格参考）

## 1. 背景（现状）

当前在任务详情点击「🚀 推送到模型」后：
- 有时会直接推送；
- 有时会进入“补充任务描述（跳过/取消/发附件）”再推送。

但用户无法在推送前明确选择“本次让模型以什么工作方式执行”（例如更稳妥的先规划、或更直接的执行），且提示词里也缺少对该模式的明确声明，容易导致模型输出风格不一致。

## 2. 目标（用户期望）

点击「🚀 推送到模型」后：
1) 先通过底部菜单选择两种模式之一：`PLAN` / `YOLO`
2) 提示词中必须明确声明本次进入哪种模式（用户可在 Telegram 预览中直接看到）
3) 并且该“模式声明”要 **替换掉** 现有提示词首行的「进入 vibe/测试阶段…」前缀（不再出现该前缀）

## 3. 交互定义（已确认 ✅）

> 用户确认：“3 替换掉进入 vibe/测试阶段这段前缀；其他按推荐”

1) 模式语义（✅）
- `PLAN`：先给出清晰可执行的计划，再执行（偏稳妥）
- `YOLO`：默认直接执行（偏快）

2) 触发频率（✅）
- 每次在 `research/test` 状态点击「🚀 推送到模型」都要先选择一次模式（不记忆上次选择）

3) 模式声明位置（✅）
- 使用“模式声明”替换掉原本提示词首行的「进入 vibe/测试阶段…」前缀
- 目标效果：提示词首行以 `PLAN/YOLO` 开头，且能让模型明确本次模式

4) 与“补充任务描述”的关系（✅）
- 先选模式，再进入现有补充流程（若当前场景需要补充）

## 4. 信息架构（主流程）

```text
任务详情
  └─ 点击「🚀 推送到模型」
       └─ 选择模式：PLAN / YOLO（+ 取消）
            ├─ 若需要补充：进入补充任务描述（跳过/取消/发附件）
            └─ 推送到模型 → Telegram 回显预览（首行包含 PLAN/YOLO，且不再出现“进入vibe/测试阶段”）
```

## 5. 验收标准（AC，可测试）

1) `research/test` 状态点击「🚀 推送到模型」后，必定先出现模式选择菜单：PLAN / YOLO / 取消。
2) 选择任一模式后才会推送到模型（或进入补充阶段后再推送）。
3) 最终推送给模型的提示词中，必须清晰声明本次模式为 PLAN 或 YOLO。
4) 最终提示词首行不再出现「进入vibe阶段…」或「进入测试阶段…」这类前缀，而改为模式声明。
5) Telegram 的“已推送到模型”预览中也能看到该模式声明（避免用户误解）。
6) 选择“取消”会退出本次推送流程，不会触发推送。
7) `done` 状态点击「🚀 推送到模型」保持现状（推送 `/compact`），不引入模式选择，避免破坏 `/compact` 指令语义。

## 6. 参考资料（官方/可验证）

- Telegram Bot API：ReplyKeyboardMarkup（用于底部菜单选择模式）  
  https://core.telegram.org/bots/api#replykeyboardmarkup
- Telegram Bot API：InlineKeyboardMarkup（任务详情里的推送按钮）  
  https://core.telegram.org/bots/api#inlinekeyboardmarkup

## 7. 开发设计（Design）

### 7.1 现状定位（可验证）

- 任务详情入口：`bot.py` `_build_task_actions()` 内联按钮 `task:push_model:{task_id}`  
- 回调处理：`bot.py` `on_task_push_model()`（当前 `research/test` 会直接进入补充阶段，`done` 会直接推送 `/compact`）  
- 补充阶段：`TaskPushStates.waiting_supplement` → `on_task_push_model_supplement()`  
- 推送与提示词构造：`_push_task_to_model()` → `_build_model_push_payload()`（原先 `research/test` 首行使用 `VIBE_PHASE_PROMPT`，本任务改为模式声明行）
- FSM 状态：`tasks/fsm.py` `TaskPushStates` 已存在 `waiting_choice`（当前未使用）

### 7.2 方案对比（至少两种）

方案 A（推荐🌟）：**ReplyKeyboard 菜单 + 复用 `waiting_choice`**
- 点击「🚀 推送到模型」后，进入 `waiting_choice`，底部菜单展示 `PLAN / YOLO / 取消`
- 选择后再进入既有“补充任务描述”流程（需要补充时），或直接推送
- 优点：与现有补充阶段一致（都用 ReplyKeyboard），支持编号选择，输入容错好；改动面小
- 缺点：会多发一条引导消息；键盘会短暂占用输入区

方案 B：**InlineKeyboard 二次确认（按钮式选择 PLAN/YOLO）**
- 点击「🚀 推送到模型」后，机器人在聊天里发一条消息，附带内联按钮 `PLAN / YOLO / 取消`
- 优点：交互更“点按化”，不占用输入键盘
- 缺点：需要额外维护 callback 路由与清理；与现有“补充任务描述” ReplyKeyboard 交互风格不一致

结论：采用方案 A（推荐🌟），保持交互一致性与最小改造风险。

### 7.3 状态机与流程（推荐实现语义）

```text
点击「🚀 推送到模型」
  ├─ 若任务状态=done：保持现状，直接推送（/compact），不引入模式选择（避免破坏 /compact 指令语义）
  └─ 若任务状态=research/test：
       进入 waiting_choice（记录 task_id/chat_id/actor/origin_message）
         ├─ 选择 PLAN → 写入 push_mode=PLAN → 进入 waiting_supplement →（跳过/补充/取消）→ 推送
         ├─ 选择 YOLO → 写入 push_mode=YOLO → 进入 waiting_supplement →（跳过/补充/取消）→ 推送
         └─ 取消 → 清空状态 → 退出本次推送
```

### 7.4 提示词改造点（满足“替换前缀”）

目标：推送给模型的提示词**首行**不再以「进入vibe阶段…/进入测试阶段…」开头，而改为**模式声明**，且首行以 `PLAN` 或 `YOLO` 开头。

推荐首行模板（示例）：
- `PLAN 模式：先给出清晰可执行的计划，再执行。{AGENTS_PHASE_SUFFIX}`
- `YOLO 模式：默认直接执行。{AGENTS_PHASE_SUFFIX}`

说明：
- 仅替换“首行前缀”，保留原本的任务字段结构（任务标题/编码/描述/补充/附件/历史）不变。
- Telegram 的“已推送到模型”预览复用现有逻辑，因此会自动展示该首行。

### 7.5 用例设计（Checklist，像测试一样）

1) `research/test` 状态点击「🚀 推送到模型」：必定先出现 `PLAN/YOLO/取消` 菜单，且 FSM 进入 `waiting_choice`。  
2) 选择 `PLAN`：进入补充阶段；最终推送提示词首行以 `PLAN` 开头，且不再出现「进入vibe/测试阶段…」前缀。  
3) 选择 `YOLO`：进入补充阶段；最终推送提示词首行以 `YOLO` 开头，且不再出现「进入vibe/测试阶段…」前缀。  
4) 在模式选择阶段发送“取消”：退出流程、不推送到模型。  
5) 在模式选择阶段发送无效输入：提示“请选择 PLAN/YOLO/取消”，不进入推送。  
6) `done` 状态点击「🚀 推送到模型」：保持原行为（直接推送 `/compact`），避免引入模式导致指令失效。  

## 8. 参考资料补充（官方）

- aiogram FSM（有限状态机）文档（用于 `FSMContext`/`StatesGroup` 的状态管理）  
  https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html

## 9. 开发实现（Develop）

### 9.1 交付内容 ✅

- 点击「🚀 推送到模型」在 `research/test` 状态下新增模式选择：`PLAN / YOLO / 取消`（ReplyKeyboard）
- 选择模式后进入原有“补充任务描述”阶段，再推送到模型
- 推送给模型的提示词首行改为模式声明（以 `PLAN` 或 `YOLO` 开头），并替换掉原「进入vibe/测试阶段…」前缀（预览与推送内容一致可见）
- `done` 状态保持原行为（直接推送 `/compact`），避免破坏既有指令语义

### 9.2 代码变更点（可验证）

- `bot.py`
  - 新增：`PUSH_MODE_PLAN/PUSH_MODE_YOLO`、`_build_push_mode_keyboard()`、`_build_push_mode_prompt()`、`_prompt_push_mode_input()`
  - 新增 FSM 处理：`on_task_push_model_choice()`（`TaskPushStates.waiting_choice`）
  - 调整流程：`on_task_push_model()` / `on_task_push_model_fill()` 进入 `waiting_choice` 而非直接补充
  - 提示词首行：`_build_model_push_payload(..., push_mode=...)` 在 `research/test` 下输出模式声明行
  - 推送参数透传：`_push_task_to_model(..., push_mode=...)`，`on_task_push_model_supplement()` 从 FSM data 读取并传入
- `tests/test_task_description.py`
  - 更新推送到模型相关用例，补齐模式选择步骤，并确保断言首行以 `PLAN/YOLO` 开头

### 9.3 自测结果（pytest）

```bash
.venv/bin/python -m pytest -q
```

- 结果：`528 passed`（含少量历史警告，未影响本次改动验收）
