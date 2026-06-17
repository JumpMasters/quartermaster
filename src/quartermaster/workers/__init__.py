"""Polled background workers: the reservation reaper, the backorder fulfilment
sweep, and the idempotency-key reaper. Each runs in bounded per-item
transactions and drives :mod:`quartermaster.application`.
"""
