# 0012 — Architectural boundaries are enforced by import-linter

- Status: Accepted
- Date: 2026-06-17

## Context

Quartermaster uses a ports-and-adapters layout with a pure domain core: `domain`
imports nothing internal; `application` imports `domain` and declares the
repository ports; `adapters` implement the ports; `api` and `workers` drive
`application`; and only `app.py`, the composition root, imports concrete
implementations. Documented boundaries erode quietly — a single convenient import
in the wrong direction is easy to add and hard to notice in review.

## Decision

We enforce the internal import graph mechanically with **import-linter**
contracts declared in `pyproject.toml`, run in CI on every change. The domain
leaf is contracted to import no other internal package, and the layered
dependency edges are checked, not merely described in prose.

## Consequences

- A violating import fails CI, so the architecture cannot silently degrade — the
  Python analogue of an import-DAG guard.
- The boundaries are legible: the contracts double as executable documentation of
  the intended dependency direction.
- The contracts carry a small maintenance cost as the package set evolves, which
  is a deliberate trade for durable boundary integrity.
