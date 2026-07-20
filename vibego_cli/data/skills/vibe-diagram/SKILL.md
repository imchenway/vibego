---
name: vibe-diagram
description: Use when a visual artifact is needed to explain architecture, workflow, sequence behavior, state and data models, debugging evidence, feature iteration, a page mockup, technical design, decision communication, or delivery acceptance.
---

# Vibe Diagram

## Update gate

On every invocation, resolve this skill directory and run `python3 <skill-root>/scripts/update_skill.py --check-and-update --json` before doing the requested work.

- For `current`, `updated`, or `managed`, continue normally.
- For `offline` or `failed`, continue with the installed version and mention the update status briefly without blocking the requested artifact.
- Never replace the installed tree directly or bypass the updater's integrity check, lock, backup, or rollback behavior.
- For an explicit manual update request, run the same script with `--force-check --json` and report the exact result.

## Runtime workflow

After the update gate finishes, read [the runtime workflow](references/runtime-workflow.md) completely from the current skill directory, then follow it for the request. Resolve this path after a successful update so the current invocation uses the newly installed workflow.

## Reference index

- [Runtime workflow](references/runtime-workflow.md)
- [Adaptive readability and semantic relations](references/adaptive-readability.md)
- [Business architecture](references/business-architecture.md)
- [Business flow](references/business-flow.md)
- [Code sequence](references/code-sequence.md)
- [Decision communication](references/decision-communication.md)
- [Delivery acceptance](references/delivery-acceptance.md)
- [Fault debugging](references/fault-debugging.md)
- [Feature iteration](references/feature-iteration.md)
- [Page mockup](references/page-mockup.md)
- [State and data model](references/state-data-model.md)
- [System architecture](references/system-architecture.md)
- [Technical design](references/technical-design.md)
