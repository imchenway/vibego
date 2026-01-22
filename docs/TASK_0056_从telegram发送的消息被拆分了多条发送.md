# /TASK_0056 从 telegram发送的消息被拆分了多条发送（PLAN）

## 1. 背景（现状）

用户在 Telegram 中粘贴一大段日志时，客户端可能会把文本拆分成多条消息发送。  
vibego 当前会把每条消息当作一次独立 prompt 推送到模型，导致：

- 连续出现多条“💭 思考中…sessionId …”确认消息
- 实际上用户希望“这次粘贴”被当作一条输入处理

## 2. 目标（用户口径）

仅对「普通对话文本 → 推送到模型」生效：

- 当单条文本接近 Telegram `sendMessage` 上限时，自动启用“长文本粘贴聚合”
- 在短时间窗口内将多条拆分文本合并为一个整体
- 将合并后的内容自动落盘为“本地附件文件”，并按“附件列表 → 文件路径”的格式推送给模型（效果等同于用户真的发了一个附件）
- 最终只触发一次推送（只出现一次 ack）

> 决策记录：1A（仅普通对话文本）/ 2C（仅接近上限才聚合）/ 3A（自动转文件并在提示词里给出路径）✅

## 3. 验收标准（AC，可测试）

1) 当用户粘贴长日志导致 Telegram 拆分多条文本时，最终仅推送一次到模型（避免重复 ack）。  
2) 推送给模型的提示词包含 `附件列表`，并显示文件路径（`→ <path>`）。  
3) 落盘文件内容为多段文本的拼接结果（不丢失内容）。  
4) 普通短消息（未接近上限）不受影响，仍按原逻辑直接推送。  
5) 回归测试通过。  

## 4. 变更点（Design + Develop）

- `bot.py`
  - 新增“长文本粘贴聚合”配置项（阈值/延迟）与状态缓存
  - `on_text()` 在进入 `_handle_prompt_dispatch()` 前增加聚合判断
  - 聚合完成后将文本落盘为附件文件，并复用 `_build_prompt_with_attachments()` 生成“附件列表”提示词
- `tests/test_task_description.py`
  - 新增用例：长文本拆分聚合为单次 dispatch + 附件文件内容校验
  - 新增用例：短文本不触发聚合

## 5. 可配置项（可选）

通过环境变量可调参：

- `ENABLE_TEXT_PASTE_AGGREGATION`：是否启用（默认开启）
- `TEXT_PASTE_NEAR_LIMIT_THRESHOLD`：触发阈值（默认 3500）
- `TEXT_PASTE_AGGREGATION_DELAY`：聚合等待窗口（默认 0.8 秒）

## 6. 参考资料（官方/可验证）

- Telegram Bot API：sendMessage（文本限制等）  
  https://core.telegram.org/bots/api#sendmessage
- Telegram Bot API：sendDocument（文件消息）  
  https://core.telegram.org/bots/api#senddocument

## 7. 自测

```bash
python3.11 -m pytest -q
```

