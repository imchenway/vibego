# TASK_20260716_001 Telegram 与终端微信预览执行差异

## 1. 现象与影响

在 `/Users/david/hypha/fawnStudio` 直接执行 `scripts/gen_preview.sh` 时，脚本报错：

```text
declare: -A: invalid option
syntax error: operand expected
[错误] 未找到小程序项目目录
```

同一项目从 Telegram 执行 `wx-dev-preview` 可以继续到微信 CLI。影响范围是未显式传入 `PROJECT_PATH`、需要脚本根据 `PROJECT_BASE` 自动探测小程序目录的预览和上传调用。

## 2. 已确认事实与根因

- 当前 macOS `/bin/bash` 为 `3.2.57`，不支持关联数组。
- fawnStudio 实际存在 `frontend-mini/project.config.json` 和 `frontend-mini/miniprogram/app.json`。
- Telegram 在 `bot.py::_maybe_handle_wx_preview` 中先扫描候选目录，再由 `_wrap_wx_preview_command` 注入 `PROJECT_PATH` 与 `PROJECT_BASE`；`scripts/gen_preview.sh::_resolve_project_path` 因而在显式路径分支提前返回。
- 终端命令只传 `PROJECT_BASE`，进入自动探测分支；修复前 `scripts/gen_preview.sh` 与 `scripts/gen_upload.sh` 均使用 `declare -A seen=()`，在 Bash 3.2 下失败。
- `scripts/gen_upload.sh::_run_upload_cli_once` 已有普通索引数组 + 精确字符串遍历去重的 Bash 3.2 兼容实现，可作为同仓工作样例。

根因：两个脚本的项目自动探测分支使用了 Bash 4+ 关联数组，而项目实际通过 macOS 默认 Bash 3.2 执行。

## 3. 方案比较与决策

### 方案 A：普通索引数组精确去重（采用）

复用 `scripts/gen_upload.sh::_run_upload_cli_once` 的既有模式：遍历已收集路径，通过 `[[ "$existing" == "$p" ]]` 做精确比较，再决定是否追加。

- 优点：兼容 Bash 3.2；不新增依赖；保持候选顺序、`PROJECT_HINT` 和最短路径规则。
- 成本：候选去重由哈希查找变为线性遍历；自动探测候选数量很小，影响可忽略。

### 方案 B：要求 Homebrew Bash 4+

不采用。会新增本机依赖并要求所有入口显式调用新版 Bash，与当前默认命令的 `bash` 调用方式冲突。

### 方案 C：仅让 Telegram 始终注入 `PROJECT_PATH`

不采用。Telegram 已经如此处理；该方案不能修复终端直调和上传脚本的自动探测兜底。

## 4. 选定设计

在两个 `_resolve_project_path` 中删除 `declare -A seen=()`，直接复用 `listed` 索引数组完成精确去重：

```bash
local existing=""
local duplicated=0

for p in "${candidates[@]}"; do
  [[ -z "$p" || ! -d "$p" ]] && continue
  duplicated=0
  for existing in "${listed[@]}"; do
    if [[ "$existing" == "$p" ]]; then
      duplicated=1
      break
    fi
  done
  if [[ $duplicated -ne 0 ]]; then
    continue
  fi
  listed+=( "$p" )
  # 后续 PROJECT_HINT、路径长度比较保持不变
done
```

不抽取公共脚本、不修改 Telegram 命令包装、不改变端口解析、CLI 参数、成功判定或二维码行为。

## 5. 可测试验收标准

1. `scripts/gen_preview.sh` 和 `scripts/gen_upload.sh` 不再包含 Bash 4+ 关联数组声明。
2. 在 fawnStudio 同构目录下，仅传 `PROJECT_BASE`、不传 `PROJECT_PATH` 时，两个脚本均能选中 `frontend-mini` 并到达假微信 CLI。
3. 自动探测仍优先显式 `PROJECT_PATH`，并保持候选精确去重、`PROJECT_HINT` 与最短路径规则。
4. Telegram 单候选自动注入 `PROJECT_PATH` 的既有测试不退化。
5. 微信 preview、auto-preview、remote-debug、upload 的既有聚焦测试通过。
6. `bash -n scripts/gen_preview.sh scripts/gen_upload.sh`、`git diff --check`、`python3.11 -m vibego_cli doctor` 与依赖自检通过。

## 6. TDD 实施计划

### Task 1：锁定 Bash 3.2 兼容契约

**文件：**

- 修改：`tests/test_wx_preview_port_flow.py`

- [x] 新增参数化测试，扫描 `gen_preview.sh`、`gen_upload.sh`，断言不存在 `declare -A`、`local -A` 或 `typeset -A`。
- [x] 新增 fawnStudio 同构目录：`workspace/frontend-mini/project.config.json` 指向 `miniprogram/`，其下存在 `app.json`。
- [x] 用假 CLI 记录参数并返回非零；两个脚本只传 `PROJECT_BASE`，断言 CLI 调用中出现 `--project <workspace/frontend-mini>`。
- [x] 补齐显式 `PROJECT_PATH`、`PROJECT_HINT`、最短路径、重复候选与同长度首次候选的行为矩阵。
- [x] 运行新增测试，确认失败原因分别是关联数组仍存在、Bash 3.2 自动探测在到达假 CLI 前失败。

