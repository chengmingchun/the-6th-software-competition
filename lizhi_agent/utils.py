from __future__ import annotations

from typing import Iterable, TypeVar

T = TypeVar("T")


def first_or_none(items: Iterable[T]) -> T | None:
    for item in items:
        return item
    return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
