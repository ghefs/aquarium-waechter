# Aquarium Guardian (Aquarium-Wächter) – GHL ProfiLux 4 in Home Assistant

**🇬🇧 English** · [🇩🇪 Deutsch](README.md)

### 🐠 Take your aquarium to the next level.

**Connects GHL ProfiLux 4 to Home Assistant in minutes** – no YAML, no hand-made
sensors: all values automatically in HA, monitoring of pumps, probes, KH Director and
dosing pumps – and an **alarm on your phone that rings even at night in silent mode**,
before a failed pump turns into an emergency.

> ☕ **The add-on is free – and will stay free.** If it saves you a sleepless night or
> even a whole tank, buy me a coffee. It keeps the project alive and funds new features.
>
> <a href="https://buymeacoffee.com/gutgeschaltet"><img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=gutgeschaltet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy me a coffee" height="50"></a>

> **Language note:** The dashboard, settings page and alarm messages are currently
> in **German**. The add-on works with any ProfiLux 4 worldwide; the detailed
> [wiki](https://github.com/ghefs/aquarium-waechter/wiki) is in German (your browser
> can translate it). Questions in English are welcome in the issues.

Home Assistant add-on that monitors one or more **GHL ProfiLux 4** aquarium
controllers (saltwater and freshwater) via the local GHL API – with alarms on your
phone (HA companion app and/or Telegram):

- **All values in Home Assistant:** power sockets (state, current – grouped by power
  bar), probes (temperature, pH, conductivity/salinity, ORP …), level sensors, flow,
  KH Director, ION Director, dosing container levels (ml and %), lights (brightness
  per channel).
- **Alarm on your phone** when something is wrong – as a **critical notification** of
  the HA app (rings even in silent mode / Do Not Disturb) and/or via Telegram:
  pump stopped (current too low), temperature out of range, dosing container almost
  empty, no KH measurement any more, ProfiLux unreachable. With delay against false
  alarms, reminders and an “OK again” message.
- **Maintenance mode:** one switch pauses all alarms while you work on the tank;
  it switches itself off after the set time.
- **Everything configurable in HA:** what is read, what is monitored, limits.
- **Dashboard is created automatically** – an overview and a settings page per ProfiLux.
- **Collective fault sensor** (`binary_sensor.aquarium_waechter_sammelstoerung`) for
  your own automations – flash a light, sound a siren, make an announcement.
- Any number of ProfiLux units, each with its own equipment. Everything present is
  detected automatically.

Telegram alarms are sent by the add-on **directly** to Telegram – so they arrive even
if Home Assistant itself is stuck. If you want to be really safe, use both.

### Why this project?

Because a pump failed unnoticed over a weekend. The aquarium controller regulates
reliably – but it does not necessarily tell you **that a device is not running at all**.
That gap is what this add-on closes:

- **Really monitor outputs:** socket is ON but the pump draws no current? → alarm.
  Current suddenly too high (blocked pump)? → alarm.
- **KH Director:** no measurement for hours (reagent empty, tube clogged)? → alarm.
  From firmware 7.52 also leak and measuring-range alarms.
- **Probe errors:** probe no longer delivers a valid value? → alarm (firmware 7.52+).
- **Dosing containers:** “refill” warning in time, in ml and %.
- **ProfiLux gone** (power cut, Wi-Fi, API off after a firmware update)? → alarm.

> **Not an official GHL product.** Independent open-source project, not affiliated
> with or endorsed by GHL Advanced Technology. “GHL”, “ProfiLux” and “GHL Connect”
> are trademarks of their respective owners and are only used to describe which
> devices the add-on works with. Outputs are only read, never switched.

> ⚠️ **Use at your own risk – see [Disclaimer](#disclaimer).** The add-on is an
> additional layer of monitoring and **no replacement** for the ProfiLux's own safety
> functions and independent protection at the tank.

## Quick start

> Requirement: Home Assistant **OS** (or “Supervised”) and a **backup** first
> (*Settings → System → Backups*).

1. **Enable the GHL API** – in GHL Connect under *System → GHL API* (“read only” is
   enough). It is switched off again after every firmware update. Give the ProfiLux a
   fixed IP address.
2. **MQTT:** *Settings → Add-ons → Add-on Store* → install and start **Mosquitto
   broker**, then under *Devices & services* “MQTT discovered” → **Configure**.
3. **Add the repository** and install **Aquarium-Wächter**:

   [![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fghefs%2Faquarium-waechter)

4. Tab **Configuration** → enter your ProfiLux → Save → **Start**:

   ```yaml
   geraete:            # devices
     - name: Reef
       host: 192.168.1.50
   ```

5. Open the **Aquarium** dashboard in the sidebar → settings page: choose your
   **power bars** (*Steckdosenleisten*), decide **what is monitored**, enable your phone
   under **“Alarm aufs Handy”** (alarm to phone) → **“Test-Alarm senden”** (send test alarm). 📱

> The GHL API serves **only one client at a time** – other queries of the same
> ProfiLux (old `tcp` sensors, Node-RED …) must be removed.

### Key German terms in the UI

| German | English |
|---|---|
| Steckdose / Steckdosenleiste | power socket / power bar |
| Dosierer, Füllstand | doser, fill level |
| Niveau, Durchfluss | level sensor, flow |
| Störung / Sammelstörung | fault / collective fault |
| Wieder OK | OK again |
| Wartung – Alarme pausieren | maintenance – pause alarms |
| Alarm melden | send alarm |
| Geräte neu einlesen | rescan devices |
| Nachfüllen | refill |

## Support the project

The add-on is free and will stay free. Every version means many evenings of
development and testing on a real tank. If it helps you – or even saved a tank –
I'd be happy about a coffee:

<a href="https://buymeacoffee.com/gutgeschaltet"><img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=gutgeschaltet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy me a coffee" height="50"></a>

Just as valuable: ⭐ the repo, report bugs and share experiences with other ProfiLux
setups (issues – English or German).

## Disclaimer

This project is provided **without any warranty** and used **at your own risk**. To the
extent permitted by law, **no liability** is accepted for damage of any kind – in
particular to animals, plants, aquarium, equipment, buildings or data, for
consequential damage, or for missing, delayed or wrong notifications.

- Monitoring can fail: power cut, Wi-Fi/network, Home Assistant, Telegram, internet,
  ProfiLux firmware updates (GHL API off afterwards) etc. No message does **not** mean
  everything is fine.
- The add-on does **not** replace safety devices: heater with its own thermostat,
  level/overflow protection, residual-current device (RCD/GFCI), smoke detector,
  regular visual checks.
- Limits and settings are the user's responsibility.
- Make a Home Assistant backup before installing and updating.

Legally binding is the [MIT license](LICENSE).

## Keywords

GHL ProfiLux 4, ProfiLux 3, GHL Connect, aquarium controller, reef tank, saltwater
aquarium, marine aquarium, KH Director, doser, dosing pump, Home Assistant add-on,
MQTT, Telegram, pump monitoring, aquarium alarm, critical notification

## License

MIT – see [LICENSE](LICENSE).
