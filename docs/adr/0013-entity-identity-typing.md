# 0013 — Entity identity typing: NewType over primitives, natural codes, UUID document IDs

- Status: Accepted
- Date: 2026-06-17

## Context

Domain entities need typed identifiers. We want the type checker to stop a
`location_id` being passed where a `sku_id` is expected, without paying runtime
cost or pushing validation into the pure domain leaf. Three mechanisms were
considered: bare primitives (`str` / `UUID`), `NewType` wrappers, and
value-object dataclasses. Separately, SKU and location identity could be a
surrogate key plus a code column, or the natural code itself.

## Decision

- **Mechanism:** `NewType` over primitives. It gives mypy `--strict` distinctness
  at zero runtime cost. Runtime validation lives at the edges (Pydantic) and in
  the database (`CHECK` constraints), so the domain leaf only needs distinct
  names — no value-object dataclasses.
- **`sku_id` / `location_id`:** natural `str` codes. They *are* the identity;
  there is no surrogate key and no separate code column, matching the data-model
  primary keys.
- **Document IDs** (`order`, `receipt`, `reservation`, `movement`): synthetic
  `UUID`. The generation strategy (v4 / v7 / bigint) is deferred to the
  persistence layer.
- **`IdempotencyKey`:** a `NewType` over `str` (a client-supplied header).

## Consequences

- The type checker rejects mixing identifier types or passing bare primitives,
  with no runtime overhead.
- Natural codes avoid a surrogate-plus-code column and read directly as the
  identity they represent.
- The document-ID generation strategy stays open for the persistence slice to
  settle deliberately.
- `NewType` carries no runtime validation, which is acceptable because the edges
  and the database perform it.
