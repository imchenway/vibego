# Vibe Diagram Runtime Workflow

## Scope and activation

Use this skill when relationships, flow, time, causality, state, evidence, or before/after change would be harder to verify as prose. Do not force a diagram when one short sentence, a command, or a small list is clearer.

Follow the user's language for visible content; use English when the language cannot be determined. Preserve explicit uncertainty and distinguish observed facts, inferences, proposed design, and unresolved questions.

## Artifact contract

Produce a self-contained single-file HTML document as the primary artifact. Inline all CSS and JavaScript and keep the document readable without a network connection.

PNG or SVG may be added only when the user explicitly requests an image supplement; either must not replace the HTML artifact. Start from a matching asset template and replace its slots instead of rebuilding a generic card page.

## Capability-based delivery

Choose delivery only from available capabilities:

- `can_write_file`: write the HTML to the requested or current project location and return its path.
- `can_attach_file`: attach the HTML artifact when direct file attachment is available.
- `can_open_local_link`: provide an openable local link in addition to the absolute path.
- `text_only`: return one complete HTML code block and state that file writing is unavailable.

Do not infer delivery behavior from a host name, installation path, or brand.

## Candidate atlas calibration mode

When the user asks for alternatives, first produce a compact atlas of meaningfully different topologies. Label the recommended candidate, state the tradeoff for each option, and keep every candidate grounded in the same evidence. After selection, produce one final artifact rather than leaving a tabbed gallery in the deliverable.

## Automatic routing

Route by the relationship the user must understand:

- System boundary, component, deployment, security, or runtime topology: system architecture.
- Roles, capabilities, domains, rules, or value creation: business architecture.
- Ordered work across roles or exception branches: business flow.
- Calls, returns, async callbacks, retries, and time: code sequence.
- State transitions, entities, lifecycle, or data movement: state/data model.
- Symptom-to-cause evidence and repair verification: fault debugging.
- Current-to-target behavior and rollout or rollback: feature iteration.
- Screen hierarchy, responsive states, or page journeys: page mockup.
- Module contracts, consistency, release switching, or detailed engineering constraints: technical design.
- Options, tradeoffs, recommendations, and decisions: decision communication.
- Requirements, changes, evidence, risk, and sign-off: delivery acceptance.

For any of the six sequence templates, read the `Sequence interaction contract` in its owning reference before editing the template.

## Shared diagram grammar

Give every major node one role, every connector one direction, and every visual encoding one stable meaning. Put the primary reading path in the dominant direction. Use boundaries for ownership or trust, lanes for actors, phases for time, and evidence annotations for claims.

Copy the selected HTML template, preserve `data-diagram-type`, `data-template-family`, `data-template-id`, `data-template-layout`, responsive structure, and slot/macro bindings, then replace visible content. Add local structure only when existing slots cannot express the verified model.

## Layout, arrows, and collision control

Lay out the main path before secondary evidence. Keep arrows outside label boxes, route branches through explicit junctions, and avoid crossings through nodes. Prefer vertical scrolling on narrow screens; never solve density by shrinking essential text below readable size.

Use progressive detail: overview first, local evidence second, full ledger last. A large diagram may use internal navigation, but its default view must still expose the conclusion and primary path.

## Visual quality and accessibility

Use semantic HTML, one visible `h1`, high-contrast text, keyboard-operable controls, visible focus, and reduced-motion handling. Keep touch targets usable, labels concise, and color supplementary rather than the sole carrier of meaning.

On mobile, preserve reading order and avoid page-level horizontal overflow. For print, expand hidden or scrollable content and prevent sticky or transformed layers from clipping the artifact.

## Evidence and uncertainty

Attach file paths, anchors, logs, tests, or user-provided facts to the claims they support. Mark inferred links as inference and future behavior as design. If sources conflict, show the conflict and stop short of a false conclusion.

Do not invent modules, actors, fields, timings, permissions, or root causes merely to make the picture look complete.

## Pre-delivery checks

Before delivery:

1. Confirm the chosen reference and template match the user's question.
2. Confirm the primary path, exceptions, evidence, uncertainty, and result are visible.
3. Confirm the HTML is self-contained, responsive, keyboard readable, and printable.
4. Confirm template identity and macros remain valid.
5. Run the bundled linter when available and fix every reported error.
6. Return the artifact path plus only the brief context needed to use it.
