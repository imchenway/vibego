# /TASK_0096 通过 Excel 批量创建缺陷

## 1. 背景

用户需要一条新的缺陷创建路径：

1. 在 **任务列表入口** 发起
2. 提供一个 **Excel 模板下载**
3. 用户按模板填写后上传 `.xlsx`
4. 系统先做 **预检**
5. 预检通过后再 **确认批量创建缺陷**

已确认的产品决策：

- 入口位置：**任务列表入口**
- 模板字段：**标准模板**
  - `缺陷标题`
  - `复现步骤`
  - `期望结果`
  - `关联任务编码`
  - `优先级`
- 导入策略：**先校验后确认**
- 依赖方案：**仅加 openpyxl**

## 2. 关键证据

- 当前任务列表交互：`bot.py`（锚点：`_build_task_list_view`, `TaskViewState`, `on_task_list_page`）
- 当前缺陷结构化正文：`bot.py`（锚点：`_build_defect_description`, `_parse_defect_description`）
- 当前“报告缺陷（创建缺陷任务）”流程：`bot.py`（锚点：`on_task_bug_report`, `on_task_defect_report_confirm`）
- 当前发送文档能力：`bot.py`（锚点：`BufferedInputFile`, `send_document`, `answer_document`）
- 当前项目还没有 Excel 依赖：`pyproject.toml`（锚点：`dependencies = [`）

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- Worker 任务列表与导入交互：`bot.py`
- FSM 状态：`tasks/fsm.py`
- 打包依赖：`pyproject.toml`
- 测试：
  - `tests/test_defect_excel_import.py`（新增）
  - `tests/test_task_list_entry.py`
  - `tests/test_task_batch_push.py`
  - `tests/test_defect_report_flow.py`

### 3.2 计划修改的具体单元

1. `tasks/fsm.py`
   - `TaskDefectExcelImportStates`（新增）

2. `bot.py`
   - `TASK_DEFECT_EXCEL_*` callbacks（新增）
   - `TaskViewKind` 新增 `defect_excel_import`
   - `_make_defect_excel_import_view_state`（新增）
   - `_build_defect_excel_template_bytes`（新增）
   - `_build_defect_excel_import_view`（新增）
   - `_build_defect_excel_confirm_summary`（新增）
   - `_build_defect_excel_error_summary`（新增）
   - `_parse_defect_excel_import_file`（新增）
   - `_build_task_list_view`
   - `on_task_defect_excel_import`
   - `on_task_defect_excel_template`
   - `on_task_defect_excel_upload_prompt`
   - `on_task_defect_excel_upload`
   - `on_task_defect_excel_confirm`
   - `on_task_defect_excel_back`

3. `pyproject.toml`
   - 新增依赖：`openpyxl`

### 3.3 直连依赖测试

- `tests/test_defect_excel_import.py`
  - 入口页、模板下载、上传预检、确认创建
- `tests/test_task_list_entry.py`
  - 任务列表入口按钮
- `tests/test_task_batch_push.py`
  - 任务列表底部附加操作兼容
- `tests/test_defect_report_flow.py`
  - 缺陷结构化描述创建口径保持兼容

### 3.4 测试范围升级判断

- 结论：**有限升级**
- 原因：
  - 修改了任务列表公共交互
  - 新增了 `.xlsx` 依赖与导入能力
  - 但未改数据库 schema / 外部 API

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_task_list_entry.py -k 'task_list_view_contains_create_button or build_task_list_view_marks_running_tasks' tests/test_parallel_flow.py -k 'push_model_starts_with_dispatch_target_choice or existing_cli_target_with_multiple_sessions_opens_session_picker' tests/test_task_description.py -k 'push_model_success or push_model_done_push'
```

结果：

```text
1 failed, 1 passed
```

失败原因：

- 既有基线问题：`_continue_push_after_existing_session_selected(...)` 中残留错误的 `callback` 变量引用，导致 `/TASK_0093` 相关用例失败。

完成 Baseline Repair 后再次执行：

```text
2 passed, 230 deselected in 0.17s
```

## 5. TDD 红灯

先新增测试文件：

- `tests/test_defect_excel_import.py`

覆盖：

1. 任务列表出现 `📥 Excel批量创建缺陷`
2. 从任务列表进入 Excel 导入页
3. 下载模板能返回 `.xlsx`
4. 上传合法模板可进入预检确认
5. 上传非法模板会保留在上传态并提示错误
6. 确认后按行创建缺陷并恢复任务列表

首次执行：

```bash
python3.11 -m pytest -q tests/test_defect_excel_import.py
```

结果：

```text
5 failed
```

失败原因符合预期：

- 任务列表还没有 Excel 导入入口
- 不存在 `TASK_DEFECT_EXCEL_*` callbacks
- 没有 Excel 导入状态机
- 没有模板下载 / 上传预检 / 确认创建流程

## 6. 最小实现

### 6.1 入口页

- 在任务列表底部新增：

```text
📥 Excel批量创建缺陷
```

- 点击后进入独立导入页：
  - `⬇️ 下载模板`
  - `📤 上传 Excel`
  - `⬅️ 返回任务列表`

### 6.2 模板

- 使用 `openpyxl` 按需生成 `.xlsx` 模板
- 表头固定为：
  - `缺陷标题`
  - `复现步骤`
  - `期望结果`
  - `关联任务编码`
  - `优先级`

### 6.3 上传预检

- 仅接受 `.xlsx`
- 预检规则：
  - 标题必填
  - 关联任务编码若填写则必须存在
  - 优先级若填写则必须为 1-5
  - 全空行忽略
- 预检失败时：
  - 输出错误摘要
  - 保持在上传态

### 6.4 确认创建

- 预检通过后进入确认态
- 用户点击 `✅ 确认创建` 后：
  - 逐行调用 `TASK_SERVICE.create_root_task(...)`
  - `task_type='defect'`
  - `description` 使用 `_build_defect_description(...)`
- 创建完成后：
  - 恢复原任务列表
  - 输出导入结果摘要

## 7. Self-Test Gate

最终测试范围：

```bash
python3.11 -m pytest -q tests/test_defect_excel_import.py tests/test_task_list_entry.py tests/test_task_batch_push.py tests/test_defect_report_flow.py
```

### 第一轮

```text
50 passed in 0.24s
```

### 第二轮

```text
50 passed in 0.24s
```

### 依赖校验

```bash
python3.11 -c 'import openpyxl; print(openpyxl.__version__)'
```

结果：

```text
3.1.5
```

### 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

```json
{
  "python_ok": true
}
```

## 8. 用户可见结果

1. 任务列表页新增 `📥 Excel批量创建缺陷`
2. 点击可下载标准 Excel 模板
3. 上传 `.xlsx` 后先做预检，不会直接创建
4. 预检通过后用户确认，再批量创建缺陷
5. 导入完成后恢复任务列表，并给出创建结果摘要

## 9. 风险与回滚

### 风险

- 当前仅支持 `.xlsx`，不支持 `.xls/.csv`
- 当前 v1 不支持 Excel 行级附件

### 回滚点

- `pyproject.toml`
- `tasks/fsm.py`
- `bot.py`
- `tests/test_defect_excel_import.py`
