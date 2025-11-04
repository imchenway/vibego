# Version Management Guide

This project relies on [bump-my-version](https://github.com/callowayproject/bump-my-version) to automate semantic
version management.

## Current Version

Current version: **0.2.11**

## Version Management Tool

### Why bump-my-version?

- ✅ Actively maintained (receives updates every year, including 2025)
- ✅ Native support for `pyproject.toml`
- ✅ Modern CLI experience (colour output and clearer diagnostics)
- ✅ Fully compatible with existing `bump2version` commands
- ✅ Pydantic-based configuration validation

### Installation

```bash
pip install bump-my-version
```

## Usage

### Method 1: Convenience Script (Recommended ⭐)

The repository provides `scripts/bump_version.sh`, which handles virtual environment paths and can **optionally commit
version bumps automatically**.

#### 🎯 Automatic Commit Behaviour

When auto-commit is enabled, the script stages pending changes and creates a commit with a message derived from the
selected bump level:

| Bump level | Commit message           | Typical use case              |
|------------|--------------------------|-------------------------------|
| `patch`    | `fix: bugfixes`          | Bug fixes                     |
| `minor`    | `feat: add new features` | Backwards-compatible features |
| `major`    | `feat!: major changes`   | Breaking changes              |

**Workflow:**

1. Verify the working tree is clean (unless `--allow-dirty` is used).
2. Stage local modifications and create the auto-generated commit.
3. Increment the version.
4. Create the version bump commit (and accompanying git tag).

---

#### 1. Check the Current Version

```bash
./scripts/bump_version.sh show
```

Example output:

```
0.2.11
```

#### 2. Bump the Version (with Auto-Commit)

```bash
# Bump patch version (0.2.11 → 0.2.12)
# Auto-commit message: fix: bugfixes
./scripts/bump_version.sh patch

# Bump minor version (0.2.11 → 0.3.0)
# Auto-commit message: feat: add new features
./scripts/bump_version.sh minor

# Bump major version (0.2.11 → 1.0.0)
# Auto-commit message: feat!: major changes
./scripts/bump_version.sh major
```

#### 3. Disable Auto-Commit

```bash
# Only increment the version; leave local changes uncommitted
./scripts/bump_version.sh patch --no-auto-commit
```

#### 4. Preview Changes (Dry Run)

```bash
# Preview a patch bump without touching the working tree
./scripts/bump_version.sh patch --dry-run

# Preview a patch bump even with local modifications
./scripts/bump_version.sh patch --dry-run --allow-dirty
```

#### 5. Show Script Help

```bash
./scripts/bump_version.sh
# or
./scripts/bump_version.sh --help
```

---

### Method 2: Directly Invoke bump-my-version

If the project virtual environment is already active, you can call `bump-my-version` directly.

#### 1. Show the Current Version

```bash
bump-my-version show current_version
```

Example output:

```
0.2.11
```

#### 2. Increment the Version

```bash
# Bump patch version
bump-my-version bump patch

# Bump minor version
bump-my-version bump minor

# Bump major version
bump-my-version bump major
```

#### 3. Manually Set a Specific Version

```bash
bump-my-version bump --new-version 1.0.0
```

#### 4. Dry Run (Preview Changes)

```bash
# Preview a patch bump
bump-my-version bump patch --dry-run --verbose

# Preview a minor bump
bump-my-version bump minor --dry-run --verbose

# Preview a major bump
bump-my-version bump major --dry-run --verbose
```

#### 5. Work with Uncommitted Changes

```bash
# Allow running even when the working tree has pending changes
bump-my-version bump patch --allow-dirty
```
