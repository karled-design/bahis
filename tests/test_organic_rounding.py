from __future__ import annotations

import unittest
from unittest.mock import patch

from core.clv_engine import _round_to_organic, calculate_stake_amount


class OrganicRoundingTest(unittest.TestCase):
    def test_kullanici_ornekleri_en_yakin_10(self):
        # Kullanicinin sectigi ornekler (tavan bol oldugunda):
        self.assertEqual(_round_to_organic(47.42, 1000.0), 50.0)
        self.assertEqual(_round_to_organic(128.90, 1000.0), 130.0)
        self.assertEqual(_round_to_organic(23.10, 1000.0), 20.0)

    def test_sifir_sifir_kalir(self):
        # Bahis yok -> 0 kalir.
        self.assertEqual(_round_to_organic(0.0, 200.0), 0.0)

    def test_gecersiz_ve_negatif_sifira_duser(self):
        self.assertEqual(_round_to_organic(-5.0, 200.0), 0.0)
        self.assertEqual(_round_to_organic(float("nan"), 200.0), 0.0)

    def test_tavani_asmaz_asagi_iner(self):
        # round(4.7)*10 = 50, tavan 45 -> asagi 40'a iner (tavani ASMAZ).
        rounded = _round_to_organic(47.0, 45.0)
        self.assertEqual(rounded, 40.0)
        self.assertLessEqual(rounded, 45.0)

    def test_pozitif_bahis_sifira_dusmez(self):
        # round(0.4)*10 = 0 ama pozitif bahis -> en az bir adim (10 TL).
        self.assertEqual(_round_to_organic(4.0, 200.0), 10.0)

    def test_cok_kucuk_kasa_ham_korunur(self):
        # Tavan bir adimdan kucukse (cap < 10) ham tutar korunur.
        self.assertEqual(_round_to_organic(3.0, 4.0), 3.0)

    def test_calculate_stake_amount_10un_kati_ve_tavan_alti(self):
        with patch(
            "core.clv_engine.get_operator_risk_per_trade", return_value=0.02
        ), patch(
            "core.clv_engine.get_active_min_ev_threshold", return_value=0.05
        ):
            stake = calculate_stake_amount(5000.0, 0.061, soft_odds=2.0)
        self.assertEqual(stake % 10, 0.0)  # 10'un kati (organik)
        self.assertLessEqual(stake, 2 * 0.02 * 5000.0)  # profil tavani (200) altinda
        self.assertGreater(stake, 0.0)

    def test_ev_esik_altinda_sifir(self):
        with patch(
            "core.clv_engine.get_operator_risk_per_trade", return_value=0.02
        ), patch(
            "core.clv_engine.get_active_min_ev_threshold", return_value=0.05
        ):
            stake = calculate_stake_amount(5000.0, 0.01, soft_odds=2.0)
        self.assertEqual(stake, 0.0)


if __name__ == "__main__":
    unittest.main()
