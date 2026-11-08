# Aquarium-Wächter – GHL ProfiLux 4 in Home Assistant

> 🇬🇧 *Home Assistant add-on that monitors GHL ProfiLux 4 aquarium controllers and sends
> Telegram alarms when a pump stops, a probe fails, the KH Director stops measuring or a
> dosing container runs low. Documentation is in German – the add-on works worldwide.*

Home-Assistant-Add-on, das einen oder mehrere **GHL ProfiLux 4** Aquariencomputer
(Meerwasser/Süßwasser) über die lokale GHL-API überwacht – mit Telegram-Alarm:

- Alle Werte in Home Assistant: Steckdosen (Zustand, Strom – gruppiert nach Leiste),
  Sensoren (Temperatur, pH, Leitwert/Salinität, Redox …), Niveau, Durchfluss,
  KH-Director, ION-Director, Füllstände der Dosierbehälter (ml und %), Licht
  (Helligkeit je Kanal). Was genau geht und was nicht: [Was wird ausgelesen?](#was-wird-ausgelesen)
- **Alarm per Telegram**, wenn etwas nicht stimmt: Pumpe steht (zu wenig Strom),
  Temperatur außerhalb der Grenzen, Dosierbehälter fast leer, keine KH-Messung mehr,
  ProfiLux nicht erreichbar. Mit Verzögerung, Erinnerung und „Wieder OK“
- **Alles in HA einstellbar:** was abgefragt wird, was überwacht wird, Grenzwerte
- **Dashboard wird automatisch erzeugt** – eine Übersicht und eine Einstellungsseite je ProfiLux
- Beliebig viele ProfiLux, jeder mit eigener Ausstattung (1 oder 6 Steckdosenleisten,
  2 oder 12 Dosierer …). Erkannt wird automatisch, was vorhanden ist

Die Alarme schickt das Add-on **direkt** an Telegram – sie kommen also auch dann,
wenn Home Assistant selbst gerade hängt.

### Warum dieses Projekt?

Weil am Wochenende unbemerkt eine Pumpe ausgefallen ist. Der Aquariencomputer regelt
zuverlässig – aber er sagt einem nicht unbedingt, **dass ein Gerät gar nicht läuft**.
Genau diese Lücke schließt der Wächter:

- **Ausgänge wirklich überwachen:** Steckdose ist AN, aber die Pumpe zieht keinen
  Strom? → Alarm. Strom plötzlich zu hoch (blockierte Pumpe)? → Alarm.
- **KH-Director im Blick:** Seit Stunden keine Messung mehr (Reagenz leer, Schlauch
  verstopft)? → Alarm. Ab Firmware 7.52 zusätzlich Leck- und Messbereichsalarm.
- **Sensorfehler:** Sonde liefert keinen gültigen Wert mehr? → Alarm (ab Firmware 7.52).
- **Dosierbehälter:** rechtzeitig „Nachfüllen“, in ml und %.
- **ProfiLux weg** (Stromausfall, WLAN, API nach Firmware-Update aus)? → Alarm.
- Alles mit Verzögerung gegen Fehlalarme, Erinnerung und „Wieder OK“ – aufs Handy,
  unabhängig von Cloud und App.

> **Kein offizielles Produkt von GHL.** Unabhängiges Open-Source-Projekt, nicht mit
> GHL Advanced Technology verbunden oder von GHL unterstützt. „GHL“, „ProfiLux“ und
> „GHL Connect“ sind Marken ihrer jeweiligen Inhaber und werden hier nur verwendet,
> um zu beschreiben, mit welchen Geräten das Add-on funktioniert.
> Ausgänge werden nur gelesen, niemals geschaltet.

> ⚠️ **Nutzung auf eigene Gefahr – siehe [Haftungsausschluss](#haftungsausschluss).**
> Der Wächter ist eine zusätzliche Überwachung und **kein Ersatz** für die
> Schutzfunktionen des ProfiLux und unabhängige Sicherungen am Becken.

**→ Direkt zur [Installation](#installation)**

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-unterst%C3%BCtzen-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/gutgeschaltet)

## Voraussetzungen

1. **GHL-API am ProfiLux einschalten** – in GHL Connect unter *System → GHL API*
   („Nur lesen“ genügt). Achtung: Nach jedem Firmware-Update ist sie wieder aus.
2. Feste IP-Adresse für jeden ProfiLux (DHCP-Reservierung im Router).
3. **MQTT** in Home Assistant (siehe unten).
4. Optional: Telegram-Bot für die Alarme.

> Die GHL-API (TCP-Port 10002) bedient **nur einen Client gleichzeitig**. Alte
> `tcp`-Sensoren, Node-RED-Flows o. ä., die denselben ProfiLux abfragen, müssen weg –
> sonst gibt es sporadische Lesefehler.

## MQTT einrichten – wozu und wie

Das Add-on schickt seine Werte über **MQTT** an Home Assistant. MQTT ist ein
kleiner „Briefkasten“ innerhalb von Home Assistant: Das Add-on legt dort Werte ab,
Home Assistant holt sie heraus und legt daraus automatisch Geräte und Entitäten an
(„MQTT Discovery“). Umgekehrt landen Einstellungen, die man in HA ändert (z. B.
„Temperatur Minimum“), über denselben Weg beim Add-on.

**Einrichtung – einmalig, ca. 2 Minuten:**

1. *Einstellungen → Add-ons → Add-on-Store* → **Mosquitto broker** installieren
   und starten (Haken bei „Beim Booten starten“).
2. *Einstellungen → Geräte & Dienste*: Home Assistant meldet „MQTT gefunden“ →
   **Konfigurieren** → **Absenden**. Fertig.

Benutzername und Passwort muss man **nicht** eintragen: Das Add-on bekommt den
Zugang automatisch vom Supervisor. Ein bereits vorhandener Mosquitto (z. B. für
Zigbee2MQTT) wird einfach mitbenutzt.

Nur wer einen **externen** Broker verwenden will (kein Mosquitto-Add-on), trägt ihn
in HA unter *Einstellungen → Add-ons → Aquarium-Wächter → Konfiguration* ein.
Die Felder erscheinen nach Klick auf **„Nicht verwendete optionale
Konfigurationsoptionen anzeigen“**:

| Feld | Beispiel |
|---|---|
| Externer MQTT-Broker (Host) | `192.168.1.10` |
| Externer MQTT-Broker (Port) | `1883` |
| Externer MQTT-Broker (Benutzer) | `aquarium` |
| Externer MQTT-Broker (Passwort) | `geheim` |

Alternativ im selben Reiter über *⋮ → Als YAML bearbeiten*:

```yaml
mqtt_host: 192.168.1.10
mqtt_port: 1883
mqtt_user: aquarium
mqtt_password: geheim
```

## Installation

> **Voraussetzung:** Home Assistant **OS** (oder „Supervised“) – nur dort gibt es
> Add-ons. Bei Home Assistant „Container“ oder „Core“ fehlt der Add-on-Store.
> Prüfen: *Einstellungen → System → Info* → „Installationsart“.

### Schritt 0 – Backup erstellen

Vor der Installation (und vor größeren Updates) eine Sicherung von Home Assistant
anlegen: *Einstellungen → System → Backups → **Backup erstellen*** (vollständiges
Backup). Damit lässt sich der vorherige Zustand jederzeit wiederherstellen.

Das Add-on legt eigene Geräte, Entitäten und ein Dashboard an, ändert aber keine
bestehende Konfiguration. Trotzdem gilt: erst sichern, dann installieren.

### Schritt 1 – Repository hinzufügen

Knopf drücken (öffnet das eigene Home Assistant) und bestätigen:

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fghefs%2Faquarium-waechter)

<details><summary>Ohne Knopf (von Hand)</summary>

*Einstellungen → Add-ons → Add-on-Store* → oben rechts **⋮ → Repositories** →
`https://github.com/ghefs/aquarium-waechter` eintragen → **Hinzufügen** → Schließen.
Danach die Seite neu laden (⋮ → Nach Updates suchen).
</details>

### Schritt 2 – Add-on installieren

Im Add-on-Store erscheint der Abschnitt **Aquarium-Wächter** → Add-on anklicken →
**Installieren**. Das fertige Add-on wird von GitHub geladen, das dauert meist
weniger als eine Minute.

### Schritt 3 – MQTT

Falls noch nicht vorhanden: **Mosquitto broker** installieren und MQTT einrichten –
siehe [MQTT einrichten](#mqtt-einrichten--wozu-und-wie). Ohne MQTT startet das Add-on nicht.

### Schritt 4 – ProfiLux eintragen

Im Add-on den Reiter **Konfiguration** öffnen → oben rechts **⋮ → Als YAML
bearbeiten** → Inhalt ersetzen, Namen und IP-Adressen anpassen → **Speichern**:

```yaml
geraete:
  - name: Riff
    host: 192.168.1.50
  - name: Quarantäne
    host: 192.168.1.51
    kh_director: false
    dosierer: false
telegram_chat_id: ""
intervall: 60
dashboard: true
```

Ein ProfiLux genügt natürlich auch. Die Chat-ID kommt später (Abschnitt
[Telegram](#telegram)) – leer gelassen werden Meldungen nur ins Log geschrieben.

### Schritt 5 – Starten

Reiter **Info** → **Starten** (am besten auch „Beim Booten starten“ und „Watchdog“
anhaken). Im Reiter **Protokoll** steht nach ca. 1 Minute je Gerät z. B.
`Inventar: 10 Steckdosen, 6 Sensoren, …`.

Danach gibt es:
- je ProfiLux ein Gerät unter *Einstellungen → Geräte & Dienste → MQTT*
- in der Seitenleiste das Dashboard **Aquarium** mit Übersicht und Einstellungsseite

Jetzt auf der Einstellungsseite festlegen, was überwacht wird
(siehe [Überwachung einstellen](#überwachung-einstellen)).

### Schritt 6 – optional: Telegram-Befehle und Shelly

[![Blueprint Telegram-Befehle importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fghefs%2Faquarium-waechter%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Faquarium_waechter%2Ftelegram_befehle.yaml)
&nbsp;Telegram-Befehle (`/aq`, `/aq_werte` …) – siehe [Werte per Befehl abfragen](#werte-per-befehl-abfragen-optional)

[![Blueprint Shelly importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fghefs%2Faquarium-waechter%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Faquarium_waechter%2Fshelly_geraet_ueberwachen.yaml)
&nbsp;Geräte an Shelly-Steckdosen überwachen – siehe [Shelly](#optional-shelly-steckdosen)

### Updates

Neue Versionen erscheinen in Home Assistant wie bei jedem Add-on unter
*Einstellungen* (Update-Hinweis) → **Aktualisieren**. Einstellungen bleiben erhalten.
Beim Aktualisieren den Haken „Backup erstellen“ gesetzt lassen.
Was sich geändert hat, steht im [Änderungsprotokoll](aquarium_waechter/CHANGELOG.md)
(in HA beim Add-on im Reiter „Änderungsprotokoll“).

### Deinstallieren

Add-on deinstallieren. Die Entitäten verschwinden nicht automatisch: unter
*Einstellungen → Geräte & Dienste → MQTT* die ProfiLux-Geräte löschen und das
Dashboard **Aquarium** entfernen.

### Was wird ausgelesen?

Der Wächter fragt beim Start ab, was der ProfiLux hat, und legt nur das an.
Alles mit **Lesezugriff** – der Wächter schaltet nie etwas.

| Bereich | Was in HA erscheint | Alarm möglich | Lücken / Hinweise |
|---|---|---|---|
| Steckdosen (bis 64) | an/aus, Strom in A | Strom zu niedrig (Gerät läuft nicht) / zu hoch | Schalten geht nicht (API nur lesend). Leisten ohne Strommessung (STDL4-4) melden immer 0 A → Leiste auswählen, dann kein Strom-Alarm |
| Sensoren (bis 32) | Messwert, Sollwert; Leitwert zusätzlich Salinität und Dichte | unter Minimum / über Maximum; Sensorfehler laut ProfiLux¹ | Die Sensor-**Art** liefert die API nicht – sie wird geschätzt und ist in HA wählbar |
| Niveausensoren (bis 16) | Zustand 0/1 | bei 0 oder bei 1 | Welcher Wert „Alarm“ bedeutet, mit dem Schwimmer ausprobieren |
| Durchfluss (bis 4) | Durchfluss | unter/über Grenze | Einheit liefert die API nicht |
| Dosierer (bis 32) | Füllstand in ml und % | „Nachfüllen“ unter X ml (1× täglich) | Dosiermengen/-zeiten nicht |
| KH-Director | KH-Wert, Sollwert, Zeit der letzten Messung | keine Messung seit X h; Leck- und Messbereichsalarm¹ | |
| ION-Director | 5 Messwerte mit Sollwert | unter/über Grenze | Ohne Testgerät entwickelt – Namen der Werte (Ca, Mg …) liefert die API nicht |
| Licht (bis 32 Kanäle) | aktuelle Helligkeit in % | – (ändert sich über den Tag) | Tageskurven, Lichtszenen nicht |
| **Nicht verfügbar** | Steckdosenleisten-Zuordnung (PAB-Geräte), Timer-Schaltzeiten, Pumpen-/Strömungsmodi, Fütterungspause/Wartung (Zustand), die Alarmliste des ProfiLux, Firmware/Uhrzeit bei älterer Firmware | | Gibt die GHL-API nicht her |

¹ Erst ab ProfiLux-Firmware **7.52** (dann enthält das Messprotokoll Statusbits).
Mit älterer Firmware fehlen nur diese Zusatzalarme, alles andere geht.

Feinsteuerung auf drei Ebenen:

| Ebene | Wo | Beispiel |
|---|---|---|
| Ganzer Bereich | Dashboard → Einstellungsseite → **Bereiche** (Standard: alle an; Vorgabe auch per Add-on-Konfiguration `steckdosen`, `sensoren`, `niveau`, `durchfluss`, `dosierer`, `kh_director`, `ion_director`, `licht`) | Licht interessiert nicht → „Licht (Helligkeit)“ aus |
| Unbenannte mitnehmen | Add-on-Konfiguration `unbenannte_abfragen: true` | Kunde hat Steckdosen in GHL nicht benannt |
| Einzelnes Teil | Einstellungsseite: Schalter **„Auslesen“** | Ungenutzten Dosierer ausblenden |

Standardmäßig wird ausgelesen, was in GHL Connect **einen Namen hat**; leere Plätze
am Ende (z. B. Steckdose 11–64) werden gar nicht erst angelegt. Weniger auslesen =
schnellere Runden (jeder Wert kostet ca. 0,4 s). Mit dem Knopf **„Geräte neu
einlesen“** wird das Inventar sofort neu gelesen (sonst stündlich) – z. B. nach
Umbenennen in GHL Connect.

### Namen und Kürzel

Jeder Name beginnt mit einem Kürzel, damit man immer weiß, was gemeint ist:

| Kürzel | Bedeutung |
|---|---|
| `Steckdose 5 · Heizer` | Steckdose 5 (ohne Leisten-Angabe einfach durchgezählt) |
| `Steckdose 2.3 · Heizer` | Leiste 2, Dose 3 (wenn [Steckdosenleisten](#steckdosenleisten) gewählt sind) |
| `Temperatur · Becken`, `pH · Sonde 2`, `Salinität · Leitwert` | Sensor nach Art |
| `Niveau 1 · …`, `Durchfluss 1 · …`, `Dosierer 4 · Kalium`, `Licht 1 · Weiß` | Art und Nummer wie in GHL Connect |
| `KH-Director · …`, `ION-Director · …` | Messgeräte |

Die Zahlen entsprechen der Zählung in GHL Connect (ab 1).

### Steckdosenleisten

Die GHL-API verrät nicht, welche Steckdose auf welcher Leiste sitzt – sie zählt die
Steckdosen einfach über alle Leisten durch. Deshalb wählt man die Leisten einmal aus:
Dashboard → Einstellungsseite → **Steckdosenleisten** → **Leiste 1**, **Leiste 2** …

| Auswahl | Dosen | Strommessung |
|---|---|---|
| STDL4-4 (am S-Anschluss des ProfiLux) | 4 | nein |
| Powerbar 6E-PAB | 6 | ja |
| Powerbar 6D-PAB | 6 | ja |
| Powerbar 5.1-PAB | 5 | ja |
| Powerhub 6D-PAB | 6 Kanäle | ja |
| Andere – 2/4/8 Dosen ohne bzw. 6 Dosen mit Strommessung | | |

**Leiste 1** bekommt die ersten Steckdosen, **Leiste 2** die nächsten usw. – in der
Reihenfolge der Steckdosennummern in GHL Connect (meist zuerst die STDL-Leisten am
S-Anschluss, dann die PAB-Leisten). Darunter zeigt das Dashboard das Ergebnis, z. B.

> Steckdose 1–4: STDL4-4 (ohne Strommessung) · Steckdose 5–10: Powerbar 6E-PAB

Danach heißen die Steckdosen `Steckdose 2.1 · Strömungspumpe` (Leiste 2, Dose 1), im
Dashboard hat jede Leiste einen eigenen Block, und Leisten **ohne Strommessung**
bekommen keinen (sinnlosen) Strom-Alarm – dort gibt es nur die Anzeige an/aus.

Für viele Geräte auf einmal geht es auch als Vorgabe in der Add-on-Konfiguration
(Text, Anzahl Dosen je Leiste): `steckdosenleisten: "STDL=4 (ohne Strom), PAB=6"`.

### Überwachung einstellen

Auf der Einstellungsseite des Dashboards steht über jedem Block der Kasten **„So wird
überwacht“** – je Teil ein Satz, der die aktuelle Regel zusammenfasst, z. B.:

> Alarm, wenn die Steckdose AN ist, aber weniger als 0,25 A fließen (Gerät läuft
> nicht), wenn das länger als 2 min anhält.

Darunter die Einstellungen:

| Einstellung | Bedeutung |
|---|---|
| **Auslesen** | Wert wird gelesen und in HA angezeigt (aus = Teil verschwindet aus HA) |
| **Alarm per Telegram** | Nur wenn an, wird gemeldet |
| **Alarm, wenn AN und Strom unter** | Steckdosen: Pumpe steht → Strom fällt. Wert etwas unter dem normalen Strom wählen |
| **Alarm, wenn Strom über (0 = aus)** | Steckdosen: z. B. blockierte Pumpe |
| **Alarm, wenn Wert unter / über** | Messwerte: zulässiger Bereich |
| **Nachfüllen melden unter** | Dosierer, in ml |
| **Alarm, wenn keine Messung seit** | KH-Director (Standard: an, 7 h – er misst typischerweise alle 4–5 h) |
| **Alarm, wenn Sensor meldet** | Niveau: 0 oder 1 |
| **Alarm erst nach (Dauer)** | So lange muss die Störung anstehen – kurze Ausreißer lösen nichts aus |
| **Sensor-Art (für Einheit)** | Temperatur, pH, Leitwert … – bestimmt Einheit und Zusatzwerte (Salinität, Dichte) |

Auf der Übersichtsseite listet **„So wird überwacht“** alle überwachten Punkte mit
Regel und aktuellem Zustand. Die Einstellungen speichert das Add-on selbst; sie
überleben Neustarts und Updates.

### Dosierbehälter neu belegt / Steckdose umgesteckt

Entitäten hängen am **Steckplatz** (Dosierer 4, Steckdose 7 …), nicht am Namen. Wird
in GHL Connect ein Name geändert, z. B. Dosierer 4 „Kalium“ → „Magnesium“:

- Name in HA ändert sich mit, Entitäts-ID, Verlauf und Dashboard bleiben
- Die Einstellungen (z. B. „Nachfüllen melden unter 500 ml“) bleiben – der Wächter **meldet die
  Änderung per Telegram** („Belegung geändert: Dosierer 4: Kalium → Magnesium – Überwachung
  bleibt an, bitte prüfen“)
- Eine laufende Störung zu diesem Steckplatz wird zurückgesetzt

Nachfüllen eines Behälters braucht nichts weiter: Steigt der Füllstand über die
Grenze, kommt „Wieder OK“.

### Dashboard

Das Add-on legt ein **eigenes** Dashboard „Aquarium“ an und pflegt es (neue
Steckdosen, umbenannte Teile …). Andere Dashboards fasst es nie an.

Wird das Dashboard **von Hand geändert**, erkennt das Add-on das und überschreibt es
nicht mehr. Wer es anpassen möchte, kopiert es am besten (*⋮ → Bearbeiten → ⋮ →
Raw-Konfiguration*) in ein eigenes Dashboard. Der Knopf **„Dashboard neu erzeugen“**
stellt den Originalzustand wieder her (Handänderungen gehen dann verloren).
Mit `dashboard: false` ist die Automatik ganz aus.

## Telegram

Meldungen gehen an eine **Gruppe** (praktisch, wenn mehrere Leute Bescheid wissen
sollen) oder an **einzelne Personen** – oder an beides, mehrere IDs mit Komma:
`-1234567890, 987654321`.

1. In Telegram **@BotFather** öffnen, `/newbot` senden, Namen vergeben → **Token**
   notieren (sieht aus wie `123456:ABC-…`, geheim halten).
2. Den Bot anschreiben:
   - **Gruppe:** Bot in die Gruppe einladen und dort eine beliebige Nachricht schreiben.
   - **Einzelchat:** den Bot suchen, **Start** drücken und eine Nachricht schreiben
     (ein Bot darf niemanden von sich aus anschreiben).
3. **Chat-ID herausfinden:** im Browser
   `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen (`<TOKEN>` ersetzen) und
   nach `"chat":{"id":` suchen – die Zahl dahinter ist die Chat-ID.
   Erscheint nur `"result":[]`, noch einmal eine Nachricht schreiben und neu laden.
   *(Das klappt nur, solange die Telegram-Integration in HA noch nicht läuft – sie
   holt die Nachrichten sonst vorher ab. Dann vorübergehend deaktivieren.)*

   | Chat-ID | bedeutet |
   |---|---|
   | `987654321` (positiv) | Einzelchat mit einer Person |
   | `-1234567890` | Gruppe |
   | `-100…` | Supergruppe / Kanal |

   Wird eine Gruppe später zur Supergruppe (passiert automatisch, z. B. beim Vergeben
   von Admin-Rechten), ändert sich die ID – dann kommt der Telegram-Test nicht mehr an
   und die neue ID muss eingetragen werden.
4. In HA die Integration **Telegram bot** einrichten (Token, „Polling“) und die
   Chat-ID als erlaubten Chat hinzufügen.
5. Chat-ID in der Add-on-Konfiguration bei `telegram_chat_id` eintragen.

Den Token liest das Add-on aus der Telegram-Integration – er muss nur dort gepflegt
werden. Ohne Integration: Token unter `telegram_token` eintragen.
Testen mit dem Knopf **„Telegram-Test senden“**.

Meldungen beginnen mit dem Gerätenamen, z. B. `🚨 Riff · STÖRUNG: Rückförderpumpe`.

### Welche Meldungen kommen?

| Meldung | Wann | Ton |
|---|---|---|
| `🚨 Riff · STÖRUNG: Steckdose 2.1 · Rückförderpumpe` | Störung steht länger als die eingestellte Verzögerung an | ja |
| `🚨 Riff · Noch immer: …` | Erinnerung, solange die Störung anhält (Standard stündlich) | ja |
| `✅ Riff · Wieder OK: …` | Störung ist vorbei | ja |
| `ℹ️ Riff · Nachfüllen: Calcium` | Dosierbehälter unter der Warngrenze (Erinnerung täglich) | leise |
| `🚨 Riff · ProfiLux nicht erreichbar` | 10 min keine Antwort (einstellbar) | ja |
| `ℹ️ Riff · Belegung geändert` | Steckdose/Dosierer in GHL Connect umbenannt | leise |

### Sammelstörung – für eigene Automationen

Unabhängig von Telegram gibt es eine **Sammelstörung**, die man in HA für alles
Mögliche nutzen kann (Lampe blinken lassen, Sirene, Sprachansage, Push aufs Handy …):

| Entität | Bedeutung |
|---|---|
| `binary_sensor.aquarium_waechter_sammelstoerung` | an, sobald bei **irgendeinem** ProfiLux eine Störung gemeldet ist |
| `binary_sensor.<gerät>_sammelstoerung` | dasselbe je ProfiLux, z. B. `binary_sensor.riff_sammelstoerung` |

„Gemeldet“ heißt: genau dann, wenn auch die Telegram-Meldung käme (nach der
eingestellten Verzögerung); „ProfiLux nicht erreichbar“ zählt mit. Attribute:
`anzahl`, `stoerungen` (Liste) und `text` (fertiger Meldungstext).

Beispiel – Wohnzimmerlampe rot, solange eine Störung anliegt:

```yaml
automation:
  - alias: Aquarium-Störung → Lampe rot
    triggers:
      - trigger: state
        entity_id: binary_sensor.aquarium_waechter_sammelstoerung
        to: "on"
    actions:
      - action: light.turn_on
        target: { entity_id: light.wohnzimmer }
        data: { color_name: red }
      - action: tts.speak
        target: { entity_id: tts.home_assistant_cloud }
        data:
          media_player_entity_id: media_player.kueche
          message: "Achtung Aquarium: {{ state_attr('binary_sensor.aquarium_waechter_sammelstoerung', 'text') }}"
```

### Werte per Befehl abfragen (optional)

In der Gruppe einen Befehl schreiben oder antippen – der Bot antwortet mit den
aktuellen Werten aller ProfiLux:

| Befehl | Antwort |
|---|---|
| `/aq` | Störungen und die wichtigsten Messwerte, Zeitpunkt der letzten KH-Messung |
| `/aq_werte` | alle Messwerte mit Sollwert, Niveau |
| `/aq_steckdosen` | alle Steckdosen: an/aus und Strom |
| `/aq_dosierer` | Füllstände aller Dosierbehälter |
| `/aq_hilfe` | Befehlsliste |

Die Befehle haben das Kürzel `aq_` (mit Unterstrich, denn Telegram macht nur Befehle
ohne Leerzeichen antippbar). So bleiben Befehle wie `/status` oder `/hilfe` frei für
andere Automationen in Home Assistant.

Beispiel `/aq`:

```
ℹ️ Status – Aquarium
━━ Riff ━━
✅ Alles OK (9 Punkte überwacht)
• KH: 7.9 dKH
• pH: 8.2
• Salinität: 35.0 PSU
• Temperatur: 25.4 °C
• KH gemessen: 03.05. 09:12
```

Einrichten:

1. Blueprint importieren: *Einstellungen → Automationen → Blueprints → Blueprint
   importieren* →
   `https://github.com/ghefs/aquarium-waechter/blob/main/blueprints/automation/aquarium_waechter/telegram_befehle.yaml`
2. **Automation erstellen** → bei „Antworten an“ die Notify-Entität der Gruppe wählen
   (sie entsteht, wenn die Gruppe in der Telegram-Integration als erlaubter Chat
   hinzugefügt wird, z. B. `notify.aquarium_gruppe`).
3. Optional, damit die Befehle beim Tippen von `/` als Menü erscheinen: bei
   **@BotFather** → `/setcommands` → Bot wählen → senden:

   ```
   aq - Aquarium: Störungen und wichtigste Werte
   aq_werte - Aquarium: alle Messwerte mit Soll
   aq_steckdosen - Aquarium: Steckdosen und Strom
   aq_dosierer - Aquarium: Füllstände der Dosierer
   aq_hilfe - Aquarium: Befehlsliste
   ```

Der Bot antwortet nur in Chats, die in der Telegram-Integration erlaubt sind.
Die Befehle laufen über Home Assistant – die Alarme dagegen kommen direkt vom Add-on.

## Optional: Shelly-Steckdosen

Geräte an Shelly-Steckdosen (Plug S, Plus Plug, Pro …) überwacht nicht das Add-on,
sondern Home Assistant selbst – mit dem Blueprint
`blueprints/automation/aquarium_waechter/shelly_geraet_ueberwachen.yaml`
(Leistung zu gering/zu hoch, Shelly offline). Je Gerät eine Automation anlegen und
wählen, welcher Leistungssensor, welche Grenzen und welches Benachrichtigungsziel.
Wer keine Shellys hat, ignoriert ihn einfach.

Import: Knopf in [Schritt 6](#schritt-6--optional-telegram-befehle-und-shelly) oder
*Einstellungen → Automationen → Blueprints → Blueprint importieren* →
`https://github.com/ghefs/aquarium-waechter/blob/main/blueprints/automation/aquarium_waechter/shelly_geraet_ueberwachen.yaml`

## Entwicklung

Bei jedem Push auf `main`, der den Ordner `aquarium_waechter/` ändert, baut GitHub
Actions die Images `ghcr.io/ghefs/{aarch64,amd64}-aquarium-waechter:<version>`.
Neue Version: `version` in `config.yaml` erhöhen, Eintrag in `CHANGELOG.md`, pushen –
nach ca. 3 Minuten steht das Update bereit.

Nur Python-Standardbibliothek. Lokal testen mit Simulatoren:

```sh
python3 tests/sim_ghl.py 19001 klein &
python3 tests/sim_ghl.py 19002 gross &
python3 tests/sim_mqtt.py 19883 &
cd aquarium_waechter && python3 -m waechter ../tests/options.json --data ../tests/_tmp
```

GHL-API-Kurzreferenz: Klartext über TCP 10002, `GET SWITCHCHANNEL[n] ACTSTATE`,
`… ACTCURRENT`, `… DESCRIPTION`, `GET SENSOR[n] ACTVALUE[u]`, `GET DOSER[n] FILLLEVEL`,
`GET KHDIRECTOR ACTVALUE`, `GET KHDIRECTOR DATARECORD[0]` (Zeit der letzten Messung).
Antworten `ACK <…>` / `NACK (code)`; Indizes ab 0.

## Unterstützen

Der Wächter ist kostenlos und bleibt es. Wenn er dir hilft – oder vielleicht sogar
ein Becken gerettet hat – freue ich mich über einen Kaffee:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-unterst%C3%BCtzen-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/gutgeschaltet)

Genauso wertvoll: ⭐ für das Repo, Fehler melden und Erfahrungen mit anderen
ProfiLux-Ausstattungen teilen (Issues).

## Haftungsausschluss

Dieses Projekt wird **ohne jede Gewährleistung** bereitgestellt und die Nutzung
erfolgt **auf eigene Gefahr**. Für Schäden jeglicher Art – insbesondere an Tieren,
Pflanzen, Aquarium, Technik, Gebäude oder Daten, für Folgeschäden sowie für
ausgebliebene, verspätete oder falsche Meldungen – wird **keine Haftung**
übernommen, soweit gesetzlich zulässig.

Bitte beachten:
- Eine Überwachung kann ausfallen: Stromausfall, WLAN/Netzwerk, Home Assistant,
  Telegram, Internet, Firmware-Updates des ProfiLux (GHL-API danach wieder aus) usw.
  Ausbleibende Meldungen bedeuten **nicht**, dass alles in Ordnung ist.
- Der Wächter ersetzt **keine** Schutzeinrichtungen: Heizstab mit eigenem
  Thermostat, Niveau-/Überlaufschutz, Fehlerstrom-Schutzschalter, Rauchmelder,
  regelmäßige Sichtkontrolle.
- Grenzwerte und Einstellungen liegen in der Verantwortung des Nutzers.
- Vor Installation und Updates ein Backup von Home Assistant erstellen.

Rechtlich maßgeblich ist die [MIT-Lizenz](LICENSE) („THE SOFTWARE IS PROVIDED
"AS IS", WITHOUT WARRANTY OF ANY KIND …“).

## Stichworte

GHL ProfiLux 4, ProfiLux 3, GHL Connect, Aquariencomputer, Aquarium Controller,
Riffaquarium, Meerwasseraquarium, KH Director, Doser, Dosierpumpe, Home Assistant
Add-on, MQTT, Telegram, Pumpenüberwachung, Aquarium Alarm

## Lizenz

MIT – siehe [LICENSE](LICENSE). Marken: siehe Hinweis oben.
