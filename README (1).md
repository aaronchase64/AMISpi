# AMIS Pi – GPIO-Lesekopf, Docker und Fronius

Die Raspberry-Pi-Variante liest Siemens TD-3511/TD-3512 (NetzOÖ AMIS) am
GPIO-UART und stellt die Werte über Weboberfläche, MQTT und Fronius-Modbus/TCP bereit.
Zielsystem: **Raspberry Pi OS Lite 64 Bit**, Python 3.11+ bzw. das beiliegende
Python-3.12-Docker-Image. Ein Desktop auf dem Pi ist nicht nötig.

## Was enthalten ist

- AMIS-M-Bus-Empfang mit 9600 Baud, **8E1**, AES-128-CBC-Entschlüsselung und ACK-Antworten.
- Weboberfläche: Live-Werte, Einstellungen, maskierte Zugangsdaten, Log und Dienstneustart.
- Fronius-Smartmeter-Emulation über TCP 502, SunSpec-Modell 213 wie im ESP-Projekt.
- MQTT 3.1.1 mit Paho, QoS 0/1, Wiederverbindung, TLS und Last Will.
- Automatische Wiederverbindung zum UART; nach 15 Sekunden ohne gültige Daten werden
  keine veralteten Modbus-Werte mehr ausgeliefert.
- Dauerhafte Konfiguration in `config/config.toml`; atomisches Speichern und Anwenden ohne Containerneustart.
- REST-Lesezugriff: `/api/status`, `/api/config` (Geheimnisse maskiert), `/api/log`.

Der Umfang ist ein AMIS-Leser mit MQTT und Fronius-Emulation. **Nicht portiert** sind
Shelly-Emulation, ThingSpeak, Home-Assistant-Discovery, die ESP-Wochen-/Monatshistorie
und die automatische Seriennummernabfrage bei 300 Baud. Die Seriennummer kann manuell
für MQTT eingetragen werden. Der AMIS-Suchmodus bei 9600 Baud funktioniert unabhängig
davon; der Pi muss sowohl empfangen als auch ACK senden können.

## 1. IR-Lesekopf anschließen

| Anschluss am Lesekopf | Raspberry Pi, 40-Pin-Leiste |
|---|---|
| TX / Datenausgang | **GPIO15 / RXD**, physischer **Pin 10** |
| RX / Dateneingang | **GPIO14 / TXD**, physischer **Pin 8** |
| GND | GND, z. B. physischer **Pin 6** |
| Versorgung | Nach Spezifikation des Lesekopfs |

Dein bisher mit **5 V** versorgter Lesekopf kann weiterhin 5 V benötigen; dann ist
beispielsweise Pin 2 oder 4 die 5-V-Versorgung. Das sagt nichts über seine Datenpegel:
**Der Raspberry-Pi-RX-Eingang darf keine 5 V bekommen.** Für einen 5-V-TX-Ausgang ist
vor GPIO15 eine passende Pegelanpassung nötig. Versorgung, Ausgangsschaltung und
Datenpegel des konkreten Lesekopfs müssen vor dem Anschluss geklärt sein.

## 2. GPIO-UART auf dem Host einrichten

Diese Änderungen erfolgen auf Raspberry Pi OS, **nicht im Container**.
AMIS benötigt gerade Parität. Die Mini-UART `/dev/ttyS0` eignet sich dafür nicht;
benötigt wird eine PL011-UART, normalerweise **`/dev/ttyAMA0`**.

1. Die Boot-Konfiguration zunächst sichern:

   ```bash
   sudo cp -a /boot/firmware/config.txt /boot/firmware/config.txt.vor-amis
   sudo cp -a /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.vor-amis
   ```

2. Bei **Pi 3 / Pi 4 / Zero 2 W** in `/boot/firmware/config.txt` im passenden
   Abschnitt (z. B. `[all]`) ergänzen:

   ```ini
   enable_uart=1
   dtoverlay=disable-bt
   ```

   Damit wird Bluetooth zugunsten der PL011 auf GPIO14/15 abgeschaltet:

   ```bash
   sudo systemctl disable --now hciuart
   ```

   Bei **Pi 5** stattdessen:

   ```ini
   dtoverlay=uart0-pi5
   ```

   Auf dem Pi 5 kann `/dev/serial0` auf den separaten Debug-Anschluss (`ttyAMA10`)
   zeigen. Deshalb für die 40-Pin-Leiste ausdrücklich `ttyAMA0` prüfen und verwenden.

3. `sudo raspi-config` → Interface Options → Serial Port:
   serielle Login-Shell **Nein**, serielle Hardware **Ja**.
   In `/boot/firmware/cmdline.txt` darf kein Konsoleneintrag für `serial0`, `ttyAMA0`
   oder `ttyS0` bleiben. Nur den betreffenden `console=...`-Eintrag entfernen;
   alle übrigen Einträge und das einzeilige Format erhalten.
