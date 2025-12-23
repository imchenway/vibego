# Demo Flow: 最小互补工作流演示（vibego × speckit）

**Date**: 2025-12-22  
**Feature**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/spec.md`  
**Assessment Report**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`  
**Conventions**: `/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/conventions.md`

## 必读：安全与“不污染仓库”原则（硬性）

- 不要在任何输出中粘贴/回显真实 token、chat_id、用户标识等敏感信息（参见 Conventions）。
- 演示必须在**隔离工作区**执行（推荐 git worktree），避免影响你的主工作区与当前分支。
- 演示产物必须写入配置目录边界（`$XDG_CONFIG_HOME` 或 `~/.config`）下的临时目录，演示结束可整体删除。
- 默认**不覆盖**已有产物：若遇到路径冲突，必须停止并改用新的 run_id 或新的目标目录。

## 参考资料（官方/可核验）

- Spec Kit：https://github.com/github/spec-kit
- Specify CLI 安装与用法（README）：https://raw.githubusercontent.com/github/spec-kit/main/README.md
- Spec-Driven Development 流程（上游文档）：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
- uv：https://docs.astral.sh/uv/
- git worktree：https://git-scm.com/docs/git-worktree

## 演示输入/输出约定

演示会用到以下变量（示例值仅用于说明）：

```bash
REPO_ROOT="/Users/david/hypha/tools/vibego"
RUN_ID="<uuid>"  # 每次演示唯一，建议使用 uuid
DEMO_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/vibego/speckit-demos/$RUN_ID"
```

演示的“预期产物清单”以**文件存在与路径可追踪**为准，不要求实现任何 HTTP 服务。

## 路径 A：不使用上游 Specify CLI（仅用仓库内 `.specify` 脚本）

目标：证明 vibego 已具备 speckit 的“spec → plan”骨架，且可在隔离工作区重复执行。

### 步骤

1) 创建隔离工作区（worktree）与演示目录

```bash
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
DEMO_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/vibego/speckit-demos/$RUN_ID"
mkdir -p "$DEMO_ROOT"
git -C "$REPO_ROOT" worktree add "$DEMO_ROOT/worktree" HEAD
cd "$DEMO_ROOT/worktree"
```

2) 生成一个新的 feature（会创建新分支与 `specs/<feature>/spec.md`）

```bash
bash .specify/scripts/bash/create-new-feature.sh --json --short-name "demo-speckit-flow" "Demo: speckit workflow in vibego"
```

预期输出（JSON）包含以下字段（值因环境而异）：
- `BRANCH_NAME`：形如 `NNN-demo-speckit-flow`
- `SPEC_FILE`：绝对路径，形如 `.../specs/NNN-demo-speckit-flow/spec.md`

3) 初始化 plan（复制模板到 `plan.md`）

```bash
bash .specify/scripts/bash/setup-plan.sh --json
```

预期输出（JSON）包含：
- `FEATURE_SPEC`：`.../specs/<feature>/spec.md`
- `IMPL_PLAN`：`.../specs/<feature>/plan.md`
- `BRANCH`：当前分支名（应为新 feature 分支）

4) 运行前置检查（验证产物存在，并输出可用文档列表）

```bash
bash .specify/scripts/bash/check-prerequisites.sh --json
```

预期输出（JSON）包含：
- `FEATURE_DIR`：绝对路径，指向 `specs/<feature>/`
- `AVAILABLE_DOCS`：已存在的可选文档（此时通常包含 `quickstart.md` 以外较少内容）

### 成功检查

- worktree 目录存在：`$DEMO_ROOT/worktree`
- 新 feature 目录存在：`$REPO_ROOT/specs/<feature>/`
- `spec.md` 与 `plan.md` 均存在且路径为绝对路径

### 失败恢复（常见）

- worktree 创建失败：检查主工作区是否有未保存改动；必要时换一个 `DEMO_ROOT` 重试。
- 分支创建失败（冲突/已存在）：换一个 `--short-name`，或清理旧演示 worktree 后重试。
- 路径不可写：确认 `DEMO_ROOT` 有权限；不要把演示目录放到仓库内。

### 清理（推荐）

```bash
git -C "$REPO_ROOT" worktree remove "$DEMO_ROOT/worktree" --force
rm -rf "$DEMO_ROOT"
```

## 路径 B：使用上游 Specify CLI（对比上游模板与工作流）

目标：证明上游 Spec Kit 的 `specify` CLI 可用于初始化/同步模板；vibego 可选择性吸收其结构与理念。

### 步骤

1) 安装（或一次性运行）Specify CLI

```bash
# 持久安装（官方推荐）
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git

# 或者一次性运行（不安装）
# uvx --from git+https://github.com/github/spec-kit.git specify init <PROJECT_NAME>
```

2) 在隔离目录初始化一个上游 demo 项目

```bash
mkdir -p "$DEMO_ROOT"
cd "$DEMO_ROOT"
specify init upstream-spec-kit-demo --ai codex
cd "$DEMO_ROOT/upstream-spec-kit-demo"
specify check
```

### 预期产物清单（以官方模板为准）

- `$DEMO_ROOT/upstream-spec-kit-demo/`：上游初始化的 demo 项目目录
- 该目录内会包含 `.specify/`（模板/脚本等，具体结构以官方模板为准）

### 成功检查

- `specify check` 输出工具检查结果（是否检测到 git 与 agent 工具等）
- `.specify/` 目录存在：`$DEMO_ROOT/upstream-spec-kit-demo/.specify/`

### 失败恢复（常见）

- `uv` 未安装：按 uv 官方文档安装或改用系统包管理器（见参考链接）。
- Python 版本不足：上游 Specify CLI 要求 Python 3.11+（见 Spec Kit README）。
- 网络受限：改用路径 A（只依赖仓库内脚本）。
