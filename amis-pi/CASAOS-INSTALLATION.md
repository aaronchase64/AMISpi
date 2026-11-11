# CasaOS-Installation auf RasPiDock

Installiert am 01.11.2026 auf Raspberry Pi 4 Model B Rev 1.2, ARM64.

- Weboberfläche: **http://192.168.1.213:8003**
- Projekt auf dem Pi: `/home/kristipi/amis-pi`
- Container: `amis-pi`, Image `amis-pi:local`, nativ auf dem Pi für ARM64 gebaut.
- `.env` wählt `docker-compose.casaos.yml`; normale `docker compose`-Befehle nutzen dieses Profil.
- Veröffentlicht wird nur `8003 -> 8080/tcp`.
- Anfangskonfiguration: Simulation an, MQTT aus, Fronius-Modbus aus. Kein Modbus-Port veröffentlicht.
- Konfiguration bleibt in `/home/kristipi/amis-pi/config/config.toml` erhalten.
- Keine vorhandenen CasaOS-Container oder Boot-Einstellungen verändert, kein Pi-Neustart durchgeführt.

## Betrieb

```bash
cd /home/kristipi/amis-pi
docker compose ps
docker compose logs --tail=80 -f
# Bei Bedarf:
docker compose restart
```

Die Weboberfläche zeigt zunächst Demodaten: 10 Wh Bezug, 11 Wh Einspeisung,
Saldo −1 W und eine feste Demo-Zählerzeit vom 18.12.2025.
Die Simulation lässt sich später in der Oberfläche abschalten; der echte AMIS-Schlüssel
muss dazu eingetragen sein. MQTT nach Wunsch gesondert konfigurieren/aktivieren.
Für den späteren Fronius-Betrieb muss zusätzlich Port 502 im CasaOS-Compose-Profil
veröffentlicht und die Modbus-Emulation aktiviert werden. Solange Demodaten laufen,
wird der Dienst nicht an einen produktiven Wechselrichter angebunden.

## UART / Bluetooth geprüft

- `/dev/serial0 -> /dev/ttyAMA0`
- `/boot/firmware/config.txt`, Abschnitt `[all]`: `enable_uart=1`, `dtoverlay=disable-bt`
- Kernel-Kommandozeile: nur `console=tty1`, keine serielle Konsole.
- Kein anderer Prozess belegte den UART.
- Echter Container-Aufruf `--check-uart` auf dem Pi meldete:
  **UART OK: /dev/ttyAMA0, 9600 baud, 8E1**.
- Bluetooth-Dienst war inaktiv und es gab kein Bluetooth-Gerät in `/sys/class/bluetooth/`.
  Der Bluetooth-Dienst steht noch auf `enabled`; die Hardware ist durch das Overlay
  abgeschaltet. `hciuart` ist nicht installiert/als Unit nicht vorhanden.

## Späterer Lesekopfanschluss

Die genannten **4, 6, 8, 10 sind physische Pin-Nummern** der 40-Pin-Leiste:

| Physischer Pi-Pin | Funktion | Lesekopf |
|---|---|---|
| 4 | +5 V | Versorgung, sofern der Lesekopf für 5 V ausgelegt ist |
| 6 | GND | GND |
| 8 | GPIO14 / TXD | RX / Dateneingang |
| 10 | GPIO15 / RXD | TX / Datenausgang |

Die UART-Datenpins vertragen nur **3,3 V**. Eine 5-V-Versorgung des Lesekopfs
bedeutet nicht automatisch 3,3-V-kompatible Daten; dessen Ausgangsschaltung
gegebenenfalls vor dem Anschluss prüfen bzw. passend pegelwandeln.
Der optische Austausch mit dem echten Zähler und der echte Fronius bleiben noch zu testen.

## Verifikation

HTTP 200 vom lokalen Windows-Rechner über die LAN-Adresse/Port 8003;
`/api/status` lieferte gültige Demodaten, Simulation aktiv, MQTT und Modbus deaktiviert.
Container-Healthcheck erfolgreich. Die Host-Konfiguration wurde nur gelesen;
der UART-Test hat lediglich das bereits aktivierte Gerät geöffnet/auf 8E1 konfiguriert.

Der Pi-Kernel meldet keine Unterstützung für Container-RAM-Limits. Ein zunächst
vorgesehenes Limit wurde deshalb aus diesem Compose-Profil entfernt; Boot-/cgroup-
Einstellungen wurden dafür nicht verändert.
