# TASK_20260717_004 Codex Run 微信预览端口漂移修复

## 1. 任务目标

修复 fawnStudio 的 Codex App Run 直接调用 `scripts/gen_preview.sh` 时，微信开发者工具服务端口变化导致预览失败的问题。共享脚本必须在获得 CLI 明确证据后自动修正端口映射并最多重试一次，同时保持失败关闭语义。

## 2. 现场证据与根因

- Codex Action：`/Users/david/hypha/fawnStudio/.codex/environments/environment.toml`，锚点：`生成微信开发预览二维码`。
- Action 直接调用：`/Users/david/hypha/tools/vibego/scripts/gen_preview.sh`，不经过 Telegram `bot.py::_execute_command_definition`。
- 旧映射：`~/.config/vibego/config/wx_devtools_ports.json` 中 fawnStudio 路径为 `39198`。
- 本轮微信开发者工具实际监听：`127.0.0.1:11620`。
- CLI 硬失败：`IDE server has started on http://127.0.0.1:11620 and must be restarted on port 39198 first`，退出码 `255`。

根因是运行期 IDE 服务端口已变化，而静态路径映射仍为旧值；Codex Action 只复用了底层脚本，没有复用 Telegram 命令外壳中的端口恢复能力。

## 3. 实现约束

1. 仅接受 CLI 同时给出“当前端口”和“本次请求端口”的不匹配证据。
2. 两个端口都必须在 `1-65535` 范围内，且错误中的请求端口必须等于本次实际传入端口。
3. 每次脚本执行最多自动重试一次；无法解析或第二次失败时返回真实失败。
4. 更新当前小程序路径映射；有明确项目名时更新对应项目键，无项目名时只允许更新旧端口唯一对应的项目键。
5. 配置写入使用同目录临时文件原子替换，保留无关项目与路径。
6. 配置写入失败只告警，本次仍可使用 CLI 已确认的当前端口重试。
7. 脚本输出 `VIBEGO_WX_PORT_RETRY_USED=1`，Telegram 外壳识别后不得再次自动重试。
8. 不主动启动、关闭或重启微信开发者工具。

## 4. 变更点

- `scripts/gen_preview.sh`
  - 新增端口不匹配解析、映射更新和单次 CLI 恢复执行器。
  - 默认二维码预览与手机自动预览复用恢复执行器。
- `bot.py`
  - 新增共享脚本重试标记识别，阻止重复自动重试。
- `tests/test_wx_preview_port_flow.py`
  - 覆盖成功恢复、最多一次、未知错误不重试、配置写入失败仍重试。
- `tests/test_command_execution_flow.py`
  - 覆盖 Telegram 外壳识别脚本已重试标记。

## 5. TDD 记录

- 基线：`BOT_TOKEN=TEST_TOKEN python3.11 -m pytest -q tests/test_wx_preview_port_flow.py tests/test_command_execution_flow.py tests/test_wx_preview_detection.py`，`74 passed`。
- RED：新增测试首次执行为 `4 failed, 1 passed`；失败点分别为脚本未重试、未更新映射、未输出重试标记以及 bot 未阻止重复重试。
- GREEN：新增测试 `5 passed`；相关测试集合扩展后为 `79 passed`。

## 6. 最终验证

- `git diff --check`：通过。
- `bash -n scripts/gen_preview.sh scripts/gen_upload.sh`：通过。
- `python3.11 -m py_compile bot.py`：通过。
- 相关测试连续两轮：实现后首轮每轮 `79 passed`；补齐非法端口及请求端口不一致用例后，最终每轮 `81 passed`。
- `python3.11 -m vibego_cli doctor`：Python、依赖、配置根、项目配置与数据库检查通过。
- `bash scripts/test_deps_check.sh`：runtime venv 与关键依赖检查通过。
- 全量测试第一次未注入 `MODEL_WORKDIR`：`1078 passed, 4 failed`，4 项均被既有 worker 环境检查提前拦截。
- 按 worker 最小测试环境补入 `MODEL_WORKDIR=/Users/david/hypha/tools/vibego` 后重跑：`1082 passed, 6 warnings`；警告均来自既有 `test_unescape_markdown.py` 测试返回值。

### 6.1 真实 Codex Action 等价验收

验收期间未启动或重启微信开发者工具；复用已监听 `127.0.0.1:11620` 的现有进程。

1. 第一次按 Codex Action 等价命令执行：
   - 脚本先读取旧映射 `39198`；
   - 输出 `VIBEGO_WX_PORT_RETRY_USED=1`；
   - 明确记录 `39198 -> 11620`；
   - 更新项目与路径映射；
   - 退出码 `0`，生成有效的 `470x470` JPEG 二维码。
2. 第二次执行：
   - 启动即使用 `11620`；
   - 未输出自动重试标记；
   - 退出码 `0`，再次生成有效二维码。
3. `wx_devtools_ports.json` 最终状态：`projects.fawnstudio` 与 fawnStudio `frontend-mini` 路径均为 `11620`，其他映射保持不变。
4. 两个验收二维码已移动到系统废纸篓，可恢复；未遗留 `/tmp/wx-preview-codex-port-drift-*.jpg`。

## 7. 完成状态

`READINESS: COMPLETE`。原始 Codex App Run 命令无需修改，端口漂移已由共享脚本恢复并通过真实预览验证。
