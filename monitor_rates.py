"""Site-level rate multiplier normalization (raw / MONITOR_RATE_DIVISOR).

Pure helpers shared by Sub2API and New-API group monitors.
Provider raw stays on ``rate_multiplier``; business rate is
``rate_multiplier_effective``.
"""

from __future__ import annotations

import math
from typing import Any


def parse_rate_divisor(raw: str | None) -> float:
    """Parse MONITOR_RATE_DIVISOR: empty/missing -> 1; must be finite and > 0."""
    if raw is None:
        return 1.0
    text = str(raw).strip()
    if not text:
        return 1.0
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"MONITOR_RATE_DIVISOR must be a positive finite number: {raw!r}"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"MONITOR_RATE_DIVISOR must be a positive finite number: {raw!r}"
        )
    return value


def annotate_group_rates(
    groups: list[dict[str, Any]],
    divisor: float,
) -> list[dict[str, Any]]:
    """Return shallow copies with ``rate_multiplier_effective = raw / divisor``.

    Does not mutate input groups. Requires each group to already carry a finite
    numeric ``rate_multiplier`` (provider contract); does not invent missing raw.
    """
    if not math.isfinite(divisor) or divisor <= 0:
        raise ValueError(f"rate divisor must be positive finite, got {divisor!r}")
    out: list[dict[str, Any]] = []
    for group in groups:
        item = dict(group)
        raw = item.get("rate_multiplier")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(
                f"rate_multiplier must be a finite number, got {raw!r}"
            )
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError(
                f"rate_multiplier must be a finite number, got {raw!r}"
            )
        item["rate_multiplier_effective"] = number / divisor
        out.append(item)
    return out
