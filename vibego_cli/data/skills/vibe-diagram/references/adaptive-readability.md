# Adaptive Readability and Semantic Relations

Use `adaptive-viewport@1` for presentation and `semantic-relations@1` for authored meaning. The shared runtime may measure, scale, focus, and reset a canvas. It must not infer families, parse visible labels, invent nodes or relations, or rewrite the author's semantic structure.

## Canvas contract

Each generic canvas declares `data-diagram-canvas`, `data-diagram-contract="1"`, a stable `data-diagram-id`, one `data-diagram-profile="graph|matrix|timeline|artboard|ledger"`, `data-diagram-width="contained|auto|wide"`, `data-diagram-height="flow|auto|scroll"`, and `data-diagram-mobile="stack|scroll|summary"`.

Fit width may choose a CSS scale from 75% through 100% only after measuring the authored stage. If 75% cannot fit, keep semantic content unchanged and use scrolling. Print, no-JavaScript, reduced-motion, and runtime-error paths must remain readable without enhancement.

## Semantic relation contract

Authors provide stable identifiers. Use `data-diagram-node-id` and `data-semantic-role` for nodes, `data-diagram-group-id` and `data-semantic-role` for groups, and `data-diagram-relation-id`, `data-from`, `data-to`, `data-relation-kind`, and non-empty `data-semantic` for relations.

Matrix canvases additionally identify axes and cells with `data-matrix-row-id`, `data-matrix-col-id`, `data-matrix-row`, and `data-matrix-col`. Overview/detail projections use authored identifiers and `data-detail-for`. Every mobile summary or structural fallback names the covered canvas with `data-fallback-for`.

## Complexity and disclosure

`contracts/family-policies.json` is the trusted allowlist for the ten generic families and 52 non-sequence templates. Family budgets are hard upper bounds; a template may only narrow them. When a canvas exceeds its budget, author an overview plus linked details instead of hiding semantics in runtime behavior. Progressive disclosure is optional enhancement: the baseline HTML must preserve native navigation, natural document flow, and printable detail content.

## Scope and evidence boundaries

All 52 canonical generic templates are registered under this contract. The six sequence templates remain governed exclusively by `sequence-contract@1`; do not double-parse or rewrite them as generic canvases.

Canonical completeness is a source and static-contract statement. It does not prove rendering in a browser or any client lifecycle. Keep `browser_runtime` pending and `client_runtime` unverified until their respective runtime evidence has actually been collected.