红灯命令：

```bash
BOT_TOKEN=TEST_TOKEN MODEL_WORKDIR=/Users/david/hypha/tools/vibego \
python3.11 -m pytest -q \
  tests/test_wx_preview_port_flow.py::test_wx_scripts_do_not_require_bash4_associative_arrays \
  tests/test_wx_preview_port_flow.py::test_wx_scripts_preserve_project_selection_rules
```

### Task 2：最小兼容实现

**文件：**

- 修改：`scripts/gen_preview.sh`
- 修改：`scripts/gen_upload.sh`

- [x] 在两个 `_resolve_project_path` 中用 `listed` 普通数组精确遍历去重。
- [x] 不修改 `PROJECT_HINT`、最短路径、显式 `PROJECT_PATH`、CLI 与端口逻辑。
- [x] 重跑新增测试并确认绿灯。

### Task 3：回归、审查与交付证据

**文件：**

- 更新：`docs/TASK_20260716_001_Telegram与终端微信预览执行差异.md`
- 更新：`docs/TASK_20260716_001_Telegram与终端微信预览执行差异.html`

- [x] 跑微信脚本/目录检测/命令执行聚焦测试。
- [x] 跑脚本语法、doctor、依赖自检与 diff 检查。
- [x] 独立 reviewer 按当前 `AGENTS.md`、本任务目标、变更范围与验证证据审查。
- [x] 反向搜索 `declare -A`，确认受影响脚本中的旧模式已消失。
- [x] 将实际命令、结果、未覆盖点和回滚说明写回本文档及故障图。

## 7. 风险与回滚

- 风险：普通数组去重为 O(n²)，但候选目录数量受搜索深度和项目结构约束，实际规模很小。
- 兼容风险：若精确比较或追加顺序写错，可能改变 `PROJECT_HINT`/最短路径选择；以行为测试与现有测试共同防护。
- 回滚：仅撤销两个脚本的去重实现及本任务新增测试/文档；不涉及数据库、依赖、配置或远端操作。
- Git：按仓库规约，本任务不执行 commit、push、merge 或其他历史/远端操作，除非用户另行明确要求。

## 8. 执行记录

- 工作区：`/Users/david/hypha/tools/vibego`，分支 `master`；未创建/切换 worktree。
- 基线：修复前 `tests/test_wx_preview_port_flow.py` 为 `35 passed`。
- RED：新增兼容约束与自动探测用例后执行聚焦命令，结果 `4 failed`；两个静态约束均命中 `declare -A`，两个行为用例均在调用假 CLI 前复现 `invalid option` / `operand expected`。
- GREEN：最小替换两个脚本去重实现后，同一命令结果 `4 passed in 0.27s`。
- 审查加固：选择规则矩阵扩展为预览/上传 × 四类规则，新增用例命令结果 `10 passed`。
- 文件级回归：`tests/test_wx_preview_port_flow.py` -> `45 passed`。
- 微信相关聚焦回归：`tests/test_wx_preview_port_flow.py tests/test_wx_preview_detection.py tests/test_command_execution_flow.py tests/test_wx_upload_args.py tests/test_wx_remote_debug_flow.py` -> `93 passed`。
- 全量回归：`BOT_TOKEN=TEST_TOKEN MODEL_WORKDIR=/Users/david/hypha/tools/vibego python3.11 -m pytest -q` -> `1179 passed, 6 warnings`；6 个 warning 均来自既有 `tests/test_unescape_markdown.py` 的 `PytestReturnNotNoneWarning`。
- Bash 语法：`/bin/bash -n scripts/gen_preview.sh scripts/gen_upload.sh` -> 通过。
- 旧模式反向搜索：两个受影响脚本中未发现 `declare -A`、`local -A` 或 `typeset -A`。
- `git diff --check` -> 通过。
- `python3.11 -m vibego_cli doctor` -> `python_ok=true`，缺失依赖为空。
- `bash scripts/test_deps_check.sh` -> 通过。
- 真实目录边界验证：在 `/Users/david/hypha/fawnStudio` 下以 `/usr/bin/false` 替代微信 CLI，脚本正确选中 `/Users/david/hypha/fawnStudio/frontend-mini` 并到达 CLI；输出中不存在 `declare: -A` 或 `operand expected`。
- 未执行真实微信开发者工具 CLI/手机预览，避免验证过程触发外部 GUI 或手机动作；目录解析、参数交付与两脚本调用边界由假 CLI 自动化测试覆盖。
- 独立 review：代码与测试无 Critical/Important 问题；reviewer 提出的选择规则覆盖、macOS `/bin/bash` 锁定和关联数组声明反向约束已补齐。
