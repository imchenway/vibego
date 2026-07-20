# Feature iteration reference

Use this family to communicate current behavior, target behavior, the change mechanism, compatibility, rollout, and rollback.

## Templates

- `../assets/templates/feature-iteration/current-target-flow.html`: compare current and target business or system paths.
- `../assets/templates/feature-iteration/current-target-sequence.html`: compare current and target calls in execution order.
- `../assets/templates/feature-iteration/diff-heatmap.html`: map change intensity and risk across areas.
- `../assets/templates/feature-iteration/release-rollback-track.html`: show build, rollout, observation, rollback triggers, and recovery.

Copy the selected template. Preserve a direct mapping between each current element and its changed, retained, or removed target counterpart.

## Iteration rules

- State the current entry point and verified current chain before proposing the target.
- Mark added, modified, retained, and removed behavior explicitly.
- Show compatibility impact, data/state migration, failure behavior, observability, and rollback.
- Tie acceptance checks to changed behavior, not to implementation activity alone.

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
