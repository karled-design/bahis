"""Marj temizleme (devig): kitapci fiyatindan gercek olasiligi cikarma.

Iki yontem:

* **Orantisal (multiplicative)** — `p_i = q_i / sum(q)`. Basit ama marji her
  secime esit ORANDA dagitir. Kitapcilar marji boyle koymaz: uzak ihtimallere
  (yuksek oran) daha fazla marj yuklerler. Sonuc, favori-uzak ihtimal
  yanliligi: uzun oranlarin gercek olasiligi oldugundan yuksek gorunur ve
  sistem orada sahte pozitif EV bulur.
* **Shin (1993)** — piyasada bilgili bahisci orani `z` kadar varsayar ve marji
  bu modele gore dagitir; uzak ihtimallerden daha fazla marj cikarir. Uzun
  oranlarda orantisal yontemden belirgin sekilde farklidir, favori tarafinda
  ikisi neredeyse ayni sonucu verir.

Her iki fonksiyon da olasilik listesi dondurur (toplam 1.0) veya veri
guvenilmezse None.
"""

from __future__ import annotations

import math

__all__ = (
    "DEVIG_METHODS",
    "DEFAULT_DEVIG_METHOD",
    "fair_probabilities",
    "proportional_probabilities",
    "shin_probabilities",
)

DEFAULT_DEVIG_METHOD = "shin"
DEVIG_METHODS = ("proportional", "shin")

_MAX_SHIN_Z = 0.35
_SHIN_ITERATIONS = 60
_SHIN_TOLERANCE = 1e-10


def _implied(odds: list[float]) -> list[float] | None:
    implied: list[float] = []
    for value in odds:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 1.0:
            return None
        implied.append(1.0 / numeric)
    if len(implied) < 2:
        return None
    return implied


def proportional_probabilities(odds: list[float]) -> list[float] | None:
    """Orantisal devig: `p_i = (1/oran_i) / toplam`."""
    implied = _implied(odds)
    if implied is None:
        return None
    total = sum(implied)
    if total <= 0.0:
        return None
    return [value / total for value in implied]


def _shin_probability(q: float, total: float, z: float) -> float:
    inner = z * z + 4.0 * (1.0 - z) * (q * q) / total
    return (math.sqrt(inner) - z) / (2.0 * (1.0 - z))


def shin_probabilities(odds: list[float]) -> list[float] | None:
    """Shin devig: bilgili bahisci payi `z` icin ikiye bolme ile cozer."""
    implied = _implied(odds)
    if implied is None:
        return None
    total = sum(implied)
    if total <= 0.0:
        return None
    if total <= 1.0:
        # Marj yok (veya negatif): Shin tanimsiz, orantisal yonteme dus.
        return [value / total for value in implied]

    low, high = 0.0, _MAX_SHIN_Z
    probabilities = [value / total for value in implied]
    for _ in range(_SHIN_ITERATIONS):
        mid = (low + high) / 2.0
        probabilities = [_shin_probability(q, total, mid) for q in implied]
        deviation = sum(probabilities) - 1.0
        if abs(deviation) < _SHIN_TOLERANCE:
            break
        # Toplam 1'in ustundeyse daha buyuk z gerekir.
        if deviation > 0.0:
            low = mid
        else:
            high = mid

    normalizer = sum(probabilities)
    if normalizer <= 0.0:
        return None
    return [value / normalizer for value in probabilities]


def fair_probabilities(odds: list[float], *, method: str = DEFAULT_DEVIG_METHOD) -> list[float] | None:
    key = str(method or "").strip().casefold()
    if key == "proportional":
        return proportional_probabilities(odds)
    if key == "shin":
        return shin_probabilities(odds)
    return None
