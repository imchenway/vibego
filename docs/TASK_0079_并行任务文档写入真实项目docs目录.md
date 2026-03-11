# TASK_0079 并行任务文档写入真实项目 docs 目录

## 1. 背景

- 用户反馈：
  - 并发执行时，模型会把任务文档写到并行临时目录的 `/docs`
  - 这样真实项目目录下的 `/docs` 无法沉淀这些任务设计，后续模型回溯困难
- 已确认策略：
  - 采用 **共享真实 docs**
  - 并行工作区根级 `docs/` 不保留副本，直接指向真实项目根目录 `docs/`

## 2. 关键证据

- 当前并行副本是整目录复制：
  - `parallel_runtime.py`（锚点：`_copy_parallel_workspace_tree`, `prepare_parallel_workspace`）
- 当前没有对根级 `docs/` 做特殊处理：
  - `parallel_runtime.py`（锚点：`shutil.copytree(source_root, workspace_root, ...)`）
- 并行工作区删除入口：
  - `parallel_runtime.py`（锚点：`delete_parallel_workspace`）

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- 并行运行时复制/清理链路：`parallel_runtime.py`
- 测试资产：`tests/test_parallel_runtime.py`

### 3.2 计划修改单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `_link_shared_docs_dir`（新增） | `parallel_runtime.py` | `tests/test_parallel_runtime.py` |
| `prepare_parallel_workspace` | `parallel_runtime.py` | `tests/test_parallel_runtime.py` |
| `delete_parallel_workspace` | `parallel_runtime.py` | `tests/test_parallel_runtime.py` |

### 3.3 直连依赖测试

- `tests/test_parallel_runtime.py`
  - 证据：当前已覆盖整目录复制、分支检出、清理相关行为

### 3.4 测试范围升级判断

- 结论：⚠️ 不升级
- 原因：
  - 变更可可靠收敛到 `parallel_runtime.py`
  - 已有对应类级测试文件，可通过局部补测证明安全

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q \
  tests/test_parallel_runtime.py::test_prepare_parallel_workspace_copies_full_workdir_and_excludes_generated_dirs \
  tests/test_parallel_runtime.py::test_prepare_parallel_workspace_skips_fetch_for_local_branch_selection
```

结果：

- ✅ `2 passed`

## 5. TDD 红灯

新增测试：

- `test_prepare_parallel_workspace_links_root_docs_to_source_docs`
- `test_prepare_parallel_workspace_creates_missing_source_docs_before_linking`
- `test_delete_parallel_workspace_keeps_shared_source_docs`

首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_parallel_runtime.py::test_prepare_parallel_workspace_links_root_docs_to_source_docs \
  tests/test_parallel_runtime.py::test_prepare_parallel_workspace_creates_missing_source_docs_before_linking \
  tests/test_parallel_runtime.py::test_delete_parallel_workspace_keeps_shared_source_docs
```

结果：

- ❌ `2 failed, 1 passed`
- 首次失败点：
  - 工作区根级 `docs/` 仍是复制目录，不是链接
  - 真实项目缺少 `docs/` 时不会自动创建

## 6. 最小实现

- `parallel_runtime.py`
  - 新增 `_link_shared_docs_dir(source_root, workspace_root)`
    - 自动创建真实项目根级 `docs/`
    - 删除工作区内已复制的 `docs/` 目录/文件/旧链接
    - 将工作区根级 `docs/` 替换为指向真实项目 `docs/` 的符号链接
  - `prepare_parallel_workspace(...)`
    - 在整目录复制后调用 `_link_shared_docs_dir(...)`
- 保持：
  - 只处理**工作区根级** `docs/`
  - 不影响嵌套仓库或其他子目录里的 `docs/`

## 7. Self-Test Gate

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_runtime.py
python3.11 -m py_compile parallel_runtime.py
```

结果：

- ✅ 第一轮：`15 passed`
- ✅ 第二轮：`15 passed`
- ✅ `py_compile` 通过

## 8. 用户可见结果

1. 并行任务中模型继续按当前目录 `docs/` 写文档
2. 但由于工作区根级 `docs/` 已共享真实项目 `docs/`
   - 文档会直接沉淀到真实项目目录
3. 清理并行工作区时
   - 只删除链接
   - 不会删除真实项目中的文档
