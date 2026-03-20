# TASK_20260320_001 wx-dev-preview /tmp 输出路径修复

## 1. 背景

- 用户提供 `wx-dev-preview` 执行详情附件，执行失败。
- 失败日志显示：命令已正常连接 IDE 服务端口 `45459`，但在预览阶段报错“二维码输出路径无效或不存在”。

## 2. 证据

### 2.1 用户附件

- 附件：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-03-20/20260320_051907615-9e2fb749e218.txt`
- 关键片段：
  - `状态：failed (exit=3)`
  - `输出：/tmp/wx-preview-1773983781.jpg`
  - `CLI 未生成二维码文件`
  - `二维码输出路径无效或不存在 %s (code 17)`

### 2.2 仓库默认命令

- `command_center/defaults.py`
  - `wx-dev-preview` 默认命令：
  - `OUTPUT_QR="${OUTPUT_QR:-/tmp/wx-preview-$(date +%s).jpg}"`

### 2.3 脚本行为

- `scripts/gen_preview.sh`
  - 原逻辑直接把 `OUTPUT_QR` 传给微信 CLI 的 `--qr-output`
  - 未对符号链接目录做物理路径规范化

### 2.4 运行环境证据

- 命令：`ls -ld /tmp`
- 结果：`/tmp -> private/tmp`
- 说明：当前 macOS 环境中 `/tmp` 是指向 `/private/tmp` 的符号链接。

### 2.5 实机复现

1. 失败复现：

```bash
PORT=45459 PROJECT_PATH="/Users/david/hypha/mall/frontend-mini" PROJECT_BASE="/Users/david/hypha/mall/frontend-mini" OUTPUT_QR="/tmp/wx-preview-codex-test-$(date +%s).jpg" bash scripts/gen_preview.sh
```

结果：失败，报错与附件一致。

2. 对照验证：

```bash
PORT=45459 PROJECT_PATH="/Users/david/hypha/mall/frontend-mini" PROJECT_BASE="/Users/david/hypha/mall/frontend-mini" OUTPUT_QR="/private/tmp/wx-preview-codex-test-$(date +%s).jpg" bash scripts/gen_preview.sh
```

结果：成功生成二维码。

## 3. 结论

高置信度结论：

- 当前失败不是端口问题，也不是小程序目录问题。
- 根因是 `wx-dev-preview` 默认输出路径使用 `/tmp/...`，而微信开发者工具 CLI 在当前 macOS 环境下会把该符号链接目录判定为无效路径。
- 当改为真实物理目录 `/private/tmp/...` 时，命令可以成功生成二维码。

## 4. 方案对比

### 方案 A：仅修改默认命令，把 `/tmp` 改成 `/private/tmp`

优点：改动最小。

缺点：
- 只能修复默认命令。
- 用户若手动传入 `/tmp/...` 仍会失败。

### 方案 B：在 `scripts/gen_preview.sh` 中统一把输出目录规范化为物理路径（推荐）

优点：
- 同时覆盖默认命令与手动传参。
- 行为更稳健，对任意符号链接输出目录都有效。

缺点：
- 日志中的最终输出路径会从 `/tmp/...` 变成 `/private/tmp/...`。

## 5. 开发设计

### 5.1 受影响单元

1. `scripts/gen_preview.sh`
   - 在调用 CLI 前，将 `OUTPUT_QR` 的父目录转换为 `pwd -P` 解析后的物理路径。
2. `tests/test_wx_preview_port_flow.py`
   - 新增回归测试，验证脚本会把符号链接输出目录转换为真实路径。

### 5.2 Class Impact Plan

- 受影响子项目与目录：
  - 仓库根脚本层：`scripts/`
  - 测试目录：`tests/`
- 计划修改单元：
  - `scripts/gen_preview.sh`
  - `tests/test_wx_preview_port_flow.py`
- 对应测试文件：
  - `tests/test_wx_preview_port_flow.py`
  - 直连契约回归：`tests/test_wx_preview_detection.py`（默认命令/预览相关契约）
  - 直连流程回归：`tests/test_command_execution_flow.py`（预览命令执行结果链路）
- 测试范围升级判断：
  - 命中“脚本修改”升级条件，但仍可收敛到 wx preview 相关模块测试，无需扩大到全项目。

## 6. TDD 记录

### 6.1 Baseline Gate

```bash
python3.11 -m pytest -q tests/test_wx_preview_detection.py tests/test_wx_preview_port_flow.py tests/test_command_execution_flow.py
```

结果：`42 passed`

### 6.2 测试先行（首次失败）

新增用例：`test_gen_preview_normalizes_symlink_output_dir`

```bash
python3.11 -m pytest -q tests/test_wx_preview_port_flow.py -k symlink_output_dir
```

结果：`1 failed`

失败原因：脚本把符号链接目录 `link-output/qr.jpg` 原样传给 CLI，而不是传真实目录 `real-output/qr.jpg`。

### 6.3 最小实现

- 文件：`scripts/gen_preview.sh`
- 改动：
  - 新增输出目录规范化逻辑：
    - `OUTPUT_DIR="$(dirname "$OUTPUT_QR")"`
    - `mkdir -p "$OUTPUT_DIR"`
    - `PHYSICAL_OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"`
    - `OUTPUT_QR="${PHYSICAL_OUTPUT_DIR}/$(basename "$OUTPUT_QR")"`

### 6.4 Self-Test Gate

局部验证：

```bash
python3.11 -m pytest -q tests/test_wx_preview_port_flow.py -k 'symlink_output_dir or prefers_python3_over_python'
```

结果：`3 passed`

模块级验证（第 1 轮）：

```bash
python3.11 -m pytest -q tests/test_wx_preview_detection.py tests/test_wx_preview_port_flow.py tests/test_command_execution_flow.py
```

结果：`43 passed`

模块级验证（第 2 轮）：

```bash
python3.11 -m pytest -q tests/test_wx_preview_detection.py tests/test_wx_preview_port_flow.py tests/test_command_execution_flow.py
```

结果：`43 passed`

### 6.5 实机回归

```bash
PORT=45459 PROJECT_PATH="/Users/david/hypha/mall/frontend-mini" PROJECT_BASE="/Users/david/hypha/mall/frontend-mini" OUTPUT_QR="/tmp/wx-preview-codex-test-fixed-$(date +%s).jpg" bash scripts/gen_preview.sh
```

结果：成功，日志输出已自动转为 `/private/tmp/...`。

## 7. 风险与边界

- 本次未改动数据库、Telegram 交互、端口配置协议。
- 本次仅修复 `wx-dev-preview` 的二维码输出路径问题；未同步改动未使用的 `scripts/wx_preview.sh`。
- 若后续还有其它 CLI 命令使用符号链接目录作为输出路径，建议复用同类规范化逻辑。
