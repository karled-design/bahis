"""football-data.co.uk kapanis backtest'i testleri (ag erisimi yok)."""

from __future__ import annotations

import unittest

from core.closing_backtest import (
    parse_football_data_csv,
    run_closing_backtest,
)

_HEADER = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,PSCH,PSCD,PSCA,"
    "B365>2.5,B365<2.5,P>2.5,P<2.5,PC>2.5,PC<2.5"
)
# Ev sahibi kazandi, 3 gol. B365 ev sahibine 2.40 verirken Pinnacle 2.10 diyor
# (soft yuksek = aday), kapanista 2.00'a dusuyor (pozitif CLV).
_ROW_HOME_WIN = (
    "T1,10/08/2024,Fenerbahce,Galatasaray,2,1,H,"
    "2.40,3.40,3.10,2.10,3.50,3.60,2.00,3.55,3.80,"
    "1.90,1.95,1.85,2.00,1.80,2.05"
)


def _csv(*rows: str) -> str:
    return "\n".join((_HEADER, *rows)) + "\n"


class ParseTests(unittest.TestCase):
    def test_row_yields_match_result_and_totals_selections(self) -> None:
        selections = parse_football_data_csv(_csv(_ROW_HOME_WIN))

        markets = sorted(selection.market for selection in selections)
        self.assertEqual(markets, ["ALT 2.5", "MS1", "MS2", "UST 2.5", "X"])

        winners = {selection.market for selection in selections if selection.won}
        self.assertEqual(winners, {"MS1", "UST 2.5"})

    def test_soft_price_and_clv_use_expected_columns(self) -> None:
        selections = {s.market: s for s in parse_football_data_csv(_csv(_ROW_HOME_WIN))}
        home = selections["MS1"]

        self.assertEqual(home.soft_odds, 2.40)  # B365 acilis
        self.assertEqual(home.sharp_odds, 2.10)  # live kipi: Pinnacle acilis
        self.assertEqual(home.closing_sharp_odds, 2.00)
        self.assertAlmostEqual(home.clv, 0.20, places=6)
        self.assertGreater(home.ev, 0.0)
        self.assertEqual(home.profit, 140.0)

    def test_clv_mode_references_closing_price(self) -> None:
        selections = {
            s.market: s for s in parse_football_data_csv(_csv(_ROW_HOME_WIN), mode="clv")
        }
        self.assertEqual(selections["MS1"].sharp_odds, 2.00)

    def test_missing_pinnacle_columns_drop_the_family(self) -> None:
        broken = _ROW_HOME_WIN.replace(",2.10,3.50,3.60,", ",,,,")
        selections = parse_football_data_csv(_csv(broken))

        self.assertEqual(
            sorted(selection.market for selection in selections), ["ALT 2.5", "UST 2.5"]
        )


class ReportTests(unittest.TestCase):
    def test_threshold_filters_and_breakdowns(self) -> None:
        selections = parse_football_data_csv(_csv(_ROW_HOME_WIN))
        report = run_closing_backtest(
            selections, thresholds=(0.02, 0.90), breakdown_threshold=0.02
        )

        low = report["esikler"]["0.0200"]
        high = report["esikler"]["0.9000"]
        self.assertGreater(low["bahis"], 0)
        self.assertEqual(high["bahis"], 0)
        self.assertEqual(high["roi"], 0.0)
        self.assertIn("T1", report["lig_kirilimi"])
        self.assertEqual(
            low["bahis"],
            sum(summary["bahis"] for summary in report["pazar_kirilimi"].values()),
        )

    def test_absurd_ev_selections_are_rejected(self) -> None:
        # Soft 6.00'a karsi keskin 2.10: %100+ EV = eslesme/veri hatasi.
        absurd = _ROW_HOME_WIN.replace("2.40,3.40,3.10", "6.00,3.40,3.10")
        report = run_closing_backtest(
            parse_football_data_csv(_csv(absurd)), thresholds=(0.01,)
        )
        played = {
            market
            for market, summary in report["pazar_kirilimi"].items()
            if summary["bahis"]
        }
        self.assertNotIn("MS1", played)

    def test_empty_selection_set_reports_zero(self) -> None:
        report = run_closing_backtest([], thresholds=(0.022,))
        self.assertEqual(report["aday_secim"], 0)
        self.assertEqual(report["esikler"]["0.0220"]["bahis"], 0)


if __name__ == "__main__":
    unittest.main()
