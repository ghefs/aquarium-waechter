# Sicherheit

## Unterstützte Versionen

Sicherheitskorrekturen gibt es nur für die **jeweils neueste Version** des
Aquarium-Wächters. Bitte vor einer Meldung auf die aktuelle Version aktualisieren.

## Sicherheitslücke melden

Bitte **kein öffentliches Issue** öffnen. Stattdessen vertraulich über GitHub melden:
**[Sicherheitslücke melden](https://github.com/ghefs/aquarium-waechter/security/advisories/new)**
(Reiter *Security* → *Report a vulnerability*).

Hilfreich sind: betroffene Version, Beschreibung, Schritte zum Nachvollziehen und die
möglichen Folgen. Ich melde mich in der Regel innerhalb weniger Tage. Nach der Korrektur
wird die Lücke im Änderungsprotokoll genannt – auf Wunsch mit Dank an den Finder.

## Wie der Wächter mit Daten umgeht

- Der ProfiLux wird nur **gelesen** (GHL-API, TCP 10002, lokal im Heimnetz); der Wächter
  schaltet und ändert dort nichts.
- Der Telegram-Token wird aus der Telegram-Integration von Home Assistant gelesen und
  nirgends gespeichert oder protokolliert. Nachrichten gehen nur an `api.telegram.org`.
- Push-Meldungen laufen über die Benachrichtigungsdienste von Home Assistant.
- Es gibt keine Telemetrie und keine Verbindung zu anderen Diensten.
- Die GHL-API selbst ist unverschlüsselt und ohne Anmeldung – den ProfiLux daher nicht
  aus dem Internet erreichbar machen.
