from __future__ import annotations

import re

__all__ = ("refresh_alert_budget_lines",)

_BUDGET_LINE = re.compile(
    r"Guncel butce(?: \(sanal kasa\)| \(test kasa\))?: [0-9][0-9.,]* TL",
)
_STAKE_LINE = re.compile(r"Oynanacak tutar: [0-9][0-9.,]* TL")
_PLAY_LINE = re.compile(
    r'(Nesine\'de "[^"]+" secenegine )[0-9][0-9.,]* TL',
)


def refresh_alert_budget_lines(
    message: str,
    *,
    live_kasa: float,
    stake: float | None = None,
) -> str:
    """Patch alert text with live kasa (and optional stake) from the database."""
    kasa_value = round(float(live_kasa), 2)
    text = _BUDGET_LINE.sub(
        f"Guncel butce: {kasa_value:.2f} TL",
        message,
    )
    if stake is None:
        return text

    stake_value = round(float(stake), 2)
    stake_text = f"{stake_value:.2f} TL"
    text = _STAKE_LINE.sub(f"Oynanacak tutar: {stake_text}", text)
    text = _PLAY_LINE.sub(rf"\g<1>{stake_text}", text)
    return text
