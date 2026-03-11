# TASK_20260311_002 并行 CLI 本地分支跳过 fetch，远端分支保留 fetch

## 1. 背景

用户确认的新口径：

1. 选择**本地分支**作为基线时，不需要访问远端。
2. 选择**远端分支**作为基线时，仍执行 `git fetch --all --prune`。
3. 行为粒度按**单仓库**判断，不是整次并行创建统一 fetch。

仓库证据：

- `parallel_runtime.py`
  - 锚点：`prepare_parallel_workspace`
  - 旧行为：所有仓库无条件执行 `fetch --all --prune`
- `bot.py`
  - 锚点：`on_parallel_branch_confirm_callback`
  - 当前会把 `selected_ref / selected_remote` 传入 `RepoBranchSelection`

---

## 2. Class Impact Plan

### 2.1 受影响子项目与目录

- 并行运行时：`parallel_runtime.py`
- 测试：`tests/test_parallel_runtime.py`

### 2.2 具体受影响单元

1. `parallel_runtime.py`
   - `prepare_parallel_workspace`

### 2.3 实现文件与测试文件映射

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| 并行副本准备时按分支来源决定是否 fetch | `parallel_runtime.py` | `tests/test_parallel_runtime.py` |

### 2.4 直连依赖测试

- 本次仅纳入 `tests/test_parallel_runtime.py`
- 证据：
  - `tests/test_parallel_runtime.py` 已直接覆盖 `prepare_parallel_workspace`
  - 锚点：
    - `test_prepare_parallel_workspace_rejects_overlapping_relative_paths`
    - `test_prepare_parallel_workspace_copies_full_workdir_and_excludes_generated_dirs`

### 2.5 测试范围升级判断

- 命中升级条件：❌ 否
- 原因：
  - 影响面可可靠收敛到单个 runtime 单元
  - 不涉及构建链、全局配置、跨端契约或数据库 schema

---

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_runtime.py -k 'prepare_parallel_workspace'
```

结果：

- ✅ `2 passed, 8 deselected`

---

## 4. TDD 红灯

先补测试：

1. `test_prepare_parallel_workspace_skips_fetch_for_local_branch_selection`
2. `test_prepare_parallel_workspace_fetches_only_selected_remote_repos`

首次执行：

```bash
python3.11 -m pytest -q tests/test_parallel_runtime.py -k 'skips_fetch_for_local_branch_selection or fetches_only_selected_remote_repos'
```

结果：

- ❌ `2 failed, 10 deselected`

失败点：

1. 本地分支场景下仍然发生了 `fetch --all --prune`
2. 混合本地/远端场景下，根仓库本地分支仍被错误 fetch

满足“先红后绿”。

---

## 5. 最小实现

文件：`parallel_runtime.py`

变更：

1. 在 `prepare_parallel_workspace(...)` 中改为：
   - `selection.selected_remote` 有值（即选中远端分支）时才执行 `fetch --all --prune`
   - `selection.selected_remote` 为空（即本地分支）时直接 `checkout -B`
2. 保留原有失败收口：
   - 远端 fetch 失败仍报 `抓取分支失败`
   - checkout 失败仍报 `切换任务分支失败`

关键锚点：

- `parallel_runtime.py:505-513`

---

## 6. Self-Test Gate

### 6.1 定向绿灯

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_runtime.py -k 'skips_fetch_for_local_branch_selection or fetches_only_selected_remote_repos'
```

结果：

- ✅ `2 passed, 10 deselected`

### 6.2 最终两轮一致性回归

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_runtime.py -k 'prepare_parallel_workspace'
python3.11 -m pytest -q tests/test_parallel_runtime.py -k 'prepare_parallel_workspace'
```

结果：

- ✅ 第一轮：`4 passed, 8 deselected`
- ✅ 第二轮：`4 passed, 8 deselected`

### 6.3 未执行项说明

- 当前仓库未找到可证实的局部 typecheck / coverage 统一命令
- 证据：
  - `AGENTS.md`
  - 锚点：`当前覆盖率工具`
  - 锚点：`typecheck：TODO`

---

## 7. 用户可见结果

1. 若仓库选择的是**本地分支**
   - 并行创建阶段不会再访问远端
   - 直接在并行副本中基于该本地分支创建任务分支
2. 若仓库选择的是**远端分支**
   - 仍会先执行 `git fetch --all --prune`
   - 再基于远端分支创建任务分支
3. 多仓库混合场景下
   - 每个仓库独立判断是否 fetch
   - 不再因为某个本地分支仓库被一并 fetch

---

## 8. 风险与回滚

### 8.1 风险

- 当前“本地/远端”的判断依据是 `selected_remote` 是否有值。
- 该语义与当前 `BranchRef.remote` / `RepoBranchSelection.selected_remote` 传递链一致；
- 若未来引入第三种分支来源类型，需要补充更显式的来源字段。

### 8.2 回滚

- 回滚 `parallel_runtime.py` 中 `prepare_parallel_workspace(...)` 的条件 fetch 逻辑
- 回滚 `tests/test_parallel_runtime.py` 两条新增测试
