# Code sequence reference

Use this family to expose calls, returns, asynchronous handoffs, transaction boundaries, retries, and exceptions in time order.

## Templates

- `../assets/templates/code-sequence/participant-timeline.html`: the general participant-column timeline.
- `../assets/templates/code-sequence/async-callback-sequence.html`: producer, bus, consumer, callback, and completion.
- `../assets/templates/code-sequence/transaction-boundary-sequence.html`: caller, transactional work, storage, commit, and rollback.
- `../assets/templates/code-sequence/retry-exception-sequence.html`: attempts, terminal error, and recovery outcome.

Copy the selected template and preserve its distinct sequence grammar. Order messages by execution, show returns separately, and annotate uncertainty instead of inventing a call.

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
