"""Gecmis kapanis orani backtest'i (football-data.co.uk CSV'leri).

Amac: canli kotayi harcamadan "+%2.2 EV esigi dogru mu, hangi ligde/pazarda
deger var" sorusunu gecmis veriye baglamak.

Yontem canli boru hattiyla ayni: keskin oranlar Shin ile devig edilir
(core/devig.py), soft oranla beklenen deger hesaplanir, esigi gecen secimler
oynanmis sayilir ve gercek sonucla kapatilir.

Iki kip vardir:

* ``live``  — keskin referans ACILIS Pinnacle orani. Bahis anininda gercekten
  gorulebilecek bilgiyle calisir; sistemin durust tekraridir.
* ``clv``   — keskin referans KAPANIS Pinnacle orani. Ileriye bakar, bu yuzden
  ROI'si strateji vaadi degildir; yalnizca "soft kitapci kapanisin gerisinde
  kaliyor mu" sorusunun ust sinirini olcer.

Her iki kipte de oynanan her secim icin CLV ayrica raporlanir:
``clv = soft_oran / kapanis_keskin_oran - 1`` (core/clv_tracker.py ile ayni
formul), cunku kisa serilerde ROI'den once CLV yakinsar.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from core.devig import fair_probabilities
from core.market_catalog import totals_market_key

__all__ = (
    "DEFAULT_THRESHOLDS",
    "SelectionOutcome",
    "build_selections",
    "parse_football_data_csv",
    "run_closing_backtest",
)

Mode = Literal["live", "clv"]

DEFAULT_THRESHOLDS: tuple[float, ...] = (0.01, 0.022, 0.03, 0.05)
_STAKE = 100.0
# Devig bandi canli hatla ayni: bu araligin disindaki toplam olasilik veri hatasi.
_MIN_OVERROUND = 1.0
_MAX_OVERROUND = 1.30
# Absurt EV = eslesme/veri hatasi; canli tarafta MAX_EV_THRESHOLD ile ayni rol.
_MAX_EV = 0.15

_TOTALS_LINE = 2.5


@dataclass(frozen=True)
class SelectionOutcome:
    """Tek bir gecmis secim: soft fiyat, keskin referans, kapanis ve sonuc."""

    league: str
    date: str
    match_name: str
    market: str
    soft_odds: float
    sharp_odds: float
    closing_sharp_odds: float
    fair_probability: float
    won: bool

    @property
    def ev(self) -> float:
        return (self.soft_odds * self.fair_probability) - 1.0

    @property
    def profit(self) -> float:
        return _STAKE * (self.soft_odds - 1.0) if self.won else -_STAKE

    @property
    def clv(self) -> float:
        if self.closing_sharp_odds <= 0.0:
            return 0.0
        return (self.soft_odds / self.closing_sharp_odds) - 1.0


def _to_float(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 1.0 else None


def _column(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        value = _to_float(row.get(name))
        if value is not None:
            return value
    return None


def _match_result_market(row: dict[str, str]) -> str | None:
    result = str(row.get("FTR", "")).strip().upper()
    return {"H": "MS1", "D": "X", "A": "MS2"}.get(result)


def _goal_count(raw: object) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        goals = int(text)
    except ValueError:
        return None
    return goals if goals >= 0 else None


def _totals_market(row: dict[str, str]) -> str | None:
    home = _goal_count(row.get("FTHG"))
    away = _goal_count(row.get("FTAG"))
    if home is None or away is None:
        return None
    goals = home + away
    side = "UST" if goals > _TOTALS_LINE else "ALT"
    return totals_market_key(side, _TOTALS_LINE)


def _devigged(odds_by_market: dict[str, float]) -> dict[str, float] | None:
    markets = list(odds_by_market)
    implied = [1.0 / odds_by_market[market] for market in markets]
    overround = sum(implied)
    if not (_MIN_OVERROUND < overround <= _MAX_OVERROUND):
        return None
    probabilities = fair_probabilities([1.0 / value for value in implied])
    if probabilities is None or len(probabilities) != len(markets):
        return None
    return dict(zip(markets, probabilities))


def _family_selections(
    row: dict[str, str],
    *,
    league: str,
    date: str,
    match_name: str,
    winning_market: str,
    soft_columns: dict[str, tuple[str, ...]],
    sharp_columns: dict[str, tuple[str, ...]],
    closing_columns: dict[str, tuple[str, ...]],
    mode: Mode,
) -> list[SelectionOutcome]:
    soft = {market: _column(row, *cols) for market, cols in soft_columns.items()}
    sharp = {market: _column(row, *cols) for market, cols in sharp_columns.items()}
    closing = {market: _column(row, *cols) for market, cols in closing_columns.items()}
    if any(value is None for value in soft.values()):
        return []
    if any(value is None for value in closing.values()):
        return []

    reference = closing if mode == "clv" else sharp
    if any(value is None for value in reference.values()):
        return []

    fair = _devigged({market: float(value) for market, value in reference.items() if value})
    if fair is None:
        return []

    selections: list[SelectionOutcome] = []
    for market, soft_odds in soft.items():
        reference_odds = reference[market]
        closing_odds = closing[market]
        if soft_odds is None or reference_odds is None or closing_odds is None:
            continue
        selections.append(
            SelectionOutcome(
                league=league,
                date=date,
                match_name=match_name,
                market=market,
                soft_odds=float(soft_odds),
                sharp_odds=float(reference_odds),
                closing_sharp_odds=float(closing_odds),
                fair_probability=fair[market],
                won=market == winning_market,
            )
        )
    return selections


def build_selections(rows: Iterable[dict[str, str]], *, mode: Mode = "live") -> list[SelectionOutcome]:
    """CSV satirlarindan oynanabilir tum secimleri uretir (esik uygulanmaz).

    Soft taraf B365 acilis orani (Nesine vekili), keskin taraf kipe gore
    Pinnacle acilis veya kapanis oranidir.
    """
    selections: list[SelectionOutcome] = []

    for row in rows:
        league = str(row.get("Div", "")).strip()
        date = str(row.get("Date", "")).strip()
        home = str(row.get("HomeTeam", "")).strip()
        away = str(row.get("AwayTeam", "")).strip()
        if not home or not away:
            continue
        match_name = f"{home} - {away}"

        winner = _match_result_market(row)
        if winner is not None:
            selections.extend(
                _family_selections(
                    row,
                    league=league,
                    date=date,
                    match_name=match_name,
                    winning_market=winner,
                    soft_columns={
                        "MS1": ("B365H",),
                        "X": ("B365D",),
                        "MS2": ("B365A",),
                    },
                    sharp_columns={"MS1": ("PSH",), "X": ("PSD",), "MS2": ("PSA",)},
                    closing_columns={"MS1": ("PSCH",), "X": ("PSCD",), "MS2": ("PSCA",)},
                    mode=mode,
                )
            )

        totals_winner = _totals_market(row)
        over = totals_market_key("UST", _TOTALS_LINE)
        under = totals_market_key("ALT", _TOTALS_LINE)
        if totals_winner is not None and over is not None and under is not None:
            selections.extend(
                _family_selections(
                    row,
                    league=league,
                    date=date,
                    match_name=match_name,
                    winning_market=totals_winner,
                    soft_columns={over: ("B365>2.5",), under: ("B365<2.5",)},
                    sharp_columns={over: ("P>2.5",), under: ("P<2.5",)},
                    closing_columns={over: ("PC>2.5",), under: ("PC<2.5",)},
                    mode=mode,
                )
            )

    return selections


def parse_football_data_csv(text: str, *, mode: Mode = "live") -> list[SelectionOutcome]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    rows = [{str(key): str(value or "") for key, value in row.items() if key} for row in reader]
    return build_selections(rows, mode=mode)


def _summarize(selections: list[SelectionOutcome]) -> dict[str, Any]:
    bets = len(selections)
    if bets == 0:
        return {
            "bahis": 0,
            "kazanan": 0,
            "yatirilan": 0.0,
            "kar": 0.0,
            "roi": 0.0,
            "ort_ev": 0.0,
            "ort_clv": 0.0,
            "pozitif_clv_orani": 0.0,
        }
    staked = _STAKE * bets
    profit = sum(selection.profit for selection in selections)
    positive_clv = sum(1 for selection in selections if selection.clv > 0.0)
    return {
        "bahis": bets,
        "kazanan": sum(1 for selection in selections if selection.won),
        "yatirilan": round(staked, 2),
        "kar": round(profit, 2),
        "roi": round(profit / staked, 4),
        "ort_ev": round(sum(selection.ev for selection in selections) / bets, 4),
        "ort_clv": round(sum(selection.clv for selection in selections) / bets, 4),
        "pozitif_clv_orani": round(positive_clv / bets, 4),
    }


def _group_summary(
    selections: list[SelectionOutcome], key: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[SelectionOutcome]] = {}
    for selection in selections:
        grouped.setdefault(str(getattr(selection, key)), []).append(selection)
    return {name: _summarize(items) for name, items in sorted(grouped.items())}


def run_closing_backtest(
    selections: list[SelectionOutcome],
    *,
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
    breakdown_threshold: float | None = None,
    mode: Mode = "live",
) -> dict[str, Any]:
    """Esik bazli ozet + secilen esikte lig/pazar kirilimi uretir."""
    threshold_list = sorted({round(float(value), 4) for value in thresholds})
    per_threshold: dict[str, dict[str, Any]] = {}
    accepted_by_threshold: dict[float, list[SelectionOutcome]] = {}

    for threshold in threshold_list:
        accepted = [
            selection
            for selection in selections
            if threshold <= selection.ev <= _MAX_EV
        ]
        accepted_by_threshold[threshold] = accepted
        per_threshold[f"{threshold:.4f}"] = _summarize(accepted)

    focus = breakdown_threshold if breakdown_threshold is not None else (
        threshold_list[0] if threshold_list else 0.0
    )
    focus = round(float(focus), 4)
    focus_selections = accepted_by_threshold.get(focus, [])

    return {
        "kip": mode,
        "aday_secim": len(selections),
        "esikler": per_threshold,
        "kirilim_esigi": focus,
        "lig_kirilimi": _group_summary(focus_selections, "league"),
        "pazar_kirilimi": _group_summary(focus_selections, "market"),
        "not": (
            "clv kipi ileriye bakar (kapanis orani bahis aninda bilinmez); "
            "ROI vaat degil, ust sinir gostergesidir."
            if mode == "clv"
            else "live kipi bahis aninda gorulebilir bilgiyle calisir."
        ),
    }
