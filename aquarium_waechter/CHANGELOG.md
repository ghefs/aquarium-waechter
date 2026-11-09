# Änderungsprotokoll

Vor jedem Update empfiehlt sich ein Backup von Home Assistant
(beim Aktualisieren den Haken „Backup erstellen“ gesetzt lassen).

## 4.5.0

- **Fünf Sprachen / five languages:** Deutsch, English, Français, Español, Italiano.
  Dashboard, Entitätsnamen, Einstellungen, Alarme und Wochenbericht kommen automatisch in der
  Sprache von Home Assistant (nicht unterstützte Sprachen → Englisch). Fest einstellbar mit der
  Option `sprache`. Einstellungen bleiben beim Sprachwechsel erhalten; Entitäts-IDs ändern sich nicht.
  Danach einmal „Dashboard neu erzeugen“, falls das Dashboard von Hand geändert wurde.
- Konfigurationsseite des Add-ons ebenfalls in allen fünf Sprachen.
- **Gesamtleistung und Energie** je ProfiLux: `sensor.<gerät>_gesamt_leistung` (W) und
  `sensor.<gerät>_gesamt_energie` (kWh, fürs Energie-Dashboard von HA). Näherung: Summe der
  gemessenen Ströme × Netzspannung (einstellbar, Standard 230 V) plus Leistung der übernommenen
  HA-Steckdosen. Steckdosen ohne Strommessung zählen nicht mit. Im Wochenbericht: Ø-Leistung und kWh.

## 4.4.0

- **Dosierbehälter: „reicht noch … Tage“.** Das Add-on rechnet den Verbrauch je Tag aus
  und meldet „Nachfüllen“ rechtzeitig, wenn der Vorrat nur noch für wenige Tage reicht
  (neue Einstellung „… oder Vorrat unter“, Standard 3 Tage, 0 = aus). Die ersten Werte
  kommen nach etwa einem Tag.
- **Letzte Dosierung** (Zeit und ml) je Dosierer – ab ProfiLux-Firmware 7.52. Der Verbrauch
  wird dann direkt aus dem Dosierprotokoll berechnet (genauer, auch ohne Behältergröße).
- **Wochenbericht** per Push/Telegram: Mittel, Minimum und Maximum der Messwerte,
  Verbrauch und Vorrat der Dosierbehälter, Störungen der Woche. Wochentag auf der
  Einstellungsseite wählbar (Standard Sonntag, gegen 18 Uhr), Knopf „Wochenbericht jetzt senden“.
- **Steckdosen aus Home Assistant** (Shelly Plug S u. a. mit Leistungsmessung): auf der
  Einstellungsseite einschalten → im Dashboard mit Zustand und Leistung, überwacht wie eine
  ProfiLux-Steckdose (Leistung zu niedrig/zu hoch, nicht erreichbar), mit Wartung und
  Sammelstörung. Das Add-on schaltet sie nicht.
- **Einstellungsseite kompakter:** Karten werden lückenlos angeordnet, kurze Beschriftungen,
  Leisten ohne Strommessung nur noch eine Zeile je Dose.
- Übersicht: Dosierbehälter als Tabelle (Füllstand, reicht noch, Verbrauch, letzte Dosierung).
- Dashboard: kurze Kürzel, damit Namen nicht abgeschnitten werden – **SD 2.3** = Steckdose
  (Leiste 2, Dose 3), **DP 4** = Dosierpumpe 4. In den Meldungen bleibt der volle Name.

## 4.3.7

- Dashboard: Bei Steckdosenleisten mit Strommessung (z. B. Powerbar 6E-PAB) steht jetzt
  je Dose auch der Zustand (an/aus) neben dem Strom.

## 4.3.6

- Einstellungsseite: Hinweis bei „Bereiche“, dass das Neueinlesen nach dem Umschalten 1–3 Minuten dauern kann.

## 4.3.5

- **Fehler behoben:** Das Dashboard wurde nach Änderungen (z. B. Bereich „Licht“ aus) nicht
  aktualisiert, wenn die Verbindung zu Home Assistant länger ungenutzt war
  („Dashboard nicht aktualisiert: Home Assistant hat die Verbindung beendet“). Das Add-on
  verbindet sich jetzt automatisch neu.

## 4.3.4

- **Dosierer ohne Behältergröße** (Füllstand wird im ProfiLux nicht geführt, z. B. Probenwasser,
  Abwasser): nur noch Anzeige, keine Einstellung „Nachfüllen melden“ mehr – sie hätte bei 0 ml
  täglich gemeldet. Im Dashboard steht ein Hinweis, welche Dosierer keinen Füllstand haben.
- Spendenknopf jetzt bayerisch: 🍖 Schweinebraten statt Kaffee.