4. Pi neu starten und im Projektordner prüfen:

   ```bash
   sh scripts/check-host.sh
   ```

Kein anderer Prozess darf denselben UART benutzen. Das Prüfskript verändert keine
Host-Einstellungen. Andere Pi-Modelle bzw. zusätzliche UART-Overlays erst anhand
der konkreten Pinbelegung einrichten.

Offizielle Grundlagen: [Raspberry-Pi-UART-Dokumentation](https://www.raspberrypi.com/documentation/computers/configuration.html#configuring-uarts)
und [UART-Overlays](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README).

## 3. Mit Docker starten

Voraussetzung: Docker Engine und `docker compose` auf dem Pi. Die offizielle
[Docker-Installationsanleitung für Debian](https://docs.docker.com/engine/install/debian/)
enthält die Einrichtung; Raspberry Pi OS Lite **64 Bit** verwendet ARM64.

Den vollständigen Ordner `amis-pi` auf den Pi kopieren, dann dort:

```bash
cd amis-pi
docker compose config --quiet
docker compose up -d --build
docker compose logs --tail=80 -f
```

Im Browser `http://<IP-des-Pi>:8080` öffnen. Der Start ohne Konfigurationsdatei und
ohne AMIS-Schlüssel ist vorgesehen: Die Weboberfläche bleibt erreichbar und zeigt
an, dass der Schlüssel fehlt. Den Schlüssel eintragen, ggf. MQTT konfigurieren,
**Speichern & anwenden** drücken. Bis gültige Daten eintreffen, kann die
Synchronisation mit dem Zähler etwa 1–2 Minuten dauern.

- MQTT ist zunächst **ausgeschaltet**. Erst nach Eintragen des gewünschten Brokers aktivieren.
- `config.example.toml` ist eine Vorlage; sie muss nicht kopiert werden.
- Ein Web-Passwort kann in der Oberfläche gesetzt werden. Ohne Passwort ist die
  Oberfläche im erreichbaren Netz frei bedienbar. Basic Auth über HTTP verschlüsselt
  die Übertragung nicht; keine direkte Veröffentlichung ins Internet.
- Konfiguration und Schlüssel bleiben in `./config/` erhalten, auch bei Image-Neubau.
  Diesen Ordner sichern und nicht öffentlich teilen.
- Der Container bekommt nur das eine serielle Gerät; **kein `privileged: true`**
  und kein `/dev/gpiomem` nötig. Er läuft für UART und Port 502 als root im Container.
- Normaler Docker-Bridge-Modus genügt für Modbus/TCP. Kein Host-Netzwerk notwendig.
- Für einen MQTT-Broker auf demselben Pi dessen **LAN-IP** eintragen;
  `localhost` bezeichnet im Container den Container selbst.
- Für einen USB-TTL-Adapter z. B. `.env` mit `AMIS_UART_DEVICE=/dev/ttyUSB0` anlegen.
  Im Container bleibt die Schnittstelle `/dev/ttyAMA0` (festes Mapping).
- Interne Web-/Modbus-Ports und Bind-Adressen normalerweise unverändert lassen.
  Änderungen erfordern passende Compose-Portfreigaben; Web-Bindung/Port/Aktivierung
  wirken erst nach Containerneustart.
- `AMIS_<ABSCHNITT>_<OPTION>`-Umgebungsvariablen können die Datei überschreiben.
  Zusammen mit der Web-Konfiguration vermeiden, sonst überschreiben sie beim Start
  erneut die gespeicherten Werte.

UART ohne Schlüssel prüfen (laufenden Leser vorher stoppen):

```bash
docker compose stop amis
docker compose run --rm --no-deps amis --check-uart
docker compose up -d
```

Ein einzelnes echtes Telegramm lesen (auch hier Leser vorher stoppen):

```bash
docker compose stop amis
docker compose run --rm --no-deps amis -c /config/config.toml --once --timeout 150
docker compose up -d
```

## 4. Fronius verbinden

Dem Pi eine feste DHCP-Zuordnung geben. Am Fronius die vorhandene Einstellung zur
Fronius-Smartmeter-Emulation verwenden, IP-Adresse auf den Pi umstellen,
**TCP-Port 502**, Zähleradresse **1**. Die Ports 502 und 8080 müssen auf dem Pi frei
und aus dem gewünschten LAN erreichbar sein. Der Emulator akzeptiert wie das
ESP-Original auch andere Unit-IDs und gibt die angefragte ID zurück.

Registeradressen und Byte-Reihenfolge stammen aus dem ESP-Original:
Leistung am Wire-Index **40097** (Registernummer 40098), Exportenergie am Index
**40129**, Importenergie am Index **40137**. Registerwerte sind Float32,
Big Endian, High Word zuerst. Bei ungültigen/veralteten Daten schließt der Server
die Verbindung, statt 0 W vorzutäuschen. Spannungen/Frequenz/Phasenaufteilung
sind wie im ESP-Projekt **simulierte Ersatzwerte**, keine echten Phasenmessungen.
Saldo-Offset und Invertierung betreffen MQTT/Webanzeige, nicht die Fronius-Register.

## 5. MQTT und HTTP

- `amis/out`: JSON mit OBIS-Werten, Saldo, Zählerzeit und optionaler Seriennummer.
- `key_style = "underscore"` liefert z. B. `1_8_0` statt `1.8.0`.
- `amis/status`: retained `online`/`offline` beschreibt die MQTT-Dienstverbindung,
  nicht die Aktualität des Zählers. Die Zählerzeit im Payload bzw. HTTP-Status prüfen.
- QoS 1 wird durch Paho bestätigt und bei Verbindungsabbruch erneut übertragen.
  Es gibt keine dauerhafte MQTT-Warteschlange über einen Prozessneustart hinweg.
- `/api/status`: Live-Werte, Aktualität, UART-Fehler, MQTT-/Modbus-Zustand.
- `/api/config`: Einstellungen mit maskierten Geheimnissen.
- `/api/log`: die letzten 300 Logzeilen im RAM.
- `/healthz`: nur `{ready: true/false}`, HTTP 200 mit frischen Daten, sonst 503.
  Ohne Schlüssel/Lesekopf wird der Container nach der Anlaufphase `unhealthy`, die
  Weboberfläche bleibt zur Einrichtung verfügbar. Docker startet einen Container
  allein wegen `unhealthy` nicht automatisch neu.

## 6. Ohne Lesekopf lokal ausprobieren

Eigenständige Compose-Datei mit eigenem Konfigurationsordner und ohne GPIO-Gerät:

```bash
docker compose -f docker-compose.simulation.yml up -d --build
```

Auf dem ausführenden Rechner `http://127.0.0.1:8081` öffnen. Modbus liegt nur lokal
auf `127.0.0.1:1502`. Demo: 1.8.0 = 10 Wh, 2.8.0 = 11 Wh, Saldo = −1 W;
Demo-Zählerzeit 18.12.2025. Der Simulationsmodus ist deutlich sichtbar.
Für einen entfernten Pi bei Bedarf einen SSH-Tunnel verwenden; keine simulierten
Werte an einen produktiven Wechselrichter senden.

```bash
docker compose -f docker-compose.simulation.yml down
```

## 7. Entwicklung und Tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python -m unittest discover -s tests -v
```

Die UART-/PTY-Tests brauchen Linux. Unter Windows werden nur diese Tests übersprungen.
Container-Tests (inklusive UART-Pseudoterminals und AES-Vergleich):

```bash
docker build --target test -t amis-pi:test .
docker run --rm --entrypoint python amis-pi:test -m unittest discover -s tests -v
```

Der normale `docker build` bzw. Compose verwendet die letzte Stufe `production`,
die keine Testabhängigkeiten enthält. Das Basisimage unterstützt ARM64; ohne native
Pakete ist die Anwendung für den Pi lokal baubar. Ein tatsächlich auf dem Pi gebautes
Image und der elektrische IR-Anschluss sind erst am Gerät verifizierbar.

## 8. Alternative: systemd ohne Docker

Die Datei `amis-pi.service` ist für eine Installation in `/opt/amis-pi` mit einer
Python-venv unter `/opt/amis-pi/.venv` vorbereitet. Paket mit `pip install .` in diese
venv installieren, Systembenutzer `amis` mit Gruppe `dialout` anlegen und
`/etc/amis-pi` diesem Benutzer schreibbar machen. Nicht gleichzeitig mit dem
Docker-Leser auf denselben UART starten. Für Port 502 nutzt die Unit ausschließlich
`CAP_NET_BIND_SERVICE`; sie läuft nicht als root.

## Stand und Lizenz

Die Software wurde lokal überarbeitet. Siehe `PRUEFBERICHT.md` für die tatsächlich
getesteten Pfade und Grenzen. Der echte Raspberry Pi wurde dabei nicht per SSH
kontaktiert und die Hardware nicht verändert.

GPL-2.0-or-later; abgeleitet von
[mgerhard74/amis_smartmeter_reader](https://github.com/mgerhard74/amis_smartmeter_reader)
(Protokoll, Registerbelegung, Demo-Daten). Siehe `LICENSE`.
