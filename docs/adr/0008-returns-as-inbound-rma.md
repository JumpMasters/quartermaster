# 0008 — Returns are inbound RMA documents that reuse the Receipt lifecycle

- Status: Accepted
- Date: 2026-06-17

## Context

Returned goods must come back into stock. One option is a direct restock
transition on the outbound order — flip the order and add the quantity back.
Another is to treat a return as a distinct inbound document that physically
arrives and is processed like any other receipt.

A direct restock would let stock reappear on a shelf without physically passing
through the dock, and would create a second restock path parallel to putaway.

## Decision

A return is a distinct inbound **Receipt** whose `kind` is `customer_rma`, which
references the order it returns and flows through the same Receipt lifecycle
(arrive → receive → putaway). It is not a transition on the order.

## Consequences

- Segregation of duties holds: returned stock physically arrives, is received,
  and is put away — it never teleports back onto a shelf.
- The Receipt state machine serves both supplier receipts and customer returns,
  so there is one inbound lifecycle to build and test.
- There is a single putaway path; returns do not duplicate it.
- Processing a return takes more steps than flipping a flag, which matches the
  physical reality it models.
