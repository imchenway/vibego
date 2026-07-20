# Business flow reference

Use this family for ordered work, responsibility changes, decisions, exception paths, and stage gates.

## Templates

- `../assets/templates/business-flow/bpmn-light-flow.html`: a compact start, activity, gateway, activity, and outcome path.
- `../assets/templates/business-flow/exception-branch-flow.html`: a dominant path plus an explicit failure or recovery branch.
- `../assets/templates/business-flow/stage-track.html`: four stages with a cross-stage checkpoint strip.
- `../assets/templates/business-flow/swimlane-flow.html`: responsibilities and handoffs across three actors or systems.

Copy the selected template and replace slot content. Preserve its distinct DOM skeleton and directional grammar.

## Modeling rules

- Start with a trigger and end with a business result.
- Label gateways as questions and outgoing paths as mutually understandable outcomes.
- Use lanes only for responsibility; use stages only for time or maturity.
- Keep exceptions connected to the step that can cause them and show rejoin, termination, or compensation.
- Use verb-object activity labels and avoid card-style prose.