## 4.3.3

- Kleine Textänderungen im Dashboard.
- README auch auf Englisch (README.en.md).

## 4.3.2

- Meldung „Belegung geändert“: Grenzwerte mit Komma (0,25 statt 0.25).

## 4.3.1

- Texte und Rechtschreibung in Dashboard, Hilfetexten und Dokumentation korrigiert.

## 4.3.0

- **Wartungsmodus**: Schalter „Wartung – Alarme pausieren“ (Übersicht und Einstellungsseite),
  Dauer einstellbar (Standard 30 min). Während der Wartung kommen keine Alarme; danach
  schaltet sie sich selbst ab, noch anstehende Störungen werden dann gemeldet.
  Meldung bei Beginn und Ende; `/aq` zeigt die laufende Wartung an
  (Blueprint „Telegram-Befehle“ dafür neu importieren).
- **Licht:** Kanäle ohne Lichtkurve werden nicht mehr angelegt (ab Firmware 7.52).
- Formulare für Fehlermeldungen und Funktionswünsche auf GitHub.
- **Fehler behoben:** Seit 4.2.1 war die Add-on-Beschreibung fehlerhaft formatiert – Home
  Assistant zeigte deshalb keine Updates an. Wer noch 4.2.0 hat: im Add-on-Store
  *⋮ → Nach Updates suchen*, dann aktualisieren.

## 4.2.2

- Einstellung „Alarm per Telegram“ heißt jetzt **„Alarm melden“** (gilt für Push an die
  HA-App und Telegram). Entitäts-IDs und Einstellungen bleiben gleich.
- Neues **[Wiki](https://github.com/ghefs/aquarium-waechter/wiki)**: alle Entitäten, Beispiele für
  eigene Automationen, fertige Automationen, alle Optionen, Fehlersuche.

## 4.2.1

- Reiter **Dokumentation** im Add-on mit Kurzanleitung.
- ☕ Hinweis „Projekt unterstützen“ in README, Dokumentation und auf der Einstellungsseite.

## 4.2.0

- **Push an die Home-Assistant-App** (iPhone/Android): Das Add-on findet alle Handys
  mit HA-App selbst; auf der Einstellungsseite unter **„Alarm aufs Handy“** je Handy
  einschalten (Vorgabe auch per Option `app_push`). Dieselben Meldungen wie bei Telegram.
  **Störungen kommen als kritische Benachrichtigung** – sie klingeln auch bei
  Stummschaltung und „Nicht stören“.
  „Wieder OK“ normal, Hinweise (Nachfüllen …) leise. Telegram ist damit optional.
- Knopf „Telegram-Test senden“ heißt jetzt **„Test-Alarm senden“** und testet alle
  Wege (als dringende Meldung, damit man den Alarmton hört).

## 4.1.2

- **Sammelstörung**: `binary_sensor.aquarium_waechter_sammelstoerung` (alle Geräte) und je
  ProfiLux `binary_sensor.<gerät>_sammelstoerung` – an, solange eine Störung gemeldet ist;
  Attribute `anzahl`, `stoerungen`, `text`. Für eigene Automationen (Lampe, Sirene, Ansage …).
- Einstellungsseite: „So wird überwacht“ als gut lesbarer Kasten über jedem Block
  (statt abgeschnittener Zeile je Teil).
- **Fehler behoben:** Mit ausgewählten Steckdosenleisten startete 4.1.1 nach einem
  Neustart nicht mehr richtig („unerwarteter Fehler“ im Protokoll). Bitte aktualisieren.

## 4.1.1

- **Steckdosenleisten**: auf der Einstellungsseite je Leiste das GHL-Produkt wählen
  (STDL4-4, Powerbar 6E-PAB …) → Namen `Steckdose 2.1 · …`, im Dashboard je Leiste ein
  Block, Ergebnis im Klartext (Vorgabe auch per Option `steckdosenleisten`).
  Leisten ohne Strommessung (STDL4-4) bekommen keinen Strom-Alarm.
  **Bitte nach dem Update einmal die Leisten auswählen** (das Textfeld aus 4.1.0 entfällt).

## 4.1.0

- **Namen mit Kürzel vorne**, damit man immer weiß, was gemeint ist:
  `Steckdose 5 · Heizer`, `pH · Becken`, `Dosierer 4 · Kalium`, `Licht 1 · Weiß` …
  (Entitäts-IDs, Verlauf und Einstellungen bleiben gleich).
- **Steckdosenleisten**: Aufteilung auf der Einstellungsseite eintragen →
  Namen `Steckdose 2.1 · …`, im Dashboard je Leiste ein Block. Leisten „ohne Strom“ bekommen keinen (sinnlosen) Strom-Alarm mehr.
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
