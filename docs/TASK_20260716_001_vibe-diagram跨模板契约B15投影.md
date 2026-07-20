# TASK_20260716_001：vibe-diagram 跨模板契约 B15 投影

## 目标

将独立 `vibe-diagram` 仓库已通过静态门禁的 `0.1.3` 投影到 Vibego 发行源，并使 canonical 版本、tree digest、内置 skill、Codex repo plugin 和 marketplace catalog 的差异可计算、可审计。

## 投影边界

- canonical 来源版本：`0.1.3`。
- canonical tree digest：`8488c39673b7205483056ef48ed5223128d166387b8fa2ee353a50dd4ec49ca6`。
- 内置目标：`vibego_cli/data/skills/vibe-diagram`。
- plugin 目标：`plugins/vibe-diagram`。
- catalog 目标：`.agents/plugins/marketplace.json`。
- host 文案覆盖：0；内置 skill 与 plugin skill 子树 82 个文件逐字节一致。
- adapter 额外文件：仅 `agents/openai.yaml`，由 `vibe_diagram_projection.json` 显式登记。
- 本批未执行 `agents-sync`，未修改 `~/.codex/skills`、`~/.agents/skills` 或活跃 override，未启动任何客户端。

## TDD 与验证

1. RED：新增投影与打包契约测试，实现前 2 项失败，分别证明投影清单/目标不存在且 package-data 未声明。
2. GREEN：新增两份投影、catalog、审计清单及打包声明；投影测试 2 项通过。
3. 受影响回归：`tests/test_agents_sync.py` 与投影测试合计 12 项通过。
4. wheel 构建：`python3.11 -m build --wheel`通过，wheel 内包含 82 个 skill 文件、58 个 HTML 模板、family policy 和自适应 runtime asset。
   - 最终复验先尝试 `--no-isolation`，因本机 `packaging` 版本不满足 setuptools 许可证解析而失败；随后按仓库正式隔离构建命令重跑成功。失败变体不计入通过证据。
5. 最终第一轮全量：1114 项通过，6 条既有 `PytestReturnNotNoneWarning`，耗时 59.65 秒。
6. 最终第二轮全量：1114 项通过，6 条同类既有 warning，耗时 56.18 秒。

## 运行时结论

本批只建立仓库投影与打包证据，不建立 native skill 安装、新会话发现、调用、交付、升级或卸载证据。运行时仍为 `unverified`。
