# Prüfbericht AMIS Pi – 01.11.2026

## Ergebnis

Lokal überarbeitete und getestete Raspberry-Pi-/Docker-Variante unter `amis-pi`.
Der Raspberry Pi wurde nicht per SSH kontaktiert, seine Konfiguration blieb unverändert.
Die bestehende ESP32-Variante wurde nicht bearbeitet.

## Ausgangsstand von Claude

Vorhanden waren AES-/AMIS-Dekoder, ein termios-UART-Leser, ein eigener MQTT-Client,
Fronius-Modbus/TCP, Weboberfläche, Compose-Datei und 27 Tests. Die meisten
Normalpfade funktionierten schon. Auf Windows war der UART-Test nicht ausführbar.
Der Ausgangsstand ist als `../amis-pi-vor-ueberarbeitung-2026-11-01.zip` gesichert.

## Behobene Probleme

- Der eigene MQTT-Client wertete QoS-1-Bestätigungen nicht aus und hatte keine
  Wiederübertragung nicht bestätigter Nachrichten. Ersatz durch Paho MQTT 2.1.0,
  mit begrenzter Warteschlange, TLS, Last Will, sauberem Stop und Wiederverbindung.
- Bei fehlgeschlagener UART-Konfiguration blieb ein geöffneter Dateideskriptor zurück.
  Fehler werden nun aufgeräumt und als Status angezeigt; ein abgezogenes Gerät löst
  Wiederverbindungsversuche aus. Der UART wird gegen doppelte AMIS-Nutzung gesperrt.
- Das Starten von Diensten konnte vor dem Cleanup-Block scheitern. Das ist jetzt
  abgesichert; Reader-Threads werden beim Stop beendet und abgewartet.
- Der Modbus-Server hatte bereits im Konstruktor den Port gebunden. Er bindet jetzt
  beim Start, schließt beim Stop bestehende Verbindungen und kann neu gestartet werden.
- Gültigkeitsprüfung und Registerkopie konnten beim gleichzeitigen Invalidieren
  auseinanderlaufen. Eine atomare Momentaufnahme verhindert ungewollte Nullwerte.
- Gleichzeitiger Datenempfang und Timeout dürfen frische Daten nicht gegenseitig
  überschreiben; das ist jetzt über denselben Lock synchronisiert.
- Konfigurationsänderungen und parallele Web-Speicherzugriffe sind serialisiert.
  Speicherung erfolgt in einer temporären Datei, fsync und atomarem Rename; Fehler
  erhalten die alte Datei und werden mit HTTP 500 gemeldet.
- Booleans, Zahlentypen, MQTT-Topics und JSON-Struktur werden geprüft. Der Text
  `false` wird nicht mehr als wahr interpretiert. TOML unterstützt nun auch korrekt
  maskierte Steuerzeichen und Unicode. Geheimnisse bleiben bei maskierten POSTs erhalten.
- Log-Snapshots sind synchronisiert. Fehlende Schlüssel und UART-Probleme erscheinen
  in der UI. Ein kleiner Health-Endpunkt meldet die Aktualität der Daten.
- Simulation verwendet immer den Demo-Schlüssel, ohne einen gespeicherten echten
  Schlüssel zu verändern. Eigene Compose-Datei ohne GPIO und mit eigener Konfiguration.
- GPIO-Dokumentation korrigiert: PL011/8E1, Pi-5-Zuordnung und 5-V-Versorgung
  getrennt von 3,3-V-Datenpegeln. Kein privilegierter GPIO-Container erforderlich.

## Tatsächlich ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| Docker-Testimage bauen | erfolgreich |
| Produktionsimage bauen | erfolgreich |
| Python 3.12 im Linux-Container: 42 Tests | alle bestanden |
| Python 3.11 unter Windows: 42 Tests | bestanden, 3 Linux-UART-Tests übersprungen |
| Linux-UART über Pseudoterminal | Empfang, ACKs, Abbruch, Fehler-Cleanup geprüft |
| AES | FIPS-197 und 50 Zufallsvektoren gegen cryptography |
| Originalvergleich | alle 71 statischen Modbus-Register identisch; AMIS-Demodaten (106 Byte) identisch |
| Fronius-Modbus | Float32-Werte, Blockabfragen, mehrere Anfragen pro TCP-Segment, ungültige Anfragen, Stale-Close, Verbindungsende beim Stop |
| MQTT | QoS 0/1, Last Will, Ablehnung, Wiederverbindung und Wiederübertragung nach verlorener Bestätigung |
| Web | Authentifizierung, Maskierung, Eingabeprüfung, Speichern, Dateifehler, parallele Änderungen, wiederholte Neustarts |
| Beide Compose-Dateien | `config --quiet` erfolgreich |
| Host-Prüfskript | Shell-Syntax geprüft; keine Host-Veränderungen |
| Python-Basisimage | Manifest enthält linux/arm64/v8 (und weitere Architekturen) |

## Ende-zu-Ende-Test des Produktionscontainers

Lokaler Docker-Daemon in WSL2, Linux/AMD64, Python 3.12.
Echter Mosquitto-2-Broker auf lokalem Port 18884, Demotelegramme im AMIS-Container.
HTTP und Modbus ausschließlich auf lokalen Testports, keine Verbindung zum echten Fronius.
Der Container lief mit schreibgeschütztem Root-Dateisystem, `/tmp` als tmpfs und
schreibbarem Konfigurationsverzeichnis.

1. HTTP `/api/status` lieferte 1.8.0 = 10 Wh; `/healthz` meldete bereit.
2. Modbus/TCP lieferte Saldo −1 W als Float32 an Wire-Adresse 40097.
3. Mosquitto lieferte die JSON-Nachricht mit QoS 1 an einen separaten Subscriber.
4. Topic über HTTP geändert; Änderung landete in TOML und Nachrichten kamen am neuen Topic an.
5. Container neu gestartet; geändertes Topic blieb gespeichert und Healthcheck war erfolgreich.
6. SIGTERM/Container-Stop endete sauber mit Exit-Code 0.

Logs liegen lokal neben diesem Bericht (`tests-docker.log`, `tests-windows.log`,
`docker-integration.log`, `docker-integration-container.log` und Build-Logs).
Die temporären Testcontainer und der lokale Test-Daemon wurden nach Abschluss beendet.

## Noch am Gerät zu verifizieren

- Nativer ARM64-Image-Build auf Raspberry Pi OS Lite; lokal wurde AMD64 ausgeführt.
- Konkretes Pi-Modell, aktiver PL011 auf GPIO14/15 und ausgeschaltete serielle Konsole.
- Elektrische Datenpegel des vorhandenen IR-Kopfs, Verkabelung und reale Zählersynchronisation.
- Erreichbarkeit über das gewünschte LAN, echter AMIS-Schlüssel und echter Fronius.
- Langzeitbetrieb. Die lokalen Tests ersetzen keinen Test mit dem Wechselrichter.

Nicht enthalten bleiben Shelly, ThingSpeak, ESP-Historie, Home-Assistant-Discovery und
Seriennummer-Handshake. Die vorhandene HTTP-API ist nicht dieselbe Route-/JSON-API wie
im ESP-Webserver; `/api/status` ist in der README beschrieben.

## Nachvollziehbarkeit

Quellbasis: https://github.com/mgerhard74/amis_smartmeter_reader
MQTT-Bibliothek: https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
Raspberry-Pi-UART: https://www.raspberrypi.com/documentation/computers/configuration.html#configuring-uarts
UART-Overlays: https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README
