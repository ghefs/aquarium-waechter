# Änderungsprotokoll

Vor jedem Update empfiehlt sich ein Backup von Home Assistant
(beim Aktualisieren den Haken „Backup erstellen“ gesetzt lassen).

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
