# 版本管理指南

本项目使用 [bump-my-version](https://github.com/callowayproject/bump-my-version) 进行自动化版本管理。

## 当前版本

当前版本：**0.2.11**

## 版本管理工具

### 工具选择

本项目使用 **bump-my-version**，这是 bump2version 的官方继任者，具有以下优势：

- ✅ 活跃维护（2025年持续更新）
- ✅ 原生支持 pyproject.toml 配置
- ✅ 现代化 CLI（彩色输出，更好的错误提示）
- ✅ 完全兼容 bump2version 命令
- ✅ Pydantic 配置验证

### 安装

```bash
pip install bump-my-version
```

## 使用方法

### 方式1：使用便捷脚本（推荐⭐）

项目提供了一个便捷脚本 `scripts/bump_version.sh`，自动处理虚拟环境路径问题，并支持**自动提交代码修改**。

#### 🎯 自动 Commit 功能

脚本会根据版本类型**自动提交当前未提交的修改**：

| 版本类型    | Commit 消息       | 适用场景       |
|---------|-----------------|------------|
| `patch` | `fix: bugfixes` | Bug 修复     |
| `minor` | `feat: 添加新功能`   | 新功能，向后兼容   |
| `major` | `feat!: 重大变更`   | 重大变更，不向后兼容 |

**工作流程：**

1. 检测是否有未提交的修改
2. 如有修改，自动创建 commit（使用对应的消息）
3. 递增版本号
4. 创建版本 commit 和 git tag

---

#### 1. 查看当前版本

```bash
./scripts/bump_version.sh show
```

输出示例：

```
0.2.11
```

#### 2. 递增版本号（自动提交）

```bash
# 递增补丁版本（0.2.11 → 0.2.12）
# 自动提交：fix: bugfixes
./scripts/bump_version.sh patch

# 递增次版本（0.2.11 → 0.3.0）
# 自动提交：feat: 添加新功能
./scripts/bump_version.sh minor

# 递增主版本（0.2.11 → 1.0.0）
# 自动提交：feat!: 重大变更
./scripts/bump_version.sh major
```

#### 3. 禁用自动提交

如果不想自动提交当前修改，添加 `--no-auto-commit` 参数：

```bash
# 仅递增版本，不提交当前修改
./scripts/bump_version.sh patch --no-auto-commit
```

#### 4. 预览变更（Dry-run）

```bash
# 预览补丁版本递增（不会提交任何内容）
./scripts/bump_version.sh patch --dry-run

# 在脏工作目录中预览
./scripts/bump_version.sh patch --dry-run --allow-dirty
```

#### 5. 查看帮助

```bash
./scripts/bump_version.sh
# 或
./scripts/bump_version.sh --help
```

---

### 方式2：直接使用 bump-my-version 命令

如果您激活了项目虚拟环境，可以直接使用 bump-my-version：

#### 1. 查看当前版本

```bash
bump-my-version show current_version
```

输出示例：

```
0.2.11
```

#### 2. 递增版本号

```bash
# 递增补丁版本
bump-my-version bump patch

# 递增次版本
bump-my-version bump minor

# 递增主版本
bump-my-version bump major
```

#### 3. 手动设置版本号

```bash
bump-my-version bump --new-version 1.0.0
```

#### 4. Dry-run（预览变更）

```bash
# 预览递增 patch 版本
bump-my-version bump patch --dry-run --verbose

# 预览递增 minor 版本
bump-my-version bump minor --dry-run --verbose

# 预览递增 major 版本
bump-my-version bump major --dry-run --verbose
```

#### 5. 在未提交的 Git 工作目录中运行

如果 Git 工作目录有未提交的修改，需要添加 `--allow-dirty` 参数：

```bash
bump-my-version bump patch --allow-dirty
```

---

### 方式3：使用完整路径

如果未激活虚拟环境，可以使用完整路径：

```bash
$HOME/.config/vibego/runtime/.venv/bin/bump-my-version show current_version
$HOME/.config/vibego/runtime/.venv/bin/bump-my-version bump patch
```

## 自动化操作

执行版本递增时，bump-my-version 会自动完成以下操作：

1. ✅ 更新 `pyproject.toml` 中的 `version` 字段
2. ✅ 更新 `vibego_cli/__init__.py` 中的 `__version__` 变量
3. ✅ 更新 `pyproject.toml` 中的 `tool.bumpversion.current_version` 配置
4. ✅ 创建 Git commit，提交消息格式：`chore: bump version {old} → {new}`
5. ✅ 创建 Git tag，标签格式：`v{new_version}`（如 `v0.2.12`）

## 配置说明

版本管理配置位于 `pyproject.toml` 文件中：

```toml
[tool.bumpversion]
current_version = "0.2.11"
parse = "(?P<major>\\d+)\\.(?P<minor>\\d+)\\.(?P<patch>\\d+)"
serialize = ["{major}.{minor}.{patch}"]
search = "{current_version}"
replace = "{new_version}"
regex = false
ignore_missing_version = false
tag = true
sign_tags = false
tag_name = "v{new_version}"
tag_message = "Bump version: {current_version} → {new_version}"
allow_dirty = false
commit = true
message = "chore: bump version {current_version} → {new_version}"
commit_args = ""

[[tool.bumpversion.files]]
filename = "pyproject.toml"
search = 'version = "{current_version}"'
replace = 'version = "{new_version}"'

[[tool.bumpversion.files]]
filename = "vibego_cli/__init__.py"
search = '__version__ = "{current_version}"'
replace = '__version__ = "{new_version}"'
```

### 配置项说明

| 配置项               | 说明                | 默认值                                                     |
|-------------------|-------------------|---------------------------------------------------------|
| `current_version` | 当前版本号             | `0.2.11`                                                |
| `tag`             | 是否创建 Git 标签       | `true`                                                  |
| `tag_name`        | Git 标签名称格式        | `v{new_version}`                                        |
| `commit`          | 是否自动创建 Git commit | `true`                                                  |
| `message`         | Commit 消息格式       | `chore: bump version {current_version} → {new_version}` |
| `allow_dirty`     | 是否允许在脏工作目录中运行     | `false`                                                 |

## 实际使用示例

### 场景1：修复 bug 后发布补丁版本（推荐⭐）

**新的简化流程（使用自动 commit）：**

```bash
# 1. 修复 bug（代码修改）
vim bot.py

# 2. 一键发布（自动提交 + 递增版本）
./scripts/bump_version.sh patch

# 3. 推送到远程
git push && git push --tags
```

**脚本自动执行的操作：**

1. ✅ 检测到未提交的修改
2. ✅ 自动创建 commit：`fix: bugfixes`
3. ✅ 递增版本：`0.2.11` → `0.2.12`
4. ✅ 创建版本 commit：`chore: bump version 0.2.11 → 0.2.12`
5. ✅ 创建 git tag：`v0.2.12`

---

### 场景2：新增功能后发布次版本

```bash
# 1. 开发新功能
vim bot.py

# 2. 一键发布
./scripts/bump_version.sh minor

# 3. 推送到远程
git push && git push --tags
```

**脚本自动执行的操作：**

1. ✅ 自动创建 commit：`feat: 添加新功能`
2. ✅ 递增版本：`0.2.11` → `0.3.0`
3. ✅ 创建版本 commit 和 tag `v0.3.0`

---

### 场景3：重大变更后发布主版本

```bash
# 1. 完成重大变更
vim bot.py

# 2. 一键发布
./scripts/bump_version.sh major

# 3. 推送到远程
git push && git push --tags
```

**脚本自动执行的操作：**

1. ✅ 自动创建 commit：`feat!: 重大变更`
2. ✅ 递增版本：`0.2.11` → `1.0.0`
3. ✅ 创建版本 commit 和 tag `v1.0.0`

---

### 场景4：传统流程（手动提交，不使用自动 commit）

如果您希望手动控制 commit 消息：

```bash
# 1. 修复 bug 并手动提交
git add .
git commit -m "fix: 修复登录超时问题"

# 2. 递增版本（禁用自动 commit）
./scripts/bump_version.sh patch --no-auto-commit

# 3. 推送到远程
git push && git push --tags
```

---

### 场景5：预览版本变更

```bash
# 查看当前版本
./scripts/bump_version.sh show

# 预览补丁版本递增（不实际修改文件）
./scripts/bump_version.sh patch --dry-run --allow-dirty
```

---

### 场景6：完整的开发流程示例

```bash
# 1. 创建功能分支
git checkout -b feature/new-feature

# 2. 开发功能
vim bot.py
vim master.py

# 3. 测试功能
pytest tests/

# 4. 一键发布（自动提交 + 递增版本）
./scripts/bump_version.sh minor

# 5. 推送到远程
git push origin feature/new-feature --tags

# 6. 创建 Pull Request（GitHub/GitLab）
# 7. 合并到主分支后，版本自动发布
```

## 注意事项

1. **自动 Commit 功能**
    - 便捷脚本 `scripts/bump_version.sh` 会自动提交未提交的修改
    - Commit 消息根据版本类型自动生成（patch/minor/major）
    - 如不想自动提交，使用 `--no-auto-commit` 参数
    - Dry-run 模式（`--dry-run`）不会执行任何提交操作

2. **Git 工作目录状态**
    - 使用便捷脚本：会自动处理未提交的修改（自动 commit）
    - 直接使用 bump-my-version：要求工作目录干净，或添加 `--allow-dirty` 参数

3. **版本号格式**
    - 本项目使用语义化版本号（Semantic Versioning）：`MAJOR.MINOR.PATCH`
    - MAJOR：重大不兼容变更
    - MINOR：新增功能，向后兼容
    - PATCH：bug 修复，向后兼容

4. **Git 标签推送**
    - 版本递增后会自动创建 Git tag
    - 需要手动推送标签到远程：`git push --tags`

5. **版本一致性**
    - bump-my-version 会自动确保 `pyproject.toml` 和 `vibego_cli/__init__.py` 中的版本号保持一致
    - 不要手动修改版本号，始终使用 bump-my-version 工具

## 常见问题

### Q1: 自动 commit 的消息能自定义吗？

**A:** 当前版本使用固定的 commit 消息：

- `patch` → `fix: bugfixes`
- `minor` → `feat: 添加新功能`
- `major` → `feat!: 重大变更`

如需自定义消息，建议：

1. 手动提交代码：`git add . && git commit -m "你的消息"`
2. 使用 `--no-auto-commit` 递增版本：`./scripts/bump_version.sh patch --no-auto-commit`

### Q2: 如何查看将要进行的更改？

使用 `--dry-run` 参数：

```bash
# 使用便捷脚本
./scripts/bump_version.sh patch --dry-run --allow-dirty

# 或直接使用 bump-my-version
bump-my-version bump patch --dry-run --verbose
```

### Q3: 如何撤销错误的版本递增？

如果还未推送到远程：

```bash
# 重置到上一个 commit
git reset --hard HEAD~1

# 删除错误的 tag
git tag -d v0.2.12
```

如果已推送到远程：

```bash
# 不建议撤销已推送的版本
# 建议递增到下一个版本
bump-my-version bump patch
```

### Q4: 如何跳过 Git commit 和 tag？

修改 `pyproject.toml` 配置：

```toml
[tool.bumpversion]
commit = false
tag = false
```

### Q5: 如何添加更多文件到版本管理？

在 `pyproject.toml` 中添加更多文件配置：

```toml
[[tool.bumpversion.files]]
filename = "path/to/file.py"
search = 'VERSION = "{current_version}"'
replace = 'VERSION = "{new_version}"'
```

## 参考资料

- [bump-my-version 官方文档](https://callowayproject.github.io/bump-my-version/)
- [语义化版本规范](https://semver.org/lang/zh-CN/)
- [GitHub: bump-my-version](https://github.com/callowayproject/bump-my-version)

---

**最后更新：** 2025-10-23
