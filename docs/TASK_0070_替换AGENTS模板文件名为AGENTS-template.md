# TASK_0070 替换 AGENTS 模板文件名为 AGENTS-template.md

## 1. 任务信息

- 任务编码：`/TASK_0070`
- 任务名称：替换 `AGENTS.md` 的名称为 `AGENTS-template.md`
- 执行模式：PLAN -> DEVELOP
- 决策输入（来自 request_user_input）：
    - 兼容策略：**完全替换**（不保留根目录 `AGENTS.md` 兼容入口）
    - 文档策略：**新建唯一文档**（不覆盖历史 `docs/TASK_0070_request_user_input防漏选与交互重构.md`）

## 2. 影响面识别（证据）

- 模板默认路径：
    - `scripts/run_bot.sh:151-159`
    - `scripts/start_tmux_codex.sh:91`
    - `scripts/models/common.sh:195`
- 打包分发：
    - `pyproject.toml:48`
    - `MANIFEST.in:2`
- 文档引用：
    - `README.md:45,143`
    - `README-en.md:44,129`
- 强制提示文案：
    - `bot.py:350-352`
- 子项目 AGENTS 证据：
    - `find . -name AGENTS.md -o -name AGENTS.evidence.json` 仅返回 `./AGENTS.md`（迁移后为 `./AGENTS-template.md`）

## 3. TDD 门禁执行记录

### 3.1 Baseline Gate（改动前）

1) 读取强制规约文件：

- `$HOME/.config/vibego/AGENTS.md` ✅
- `./AGENTS.md` ✅（迁移前）
- 子项目 `AGENTS.md / AGENTS.evidence.json`：未发现额外子项目文件

2) 基线测试（改动前）：

```bash
PYTHONPATH=. pytest -q
# 626 passed, 8 warnings in 11.00s
```

3) coverage 基线：

```bash
python3 -m coverage --version
# coverage: NOT_INSTALLED
```

### 3.2 TDD Gate（先写测试并先失败）

1) 新增测试：`tests/test_agents_template_migration.py`
2) 先失败验证：

```bash
PYTHONPATH=. pytest -q tests/test_agents_template_migration.py
# 4 failed
```

失败点覆盖：

- `ENFORCED_AGENTS_NOTICE` 未切换
- 启动脚本默认模板路径未切换
- 打包清单未切换
- README 引用未切换

### 3.3 Implementation Gate（最小实现）

- 仅执行“模板文件重命名 + 引用切换”的最小变更；未引入新依赖、未调整 CI/构建策略。

### 3.4 Self-Test Gate（改动后）

1) 受影响测试：

```bash
PYTHONPATH=. pytest -q tests/test_agents_template_migration.py
# 4 passed

PYTHONPATH=. pytest -q tests/test_agents_template_migration.py tests/test_task_description.py
# 146 passed
```

2) 全量测试（连续两轮）：

```bash
PYTHONPATH=. pytest -q
# 630 passed, 6 warnings in 9.79s

PYTHONPATH=. pytest -q
# 630 passed, 6 warnings in 9.44s
```

3) coverage：

```bash
python3 -m coverage run -m pytest -q
# /opt/homebrew/opt/python@3.14/bin/python3.14: No module named coverage
```

> 结论：当前仓库缺少 coverage 工具链（TODO）。

## 4. 实际改动清单

- 文件重命名：
    - `AGENTS.md` -> `AGENTS-template.md`
- 代码与脚本：
    - `bot.py`
    - `scripts/run_bot.sh`
    - `scripts/start_tmux_codex.sh`
    - `scripts/models/common.sh`
- 打包文件：
    - `pyproject.toml`
    - `MANIFEST.in`
- 文档：
    - `README.md`
    - `README-en.md`
    - `specs/001-speckit-feasibility/quickstart.md`
- 测试：
    - `tests/test_agents_template_migration.py`（新增）

## 5. 风险与回滚

### 风险

- 外部若硬编码依赖仓库根 `AGENTS.md` 会断链（本任务明确选择“完全替换”）。

### 回滚点

1) 恢复文件名：`AGENTS-template.md` -> `AGENTS.md`
2) 回退路径引用：
    - `scripts/run_bot.sh`
    - `scripts/start_tmux_codex.sh`
    - `scripts/models/common.sh`
3) 回退打包与文档引用：
    - `pyproject.toml`
    - `MANIFEST.in`
    - `README.md` / `README-en.md`
4) 回退提示文案：`bot.py` 中 `ENFORCED_AGENTS_NOTICE`

## 6. 结果小结

- 在“完全替换”决策下，已完成模板名称迁移与全链路引用更新。
- 关键回归通过（受影响测试 + 全量双轮）。
- coverage 工具链缺失，已记录为 TODO，未擅自引入新依赖。
