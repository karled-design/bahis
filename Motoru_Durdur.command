#!/usr/bin/env bash
# Cift tiklayarak motoru guvenli sekilde durdurur (macOS).
cd "$(dirname "$0")" || exit 1
./tools/motor_ctl.sh durdur
