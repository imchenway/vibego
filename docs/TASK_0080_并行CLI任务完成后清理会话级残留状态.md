# /TASK_0080 并行 CLI 任务完成后清理会话级残留状态

## 1. 背景

- 用户现象：并行 CLI 链路创建的会话，在代码提交后仍会残留到 `vibego` 重启前。
- 本次确认的目标行为：
  - ✅ 自动结束时机：**任务完成时**
  - ✅ 关闭语义：**关闭并删除目录**
- 仓库现状证据：
  - `bot.py:16554-16555`：任务状态切到 `done` 时会调度 `_schedule_parallel_cleanup_for_done(...)`
  - `bot.py:9006-9038`：`_delete_parallel_session_workspace(...)` 会删除 tmux / runtime 目录并写回 `deleted`
  - `bot.py:13079-13083`、`13131-13134`：提交/自动合并成功后状态分别为 `committed` / `merged`，不会自动关闭

## 2. Class Impact Plan

### 2.1 受影响子项目与目录

- worker 并行会话清理链路：`bot.py`
- 相关测试：
  - `tests/test_parallel_session_routing.py`
  - `tests/test_task_description.py`

### 2.2 受影响单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `_drop_parallel_session_bindings` | `bot.py` | `tests/test_parallel_session_routing.py` |
| `_delete_parallel_session_workspace` | `bot.py` | `tests/test_parallel_session_routing.py` |
| `on_status_callback` | `bot.py` | `tests/test_task_description.py` |
| `_clear_parallel_session_scoped_runtime_state`（新增） | `bot.py` | `tests/test_parallel_session_routing.py` |
| `_clear_parallel_reply_targets_for_task`（新增） | `bot.py` | `tests/test_parallel_session_routing.py` |

### 2.3 直连依赖测试纳入依据

- `tests/test_task_description.py::test_on_status_callback_done_schedules_parallel_cleanup`
  - 直接覆盖 `task:status:*` -> `done` -> 后台清理调度
- `tests/test_parallel_session_routing.py::*delete_parallel_session_workspace*`
  - 直接覆盖并行目录删除、状态落库、trusted 清理与内存态回收

### 2.4 测试范围升级判断

- 结论：❌ 未升级
- 原因：
  - 本次仅补强并行会话删除时的内存态清理
  - 未修改跨端契约、数据库 schema、构建链或公共外部接口

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_on_status_callback_done_schedules_parallel_cleanup \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_allows_cleanup_for_closed_session \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_cleans_parallel_workspace_trust
```

结果：

- ✅ `3 passed`

## 4. TDD 红灯

新增测试：

- `tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_cleans_session_scoped_runtime_state`

首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_cleans_session_scoped_runtime_state
```

结果：

- ❌ 失败：`SESSION_OFFSETS` / `PENDING_SUMMARIES` / `CHAT_LAST_MESSAGE` / `CHAT_DELIVERED_*` / `CHAT_PARALLEL_REPLY_TARGETS`
  在删除并行目录后仍残留

## 5. 最小实现

### 5.1 新增会话级缓存清理

- `bot.py::_clear_parallel_session_scoped_runtime_state(session_key)`
  - 清理：
    - `SESSION_OFFSETS`
    - `PENDING_SUMMARIES`
    - `SESSION_TASK_BINDINGS`
    - `SESSION_COMMIT_CALLBACK_BINDINGS`
    - 所有 chat 下该 `session_key` 的：
      - `CHAT_LAST_MESSAGE`
      - `CHAT_DELIVERED_HASHES`
      - `CHAT_DELIVERED_OFFSETS`

### 5.2 新增任务级回复态清理

- `bot.py::_clear_parallel_reply_targets_for_task(task_id)`
  - 清理 `CHAT_PARALLEL_REPLY_TARGETS`
  - 防止并行目录已删后，用户仍误入旧的“回复 /TASK_xxx”态

### 5.3 删除链路补强

- `bot.py::_drop_parallel_session_bindings(...)`
  - 统一回收：
    - `PARALLEL_TASK_SESSION_MAP`
    - `PARALLEL_SESSION_CONTEXTS`
    - `PARALLEL_SESSION_TASK_BINDINGS`
    - `PARALLEL_CALLBACK_BINDINGS`
    - 上述新增的 session 级缓存 / reply target

## 6. Self-Test Gate

执行两轮一致性回归：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_on_status_callback_done_schedules_parallel_cleanup \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_allows_cleanup_for_closed_session \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_cleans_session_scoped_runtime_state \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_cleans_parallel_workspace_trust
```

结果：

- ✅ 第一轮：`4 passed`
- ✅ 第二轮：`4 passed`

## 7. 用户可见结果

1. 任务点成 `done` 后，仍保持“关闭并删除目录”的既有行为。
2. 本次补强后，删除并行目录不只会删 tmux / workspace / runtime：
   - 还会把会话级缓存与回复态一并清掉。
3. 即便不重启 `vibego`，旧并行会话对应的残留状态也不会继续留在内存里。
