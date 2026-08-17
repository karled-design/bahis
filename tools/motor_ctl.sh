#!/usr/bin/env bash
# SQE-V1 motor kontrolu: baslat / durdur / durum.
# macOS'taki Motoru_Baslat.command ve Motoru_Durdur.command bu betigi cagirir.
set -euo pipefail

PROJE_DIZINI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DOSYASI="${PROJE_DIZINI}/database/motor.pid"
LOG_DOSYASI="${PROJE_DIZINI}/database/motor.log"
PANEL_URL="http://127.0.0.1:${PANEL_PORT:-8765}"
KAPANIS_BEKLEME_SANIYE="${MOTOR_STOP_TIMEOUT:-20}"

python_bul() {
  if [ -x "${PROJE_DIZINI}/.venv/bin/python" ]; then
    echo "${PROJE_DIZINI}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    echo ""
  fi
}

calisan_pid() {
  # Yalnizca gercekten yasayan ve bu projeye ait sureci dondurur (bayat PID temizlenir).
  [ -f "${PID_DOSYASI}" ] || return 1
  local pid
  pid="$(cat "${PID_DOSYASI}" 2>/dev/null || true)"
  case "${pid}" in
    ''|*[!0-9]*) rm -f "${PID_DOSYASI}"; return 1 ;;
  esac
  if ! kill -0 "${pid}" 2>/dev/null; then
    rm -f "${PID_DOSYASI}"
    return 1
  fi
  if ! ps -p "${pid}" -o args= 2>/dev/null | grep -q "bahis/main.py"; then
    # PID baska bir surece devredilmis: dosya bayat.
    rm -f "${PID_DOSYASI}"
    return 1
  fi
  echo "${pid}"
}

baslat() {
  local mevcut
  if mevcut="$(calisan_pid)"; then
    echo "[SQE-V1] Motor zaten calisiyor | PID=${mevcut} | Panel: ${PANEL_URL}"
    return 0
  fi

  local python_yolu
  python_yolu="$(python_bul)"
  if [ -z "${python_yolu}" ]; then
    echo "[SQE-V1] HATA: Python bulunamadi. Once sanal ortami kurun: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    return 1
  fi

  mkdir -p "$(dirname "${PID_DOSYASI}")"
  cd "${PROJE_DIZINI}"
  # .env varsa yuklenir; anahtarlar ekrana yazilmaz.
  if [ -f "${PROJE_DIZINI}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${PROJE_DIZINI}/.env"
    set +a
  fi

  PYTHONPATH="${PROJE_DIZINI}" nohup "${python_yolu}" -u bahis/main.py >>"${LOG_DOSYASI}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${PID_DOSYASI}"

  # Surec ilk saniyelerde konfigurasyon hatasiyla dusebilir: sessizce "basladi" demeyelim.
  sleep 3
  if ! kill -0 "${pid}" 2>/dev/null; then
    rm -f "${PID_DOSYASI}"
    echo "[SQE-V1] HATA: Motor baslatilamadi. Son satirlar:" >&2
    tail -n 15 "${LOG_DOSYASI}" >&2 || true
    return 1
  fi

  echo "[SQE-V1] Motor basladi | PID=${pid} | Panel: ${PANEL_URL}"
  echo "[SQE-V1] Log: ${LOG_DOSYASI}"
  if command -v open >/dev/null 2>&1; then
    open "${PANEL_URL}" >/dev/null 2>&1 || true
  fi
}

durdur() {
  local pid
  if ! pid="$(calisan_pid)"; then
    echo "[SQE-V1] Motor zaten kapali."
    return 0
  fi

  # SIGTERM -> main.py guvenli kapanisa gecer (settler durur, panel durumu sifirlanir).
  kill -TERM "${pid}" 2>/dev/null || true
  local bekleme=0
  while kill -0 "${pid}" 2>/dev/null; do
    if [ "${bekleme}" -ge "${KAPANIS_BEKLEME_SANIYE}" ]; then
      echo "[SQE-V1] Motor ${KAPANIS_BEKLEME_SANIYE} sn icinde kapanmadi; zorla sonlandiriliyor."
      kill -KILL "${pid}" 2>/dev/null || true
      break
    fi
    sleep 1
    bekleme=$((bekleme + 1))
  done

  rm -f "${PID_DOSYASI}"
  echo "[SQE-V1] Motor durduruldu | PID=${pid}"
}

durum() {
  local pid
  if pid="$(calisan_pid)"; then
    echo "[SQE-V1] Durum: CALISIYOR | PID=${pid} | Panel: ${PANEL_URL}"
    return 0
  fi
  echo "[SQE-V1] Durum: KAPALI"
  return 1
}

case "${1:-durum}" in
  baslat|start) baslat ;;
  durdur|stop) durdur ;;
  durum|status) durum ;;
  yeniden|restart) durdur; baslat ;;
  *)
    echo "Kullanim: $(basename "$0") {baslat|durdur|durum|yeniden}" >&2
    exit 2
    ;;
esac
