# Änderungsprotokoll

Vor jedem Update empfiehlt sich ein Backup von Home Assistant
(beim Aktualisieren den Haken „Backup erstellen“ gesetzt lassen).

## 4.1.0

- **Namen mit Kürzel vorne**, damit man immer weiß, was gemeint ist:
  `Steckdose 5 · Heizer`, `pH · Becken`, `Dosierer 4 · Kalium`, `Licht 1 · Weiß` …
  (Entitäts-IDs, Verlauf und Einstellungen bleiben gleich).
- **Steckdosenleisten**: Aufteilung auf der Einstellungsseite eintragen, z. B.
  `Alt=4 (ohne Strom), PAB=6` → Namen `Steckdose 2.1 · …`, im Dashboard je Leiste
  ein Block (Vorgabe auch per Option `steckdosenleisten`). Leisten „ohne Strom“ bekommen keinen (sinnlosen) Strom-Alarm mehr.
- **Klarer Wortlaut** der Einstellungen („Alarm, wenn AN und Strom unter“, „Alarm erst
  nach (Dauer)“ …) und je Teil der Satz **„So wird überwacht“** im Dashboard;
  Übersicht „So wird überwacht“ mit allen überwachten Punkten. Zahlen mit Komma.
- **Neu ausgelesen:** Licht (Helligkeit je Kanal), Füllstand der Dosierer auch in %
  (Behältergröße aus dem ProfiLux), ION-Director (5 Werte).
- **Bereiche in HA schaltbar** (Einstellungsseite → Bereiche), z. B. Licht aus.
  Abgeschaltete Teile verschwinden jetzt auch aus HA.
- Ab ProfiLux-Firmware 7.52: Alarm bei **Sensorfehler** und **KH-Director Leck-/
  Messbereichsalarm** (aus den Statusbits des Messprotokolls).
- Größere Anlagen: bis 64 Steckdosen, 32 Sensoren, 32 Dosierer, 16 Niveausensoren
  (Grenzen laut GHL-API-Referenz); leere Plätze am Ende werden nicht angelegt.
- Dosierer und KH-Messung haben keine (unnötige) Verzögerung mehr.

Hinweis: Wer den Blueprint „Telegram-Befehle“ nutzt, bitte **neu importieren**
(angepasst an die neuen Namen).

## Blueprint Telegram-Befehle (ohne Add-on-Update)

- Befehle heißen jetzt `/aq`, `/aq_werte`, `/aq_steckdosen`, `/aq_dosierer`,
  `/aq_hilfe` (antippbar, keine Überschneidung mit anderen Automationen).
  Wer den Blueprint schon nutzt: unter *Blueprints* → ⋮ → **Blueprint neu
  importieren**, danach im BotFather-Menü die neuen Befehle eintragen.

## 4.0.4

- Das Add-on wird jetzt fertig gebaut von GitHub geladen (ghcr.io) statt auf dem
  eigenen Rechner gebaut – Installation und Updates gehen deutlich schneller.
  Keine Änderung an Funktionen oder Einstellungen.

## 4.0.3

- Externer MQTT-Broker: eigene Optionen `mqtt_host`, `mqtt_port`, `mqtt_user`,
  `mqtt_password` (im Formular unter „Nicht verwendete optionale
  Konfigurationsoptionen anzeigen“). Ohne Angabe wird wie bisher automatisch das
  Mosquitto-Add-on benutzt.

## 4.0.2

- Dashboard: Verlaufsdiagramme für alle Messwerte, je Messgröße ein Diagramm
  (beide pH-Sonden bzw. Temperaturfühler zusammen); KH über 7 Tage,
  Füllstände der Dosierbehälter über 14 Tage.
- Die Auswahl „Art“ eines Sensors zeigt jetzt auch die automatisch geschätzte Art
  an (vorher „unbekannt“).

## 4.0.1

- Add-on heißt jetzt „Aquarium-Wächter“.
- Dashboard: kürzere Namen bei Steckdosen, Dosierern und Niveau
  (ohne Gerätenamen und „Füllstand“).

## 4.0.0

Erste öffentliche Version.

- Beliebig viele ProfiLux, parallel abgefragt; Konfiguration in der Add-on-Oberfläche.
- Automatische Erkennung von Steckdosen, Sensoren, Niveau, Durchfluss, Dosierern
  und KH-Director; Bereiche je Gerät abschaltbar, einzelne Teile in HA mit
  „… abfragen“.
- Überwachung je Teil in HA einstellbar (Minimum/Maximum, Verzögerung, Alarm bei 0/1,
  Warnen unter … ml, KH-Messung älter als … h – standardmäßig an, 7 h).
- Telegram-Alarme direkt aus dem Add-on, Meldungen mit Gerätenamen, Erinnerungen,
  „Wieder OK“, Hinweis bei Umbenennung in GHL Connect („Belegung geändert“).
- Automatisches Dashboard mit Schutz vor dem Überschreiben von Handänderungen.
- Knöpfe: Dashboard neu erzeugen, Telegram-Test, Geräte neu einlesen.
- Blueprints: Telegram-Befehle, Geräte an Shelly-Steckdosen überwachen.
