# VG_TASK_0001 任务编码前缀隔离治理（DEVELOP）

## 1. 背景

- 问题：未正式创建任务时的自由编码文档会占用 `TASK_XXXX`，与 vibego 任务中心编码冲突。
- 目标：自由编码继续使用 `TASK_XXXX`；vibego 任务中心改为独立前缀，避免互占。

## 2. 已确认决策

- vibego 前缀：`VG_TASK_XXXX`（4 位）✅
- 自由编码：继续 `TASK_XXXX` ✅
- vibego 链路严格使用 `VG_TASK_*`，不再自动纠错旧格式 ✅
- 不处理历史任务数据，不做批量迁移/重排 ✅
- 不引入 `task_id_map.json` 映射文件 ✅
- 一次性全量切换（非灰度）✅

## 3. 实施内容

### 3.1 任务服务（`tasks/service.py`）

- 新建 vibego 任务 ID 规则：`VG_TASK_` 前缀。
- 创建任务时改为生成 `VG_TASK_0001` 形式。
- 任务 ID 规范化改为严格校验（仅接受 `VG_TASK_[A-Z0-9_]+`）。
- 历史 ID 自动迁移逻辑冻结（不再自动改写旧 TASK 数据）。
- 序列号按“项目 + 前缀”隔离，保证不同前缀互不影响。

### 3.2 机器人交互层（`bot.py`）

- 任务 ID 校验改为 `VG_TASK_*`。
- 快捷命令改为 `/VG_TASK_XXXX`。
- `TASK_ID_USAGE_TIP`、命令示例、摘要命令示例统一切换到 `VG_TASK_0001`。
- 模型文本中的任务 ID 提取改为识别 `VG_TASK_*`。
- Markdown 保护继续兼容 `TASK_*` 与 `VG_TASK_*`（避免下划线解析问题）。

### 3.3 测试

- 同步更新任务相关测试样例 ID 到 `VG_TASK_*`。
- 调整“旧 ID 迁移”测试为“历史数据不迁移”的新断言。

## 4. 验收结果

- 执行命令：`PYTHONPATH=. pytest -q`
- 结果：`619 passed, 6 warnings` ✅

## 5. 影响与说明

- 新创建的 vibego 任务统一为 `VG_TASK_*`。
- 历史 `TASK*` vibego 数据不会被自动改写（符合“不做历史处理”决策）。
- 若需要访问历史旧 TASK 任务，需后续单独制定迁移或兼容方案（当前版本不启用）。
