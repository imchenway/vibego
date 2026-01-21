# /TASK_0054 PLAN 模式的提示词不对

## 1. 背景（现状）

在 Telegram「🚀 推送到模型」流程中，用户选择 `PLAN` 模式后，推送给模型的提示词首行当前为：

```text
PLAN 模式：先给出清晰可执行的计划，再执行{AGENTS_PHASE_SUFFIX}
```

用户反馈该文案“提示词不对”，期望更简洁——**只需提示“进入 PLAN 模式”即可**，其余行为约束由 `AGENTS.md` 驱动。

## 2. 目标（用户口径）

- `PLAN` 模式首行改为：

```text
进入 PLAN 模式{AGENTS_PHASE_SUFFIX}
```

- 其余提示词结构保持不变（任务标题/编码/描述/补充/附件/历史）。

## 3. 验收标准（AC，可测试）

1) `research/test` 状态推送到模型，选择 `PLAN` 后，payload 第一行以 `进入 PLAN 模式` 开头。  
2) payload 第一行不再出现旧文案 `PLAN 模式：先给出清晰可执行的计划`。  
3) payload 第一行仍不应回退为 `进入vibe阶段...` 或 `进入测试阶段...` 前缀。  
4) 回归测试通过。  

## 4. 变更点（Design + Develop）

- `bot.py`：`_build_model_push_payload()` 中 `PUSH_MODE_PLAN` 分支
  - `phase_line` / `tail_prompt` 统一改为 `进入 PLAN 模式{AGENTS_PHASE_SUFFIX}`
- `tests/test_task_description.py`
  - 更新推送到模型（PLAN 模式）首行断言

## 5. 参考资料（可验证）

- 代码实现位置：`bot.py` `_build_model_push_payload()`  
- 回归用例：`tests/test_task_description.py`（推送到模型 PLAN 场景断言）  

## 6. 自测

```bash
pytest -q
```

