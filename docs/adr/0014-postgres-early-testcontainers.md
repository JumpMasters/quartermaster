# 0014 — Postgres-early testing with testcontainers

- Status: Accepted
- Date: 2026-06-17

## Context

The engine's guarantees — no oversell, reserved never exceeds on-hand, exactly
once — are upheld by Postgres under concurrency: row locks, the conditional
`WHERE` guard, and the chosen isolation level (see 0003, 0005). Building the
command and persistence layers against in-memory fakes would test the
orchestration but none of what actually makes the engine correct, because an
in-memory fake cannot reproduce row-locking behaviour. We also need a way to
provision the test database that behaves identically for contributors and in CI.

## Decision

- **Postgres-early.** Every guard and race is exercised against real Postgres
  from the first command. A record-only fake is permitted *only* for
  unit-testing the envelope's orchestration wiring (claim → guard → stock change
  → movement → store → commit ordering, and rollback on business failure) — never
  for concurrency. There is no concurrency-simulating fake.
- **testcontainers-python.** The test database is provisioned with
  testcontainers, giving a hermetic, byte-identical Postgres locally and in CI
  from a single provisioning path.

## Consequences

- The concurrency guarantees are demonstrated against the real engine rather than
  asserted against a simulation — which is the project's thesis.
- One provisioning path keeps local and CI runs identical, avoiding drift between
  two database setups.
- Contributors need Docker available, and the integration tests are slower than
  pure unit tests. This is accepted: in-memory fakes cannot reproduce the
  row-locking correctness that is the whole point.
