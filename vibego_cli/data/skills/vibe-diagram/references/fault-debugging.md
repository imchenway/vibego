# Fault debugging reference

Use this family to connect an observed symptom to evidence, candidate causes, verified breakpoints, a repair, and regression proof.

## Templates

- `../assets/templates/fault-debugging/before-after-flow.html`: compare the observed path before and after repair.
- `../assets/templates/fault-debugging/bpmn-debug-flow.html`: reproduce, inspect, decide, isolate, and verify.
- `../assets/templates/fault-debugging/causal-chain.html`: symptom-to-mechanism-to-impact causal reasoning.
- `../assets/templates/fault-debugging/debugging-sequence.html`: time-ordered evidence across actors and dependencies.
- `../assets/templates/fault-debugging/state-data-breakpoint.html`: correlate state transitions with writes and invalid data.

Copy the selected template. Keep observed evidence distinct from hypotheses; do not label a candidate root cause as confirmed until the discriminating check passes.

## Debugging rules

- Anchor the first node in the actual symptom and impact.
- The current implementation chain is the primary canvas: show the current code, interface, state, configuration, or runtime path step by step until the fault point.
- Attach evidence as E# to the chain node it observes, with source, observation, and the judgment it supports. Classify each hypothesis as supported, excluded, or pending evidence.
- Put a confirmed root cause R# on the chain itself. When evidence is incomplete, label only the highest-suspicion point H#; never present H# as R#.
- Put the repair, verification, and rollback beside the fault point. State which link is cut, replaced, compensated, or guarded, then include the command, result, rollback action, and residual risk.
- Show at least two candidates until evidence rules one in.
- Place logs, traces, runtime inspection, queries, or reproductions at the step they support.
- Explain the repair mechanism and show the exact regression check and remaining uncertainty.
- Use a before/after view when the repair changes multiple important nodes, a call or data chain, state, a contract, a permission boundary, or the user's primary path. Keep before left or above and after right or below.

## Sequence interaction contract

Sequence contract version: `1`.

- Give each semantic participant a stable `data-participant-id`.
- Give each primary message `data-from`, `data-to`, and `data-message-kind`; optional `data-semantic` may describe intent. Core kinds are `sync`, `return`, `async`, `self`, and `error`.
- Never infer endpoints from visible route text. Resolve endpoints only through structured participant identifiers.
- Treat `data-sequence-width="auto|contained|wide"` and `data-sequence-height="auto|flow|scroll"` as orthogonal modes.
- In auto width, use the viewport only when horizontal overflow exists; then expose `Fit width`, `75%`, `90%`, and `100%`. Keep essential text at or above `12 CSS px` after scaling.
- In auto height, create internal vertical scrolling and a `sticky participant header` only beyond `clamp(520px, 75vh, 900px)`.
- When a canvas exceeds `12 semantic participants`, `40 primary sequence messages`, or `4 major sequence phases`, provide an `overview sequence` and mapped `detail sequence` diagrams. The renderer must not merge semantically different participants.
- Provide a sequential mobile ledger, a readable no-JavaScript baseline, and print expansion that removes sticky positioning, transforms, fixed heights, and clipping.
