# Quickstart: 复现“vibego × speckit 互补可行性探索”产物

**Date**: 2025-12-22  
**Feature Dir**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/`

## 你将得到什么

- 一份可决策的评估结论（能力映射 + 互补方案对比 + 推荐方案 + 风险/路线图）
- 一份概念性合同（OpenAPI），用于统一输入/输出语义并降低歧义
- 一份数据模型，便于后续实现阶段落地与校验
- 一份可重复执行的最小演示流程（见 demo-flow.md）

## 前置条件

- 已具备 `git`、`bash`、`python3`（vibego 本身要求 Python >=3.9）
- 可选：如需体验上游 Spec Kit 的初始化能力，可安装 `uv` + `specify` CLI（上游要求 Python 3.11+）

参考（官方）：
- Spec Kit：https://github.com/github/spec-kit
- uv：https://docs.astral.sh/uv/

## 步骤 1：检查分支与产物目录

```bash
git status
git branch --show-current
ls -la specs/001-speckit-feasibility/
```

预期：
- 当前分支为 `001-speckit-feasibility`
- 目录下至少包含：`spec.md`、`plan.md`、`research.md`、`data-model.md`、`quickstart.md`、`contracts/`

## 步骤 2：阅读可决策结论

按顺序阅读以下文件（都在仓库内，可直接 diff 与评审）：

- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/plan.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/research.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/data-model.md`
- `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/contracts/openapi.yaml`

## 步骤 3：生成一个新的 speckit 特性（示例）

> 推荐按 `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md` 在隔离工作区执行，避免污染主工作区。

两条可选演示路径：
- 路径 A：不使用上游 Specify CLI（仅用仓库内 `.specify` 脚本）
- 路径 B：使用上游 Specify CLI（用于对比上游模板与工作流）

### 路径 A：不使用上游 Specify CLI

```bash
REPO_ROOT="/Users/david/hypha/tools/vibego"
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
DEMO_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/vibego/speckit-demos/$RUN_ID"

mkdir -p "$DEMO_ROOT"
git -C "$REPO_ROOT" worktree add "$DEMO_ROOT/worktree" HEAD
cd "$DEMO_ROOT/worktree"

bash .specify/scripts/bash/create-new-feature.sh --json --short-name "demo-speckit-flow" "Demo: speckit workflow in vibego"
bash .specify/scripts/bash/setup-plan.sh --json
bash .specify/scripts/bash/check-prerequisites.sh --json
```

预期：
- `create-new-feature.sh` 输出 JSON（含 `BRANCH_NAME`、`SPEC_FILE`）
- `setup-plan.sh` 输出 JSON（含 `FEATURE_SPEC`、`IMPL_PLAN`）
- `check-prerequisites.sh` 输出 JSON（含 `FEATURE_DIR`、`AVAILABLE_DOCS`）

### 路径 B：使用上游 Specify CLI

> 证据与命令来源：Spec Kit README（https://raw.githubusercontent.com/github/spec-kit/main/README.md）

```bash
# 若你未执行“路径 A”，请先定义演示目录（建议位于配置目录边界内）
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
DEMO_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/vibego/speckit-demos/$RUN_ID"

# 安装（或使用 uvx 一次性运行）
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git

# 在隔离目录初始化上游 demo 项目（不要在主仓库目录执行）
mkdir -p "$DEMO_ROOT"
cd "$DEMO_ROOT"
specify init upstream-spec-kit-demo --ai codex
cd "$DEMO_ROOT/upstream-spec-kit-demo"
specify check
```

预期：
- `$DEMO_ROOT/upstream-spec-kit-demo/.specify/` 存在
- `specify check` 输出工具检查结果

### 演示产物与成功检查

详见：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/demo-flow.md`

```bash
#（演示命令见上文两条路径；此处保留为快速入口提示）
```

## 步骤 4：更新 agent 上下文（可选）

> 若你希望把“当前 feature 的技术上下文”汇总到 agent 文件，可运行：

```bash
bash .specify/scripts/bash/update-agent-context.sh codex
```

注意：
- 该脚本会修改仓库内 `AGENTS.md`（在文件末尾追加 Active Technologies / Recent Changes 等段落）。
- 输出中不得包含任何敏感信息（token、chat_id 等）；如需要示例，请用占位符代替。

## 安全提示（必读）

- 不要在任何规格/计划/日志/报错中粘贴真实 token 与用户标识。
- 运行期文件（日志/状态/数据库）必须留在配置目录边界内（默认 `~/.config/vibego/`），不要落入仓库目录。
