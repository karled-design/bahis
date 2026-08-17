#!/usr/bin/env bash
# Cift tiklayarak motoru baslatir (macOS). Terminal penceresi acik kalir.
cd "$(dirname "$0")" || exit 1
./tools/motor_ctl.sh baslat
echo
echo "Durdurmak icin: Motoru_Durdur.command"
echo "Bu pencereyi kapatabilirsiniz; motor arka planda calismaya devam eder."
