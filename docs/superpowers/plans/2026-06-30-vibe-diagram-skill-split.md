# vibe-diagram Skill Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:
> executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `vibe-diagram` from one long always-injected skill into a thin core plus diagram-type reference files.

**Architecture:** Keep `vibego_cli/data/skills/vibe-diagram/SKILL.md` as the always-injected core: delivery, routing,
common diagram red lines, reference index, and self-check. Move type-specific rules into
`vibego_cli/data/skills/vibe-diagram/references/*.md`; tests read core plus references for rule coverage while
separately enforcing core size and reference presence.

**Tech Stack:** Python 3.11, pytest, setuptools package-data, Markdown skill/reference files, existing `agents_sync`
copytree flow.

---

### Task 1: RED tests for split structure

**Files:**

- Modify: `tests/test_builtin_skills_injection.py`
- Modify: `tests/test_agents_sync.py`

- [ ] **Step 1: Add helper functions**

Add constants and helpers near `ROOT`:

```python
VIBE_DIAGRAM_DIR = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram"
VIBE_DIAGRAM_SKILL = VIBE_DIAGRAM_DIR / "SKILL.md"
VIBE_DIAGRAM_REFERENCES = VIBE_DIAGRAM_DIR / "references"


def _read_vibe_diagram_core() -> str:
    return VIBE_DIAGRAM_SKILL.read_text(encoding="utf-8")


def _read_vibe_diagram_reference(name: str) -> str:
    return (VIBE_DIAGRAM_REFERENCES / name).read_text(encoding="utf-8")


def _read_vibe_diagram_all_rules() -> str:
    texts = [_read_vibe_diagram_core()]
    if VIBE_DIAGRAM_REFERENCES.exists():
        texts.extend(path.read_text(encoding="utf-8") for path in sorted(VIBE_DIAGRAM_REFERENCES.glob("*.md")))
    return "\n".join(texts)
```

- [ ] **Step 2: Add failing structure tests**

Add tests that assert:

```python
def test_vibe_diagram_core_is_thin_and_routes_to_references() -> None:
    core = _read_vibe_diagram_core()
    assert core.count("\n") + 1 <= 300
    assert "## 图型规则索引" in core
    assert "选择图型后必须读取对应 reference" in core
    assert "读取失败必须 fail-closed" in core
    assert "references/system-architecture.md" in core
    assert "references/business-architecture.md" in core


def test_vibe_diagram_reference_files_exist_and_are_packaged() -> None:
    expected = {...}
    assert {path.name for path in VIBE_DIAGRAM_REFERENCES.glob("*.md")} == expected
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "data/skills/*/references/*.md" in pyproject_text
```

- [ ] **Step 3: Run RED**

Run:

```bash
python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_core_is_thin_and_routes_to_references tests/test_builtin_skills_injection.py::test_vibe_diagram_reference_files_exist_and_are_packaged
```

Expected: FAIL because references do not exist and core is still 619 lines.

### Task 2: GREEN split skill text

**Files:**

- Modify: `vibego_cli/data/skills/vibe-diagram/SKILL.md`
- Create: `vibego_cli/data/skills/vibe-diagram/references/*.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create 10 reference files**

Create:

```text
system-architecture.md
business-architecture.md
business-flow.md
code-sequence.md
state-data-model.md
fault-debugging.md
feature-iteration.md
page-mockup.md
technical-design.md
decision-communication.md
```

- [ ] **Step 2: Move type-specific rules into references**

Move system architecture, business architecture, business flow, sequence, state/data, fault, feature iteration, page
mockup, technical design, and decision rules out of core and into the matching file.

- [ ] **Step 3: Rewrite core**

Rewrite `SKILL.md` to include frontmatter plus sections:

```markdown
## 核心原则

## AGENTS 配合协议

## 交付铁律

## 自动路由规则

## 图型规则索引

## 共性图形语法门禁

## 布局、箭头与防重叠门禁

## 节点信息承载与证据详情

## CSS 与可访问性规则

## 输出前自检
```

- [ ] **Step 4: Package references**

Add `data/skills/*/references/*.md` to `pyproject.toml` package-data.

- [ ] **Step 5: Run GREEN for structure**

Run the two RED tests again. Expected: PASS.

### Task 3: Refactor existing tests to read all rule files

**Files:**

- Modify: `tests/test_builtin_skills_injection.py`
- Modify: `tests/test_agents_sync.py`

- [ ] **Step 1: Replace single-file rule reads**

Most rule assertions should use `_read_vibe_diagram_all_rules()` instead of reading only `SKILL.md`.

- [ ] **Step 2: Keep targeted reference assertions**

For type-specific tests, read the matching reference directly when useful.

- [ ] **Step 3: Add sync test for references not being injected**

Assert synced AGENTS includes reference index paths but not a reference-only sentinel phrase from
`system-architecture.md` body.

- [ ] **Step 4: Run test subset**

```bash
python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_sync.py
```

Expected: PASS.

### Task 4: Sync AGENTS and verify final delivery

**Files:**

- Modify generated/synced: `AGENTS.md`, `AGENTS-template.md` if needed, global AGENTS targets via agents-sync
- Create: `docs/TASK_20260630_023_vibe-diagram_skill拆分实施验收.html`

- [ ] **Step 1: Run full relevant regression**

```bash
python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py
```

Expected: PASS.

- [ ] **Step 2: Sync AGENTS**

```bash
python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json
```

Expected: JSON with `"ok": true`.

- [ ] **Step 3: Generate final HTML**

Create an HTML summary with changed files, validation results, risks, and next action.
