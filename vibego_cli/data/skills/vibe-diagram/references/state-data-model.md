# State and data model reference

Use this family for state transitions, entity relationships, lifecycle stages, data movement, and state/event behavior.

## Templates

- `../assets/templates/state-data-model/data-flow-model.html`: sources, processes, stores, outputs, and trust or consistency boundaries.
- `../assets/templates/state-data-model/er-lite.html`: entities, keys, important fields, relationships, and cardinality.
- `../assets/templates/state-data-model/lifecycle-track.html`: states and milestones across an object's lifetime.
- `../assets/templates/state-data-model/state-event-matrix.html`: valid, invalid, and risky state/event combinations.
- `../assets/templates/state-data-model/state-machine.html`: states, guarded events, transitions, and terminal outcomes.

Copy the selected template and preserve its distinct modeling grammar.

## Modeling rules

- A state is a durable condition; an event causes evaluation; a transition names its guard and effect.
- Show invalid transitions and error states, not only the happy path.
- In entity views, separate identifiers, attributes, relationships, and constraints.
- For every decision-relevant field, record source, unit, enumeration, nullability, idempotency key, and version when applicable.
- Mark concurrency, consistency, compensation, retry, undo, and soft-delete risks beside the state, field, relationship, or transition they affect.
- In data-flow views, label direction, transformation, storage, ownership, and trust or consistency boundaries.
- Do not mix lifecycle order, entity cardinality, and process sequence into an ambiguous single notation.
