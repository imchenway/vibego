# Technical design reference

Use this family for module boundaries, API contracts, consistency, release switching, implementation constraints, test evidence, and rollback.

## Templates

- `../assets/templates/technical-design/api-contract-swimlane.html`: caller, interface, service, observation, and contract behavior.
- `../assets/templates/technical-design/data-consistency-boundary.html`: writes, validation, state, compensation, reconciliation, consumers, and audit.
- `../assets/templates/technical-design/module-contract-data-topology.html`: modules, contracts, data, operations, tests, release, and rollback.
- `../assets/templates/technical-design/release-switch-track.html`: build, gate, rollout, observation, switch, and rollback.

Copy the selected template and retain its layout identity. Replace slots with evidence-backed design content.

## Design rules

- Begin with the current entry point and verified implementation chain.
- Define changed, retained, and removed behavior plus compatibility impact.
- Put module ownership, interfaces, schemas, invariants, state transitions, and failure semantics on the main canvas.
- Include permissions, concurrency, consistency, observability, migration, testing, deployment, and rollback when they affect correctness.
- Keep code paths and anchors near the claims they support.
- Distinguish current implementation, proposed design, and unresolved decision.

## Layout and interaction

Use directional topology rather than a card inventory. Keep labels off connector paths, expose critical details without hover, and put dense evidence in accessible details or a bottom ledger. Ensure keyboard access, visible focus, mobile reflow, and print expansion.

