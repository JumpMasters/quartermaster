"""Async concurrency driver and OCC-retry instrumentation for the harness.

(Type aliases live here; ``drive``/``run_one`` are added in the runner task.)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Sleep = Callable[[float], Awaitable[None]]
Rand = Callable[[], float]
CommandThunk = Callable[[Sleep, Rand], Awaitable[Any]]
