#!/bin/sh
# Read-only host diagnosis: does not alter UART, boot files or services.
set -eu
device="${AMIS_UART_DEVICE:-/dev/ttyAMA0}"
printf 'Architektur: '; uname -m
if [ -r /proc/device-tree/model ]; then
  printf 'Modell: '; tr -d '\000' </proc/device-tree/model; printf '\n'
fi
printf 'UART-Geraet: %s\n' "$device"
if [ ! -c "$device" ]; then
  printf 'FEHLT: UART ist nicht als Zeichengeraet vorhanden. README/UART-Einrichtung pruefen.\n'
  exit 1
fi
ls -l "$device"
resolved="$(readlink -f "$device")"
printf 'Aufgeloest: %s\n' "$resolved"
case "$resolved" in
  */ttyS*) printf 'Mini-UART erkannt: fuer AMIS wird Paritaet/PL011 benoetigt.\n'; exit 1 ;;
  */ttyAMA10) printf 'Pi-5-Debug-UART erkannt: fuer GPIO14/15 normalerweise ttyAMA0 verwenden.\n'; exit 1 ;;
esac
if [ -r /proc/cmdline ]; then
  for item in $(cat /proc/cmdline); do
    case "$item" in
      console=serial0,*|console=ttyAMA0,*|console=ttyS0,*)
        printf 'Serielle Konsole aktiv: %s (vor AMIS abschalten)\n' "$item"; exit 1 ;;
    esac
  done
fi
if command -v fuser >/dev/null 2>&1; then
  printf 'Prozesse am UART (nur PIDs):\n'
  fuser "$device" || true
fi
if command -v docker >/dev/null 2>&1; then
  docker compose version
  docker compose config --quiet
else
  printf 'Docker fehlt. Installationshinweis in README beachten.\n'
fi
printf 'Host-Pruefung abgeschlossen. Elektrische Pegel und Paritaet separat pruefen.\n'
