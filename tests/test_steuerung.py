"""Steuerung (SET-Befehle der offiziellen GHL-API):  python3 tests/test_steuerung.py  (aus dem Ordner aquarium_waechter)"""
import os
import socketserver
import sys
import threading
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(__file__))
import sim_ghl  # noqa: E402
import time as _time  # noqa: E402
import types  # noqa: E402
import waechter.ghl as _ghl  # noqa: E402
_ghl.time = types.SimpleNamespace(sleep=lambda s: None, time=_time.time)   # keine Pausen im Test
from waechter.ghl import GhlClient, Inventory, NackError  # noqa: E402
from waechter.steuerung import Steuerung, kurve_text, _kvp  # noqa: E402


def server(profile):
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), sim_ghl.Handler)
    srv.profile = profile
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class FakeApp:
    def __init__(self):
        self.pause = 0
        self.ended = 0
        self.active = False

    def maintenance_minutes(self): return 30
    def futterpause_minutes(self): return 15
    def wasserwechsel_minutes(self): return 60
    def start_pause(self, minutes): self.pause = minutes
    def ctl_active(self): return self.active
    def _end_maintenance(self, early=False): self.ended += 1
    def _publish_maintenance(self): pass


class FakeStore:
    def __init__(self):
        self.data = {"geraete": {"dev": {}}}
        self.lock = threading.RLock()

    def save(self): pass


class FakeSettings:
    store = FakeStore()

    def get(self, key):
        return {"art": None, "aktiv": True}


class FakeDev:
    def __init__(self, port, cfg, profile_scan=True, typ=None):
        self.uid, self.name, self.app = "dev", "Gerät", FakeApp()
        self.cfg = dict(cfg)
        if typ:
            self.cfg["typ"] = typ
        self.ghl = GhlClient("127.0.0.1", port, timeout=3)
        self.inv = Inventory(self.ghl, self.cfg)
        self.settings = FakeSettings()
        self.settings.store = FakeStore()
        self.notices = []
        self.force_scan = False
        self.wake = threading.Event()
        if profile_scan:
            self.inv.scan()
        self.items = self.inv.items(self.settings)
        self.steuerung = Steuerung(self)

    def notify(self, title, msg, urgent=False, kind=None):
        self.notices.append((title, msg))


def dev(profile, **cfg):
    srv, port = server(profile)
    sim_ghl.SENT.clear()
    sim_ghl.STATE.clear()
    d = FakeDev(port, cfg)
    d.srv = srv
    return d


class Client(unittest.TestCase):
    def test_set_ack_und_fehlercodes(self):
        srv, port = server("klein")
        c = GhlClient("127.0.0.1", port, timeout=3)
        self.assertTrue(c.set("SET SPECIALFUNCTION MAINTENANCE[0] 1"))
        with self.assertRaises(NackError) as cm:
            c.set("SET SPECIALFUNCTION MAINTENANCE[7] 1")
        self.assertEqual(cm.exception.code, -101)
        srv.shutdown()

    def test_nur_lesen(self):
        srv, port = server("klein_nur_lesen")
        c = GhlClient("127.0.0.1", port, timeout=3)
        with self.assertRaises(NackError) as cm:
            c.set("SET SPECIALFUNCTION MAINTENANCE[0] 1")
        self.assertEqual(cm.exception.code, -105)
        self.assertEqual(c.get("GET SENSOR[0] ACTVALUE"), "25.100")   # Lesen geht weiter
        srv.shutdown()


class Freigaben(unittest.TestCase):
    def test_ohne_freigabe_keine_steuerung(self):
        d = dev("voll")
        self.assertEqual(d.steuerung.controls(), [])
        self.assertFalse(d.steuerung.handle("master", "50"))
        self.assertEqual(sim_ghl.SENT, [])
        d.srv.shutdown()

    def test_nur_aktionen(self):
        d = dev("voll", steuerung=True)
        ids = {c["id"] for c in d.steuerung.controls()}
        self.assertIn("wartung_1", ids); self.assertIn("lichtszene_8", ids); self.assertIn("kh_aktion", ids)
        self.assertIn("ion_aktion_5", ids); self.assertIn("master", ids)
        self.assertFalse(any(i.startswith(("soll_", "kurve_", "name_")) for i in ids))   # dauerhafte Änderungen aus
        self.assertFalse(d.steuerung.handle("soll_sensor_0", "25.5"))
        d.srv.shutdown()

    def test_einstellungen_nur_mit_eigener_freigabe(self):
        d = dev("voll", einstellungen_schreiben=True)
        ids = {c["id"] for c in d.steuerung.controls()}
        self.assertIn("soll_sensor_0", ids); self.assertIn("soll_kh", ids); self.assertIn("kurve_0", ids)
        self.assertIn("name_sensor_0", ids); self.assertIn("name_timer_1", ids)
        self.assertFalse(any(i.startswith(("wartung", "master", "gewitter")) for i in ids))
        self.assertFalse(d.steuerung.handle("master", "50"))
        d.srv.shutdown()

    def test_namen_standardmaessig_in_ha_deaktiviert(self):
        d = dev("voll", einstellungen_schreiben=True)
        names = [c for c in d.steuerung.controls() if c["id"].startswith("name_")]
        self.assertTrue(names and all(not c["enabled"] for c in names))
        d.srv.shutdown()


class Aktionen(unittest.TestCase):
    def test_wartung_futterpause_wasserwechsel_koppeln_alarmpause(self):
        d = dev("voll", steuerung=True)
        self.assertTrue(d.steuerung.handle("wartung_1", "ON")); self.assertEqual(d.app.pause if hasattr(d, "app") else d.app.pause, 30)
        self.assertTrue(d.steuerung.handle("futterpause_2", "ON")); self.assertEqual(d.app.pause, 15)
        self.assertTrue(d.steuerung.handle("wasserwechsel_1", "ON")); self.assertEqual(d.app.pause, 60)
        self.assertEqual(sim_ghl.SENT[0], "SET SPECIALFUNCTION MAINTENANCE[0] 1")
        self.assertEqual(sim_ghl.SENT[1], "SET SPECIALFUNCTION FEEDPAUSE[1] 1")
        self.assertEqual(sim_ghl.SENT[2], "SET SPECIALFUNCTION WATERCHANGE[0] 1")
        d.app.active = True
        d.steuerung.handle("wartung_1", "OFF")
        self.assertEqual(d.app.ended, 0)                       # eine andere Funktion läuft noch
        d.app.active = False
        d.steuerung.handle("futterpause_2", "OFF")
        self.assertEqual(d.app.ended, 1)
        d.srv.shutdown()

    def test_lichtszene_exklusiv_und_ueberblendzeit(self):
        d = dev("voll", steuerung=True)
        d.steuerung.handle("lichtszene_fade", "30")
        d.steuerung.handle("lichtszene_1", "ON"); d.steuerung.handle("lichtszene_3", "ON")
        self.assertEqual(sim_ghl.SENT[-1], "SET SPECIALFUNCTION LIGHTSCENE[2] 1, 30")
        self.assertEqual(d.steuerung.state["lichtszene_1"], "OFF"); self.assertEqual(d.steuerung.state["lichtszene_3"], "ON")
        d.steuerung.handle("lichtszene_3", "OFF")
        self.assertEqual(sim_ghl.SENT[-1], "SET SPECIALFUNCTION LIGHTSCENE[2] 0, 30")
        d.srv.shutdown()

    def test_gewitter(self):
        d = dev("voll", steuerung=True)
        d.steuerung.handle("gewitter_dauer", "99")             # auf 60 begrenzt
        d.steuerung.handle("gewitter_start", "PRESS")
        d.steuerung.handle("gewitter_stop", "PRESS")
        self.assertEqual(sim_ghl.SENT, ["SET SPECIALFUNCTION THUNDERSTORM 60", "SET SPECIALFUNCTION THUNDERSTORM 0"])
        d.srv.shutdown()

    def test_master_helligkeit(self):
        d = dev("voll", steuerung=True)
        d.steuerung.handle("master_fade", "5")
        self.assertTrue(d.steuerung.handle("master", "70"))
        self.assertEqual(sim_ghl.SENT[-1], "SET ILLUMINATION MASTERBRIGHTNESS 70, 5")
        d.steuerung.handle("master", "250")                    # auf 100 begrenzt
        self.assertEqual(sim_ghl.SENT[-1], "SET ILLUMINATION MASTERBRIGHTNESS 100, 5")
        d.srv.shutdown()

    def test_kh_und_ion_aktionen(self):
        d = dev("voll", steuerung=True)
        self.assertTrue(d.steuerung.handle("kh_aktion", "Messung starten"))
        self.assertTrue(d.steuerung.handle("kh_aktion", "Messzelle leeren"))
        self.assertTrue(d.steuerung.handle("ion_aktion_4", "Sensortest"))
        self.assertEqual(sim_ghl.SENT, ["SET KHDIRECTOR STARTACTION 1", "SET KHDIRECTOR STARTACTION 4",
                                        "SET IONDIRECTOR[3] STARTACTION 4"])
        self.assertEqual(d.steuerung.state["kh_aktion"], "–")  # Auswahl springt zurück
        self.assertFalse(d.steuerung.handle("kh_aktion", "–"))
        d.srv.shutdown()

    def test_nur_lesen_meldet_einmal(self):
        d = dev("klein_nur_lesen", steuerung=True)
        self.assertFalse(d.steuerung.handle("wartung_1", "ON"))
        self.assertFalse(d.steuerung.handle("master", "50"))
        self.assertEqual(len(d.notices), 1); self.assertIn("Nur lesen", d.notices[0][1])
        self.assertEqual(d.app.pause, 0)
        d.srv.shutdown()

    def test_ablauf_setzt_anzeige_zurueck_ohne_befehl(self):
        d = dev("voll", steuerung=True)
        d.steuerung.until["wartung_1"] = 1.0; d.steuerung.state["wartung_1"] = "ON"
        sim_ghl.SENT.clear()
        self.assertTrue(d.steuerung.expire())
        self.assertEqual(d.steuerung.state["wartung_1"], "OFF"); self.assertEqual(sim_ghl.SENT, [])
        d.srv.shutdown()


class Einstellungen(unittest.TestCase):
    def test_sollwert_in_grenzen_und_sprungbegrenzung(self):
        d = dev("voll", einstellungen_schreiben=True)
        self.assertTrue(d.steuerung.handle("soll_sensor_0", "26.0"))       # Temperatur 25 -> 26: ok
        self.assertEqual(sim_ghl.SENT[-1], "SET SENSOR[0] DESVALUE 26")
        n = len(sim_ghl.SENT)
        self.assertFalse(d.steuerung.handle("soll_sensor_0", "34"))        # Sprung > 3 K
        self.assertFalse(d.steuerung.handle("soll_sensor_0", "50"))        # außerhalb 10–35 °C
        self.assertEqual(len(sim_ghl.SENT), n)                             # nichts gesendet
        self.assertEqual(len(d.notices), 2)
        d.srv.shutdown()

    def test_ph_kh_ion(self):
        d = dev("voll", einstellungen_schreiben=True)
        self.assertTrue(d.steuerung.handle("soll_sensor_1", "8.3"))        # pH 8.2 -> 8.3
        self.assertFalse(d.steuerung.handle("soll_sensor_1", "9.4"))       # Sprung > 0,5
        self.assertTrue(d.steuerung.handle("soll_kh", "8.5"))
        self.assertFalse(d.steuerung.handle("soll_kh", "15"))              # Sprung > 2 dKH
        self.assertTrue(d.steuerung.handle("soll_ion_2", "1400"))          # ±20 % von 1350
        self.assertFalse(d.steuerung.handle("soll_ion_2", "2500"))
        d.srv.shutdown()

    def test_lichtkurve_schreiben_und_lesen(self):
        d = dev("voll", einstellungen_schreiben=True)
        self.assertTrue(d.steuerung.handle("kurve_0", "06:30=0, 08:00=40, 12:00=100; 20:00=0"))
        self.assertEqual(d.steuerung.lese_kurve(0), [(390, 0.0), (480, 40.0), (720, 100.0), (1200, 0.0)])
        self.assertEqual(d.steuerung.state["kurve_0"], "06:30=0, 08:00=40, 12:00=100, 20:00=0")
        d.srv.shutdown()

    def test_lichtkurve_ungueltig_aendert_nichts(self):
        d = dev("voll", einstellungen_schreiben=True)
        vorher = d.steuerung.lese_kurve(0); sim_ghl.SENT.clear()
        for bad in ("", "abc", "08:00=40, 07:00=10", "25:00=10", "08:00=140", "08:00=40, 08:00=50",
                    ", ".join(f"{h:02d}:00={h}" for h in range(0, 24)) + ", 23:30=5"):
            self.assertFalse(d.steuerung.handle("kurve_0", bad), bad)
        self.assertEqual(d.steuerung.lese_kurve(0), vorher); self.assertEqual(sim_ghl.SENT, [])
        d.srv.shutdown()

    def test_lichtkurve_rollback_bei_fehler(self):
        d = dev("voll", einstellungen_schreiben=True)
        vorher = d.steuerung.lese_kurve(0)
        echt, n = d.ghl.set, {"i": 0}

        def kaputt(cmd, retries=2):
            n["i"] += 1
            if n["i"] == 4:                                    # mitten im Schreiben
                raise NackError("x", -101)
            return echt(cmd, retries)
        d.ghl.set = kaputt
        self.assertFalse(d.steuerung.handle("kurve_0", "06:00=10, 09:00=60, 18:00=60, 21:00=0"))
        d.ghl.set = echt
        self.assertEqual(d.steuerung.lese_kurve(0), vorher)    # alter Stand wiederhergestellt
        d.srv.shutdown()

    def test_namen(self):
        d = dev("voll", einstellungen_schreiben=True)
        self.assertTrue(d.steuerung.handle("name_sensor_0", "Becken A"))
        self.assertEqual(sim_ghl.SENT[-1], 'SET SENSOR[0] DESCRIPTION "Becken A"')
        self.assertTrue(d.force_scan)
        self.assertTrue(d.steuerung.handle("name_timer_1", "Pumpe 2"))
        for bad in ("Wasserwechsel zu lang!", "Röhre", 'a"b', ""):
            self.assertFalse(d.steuerung.handle("name_sensor_0", bad), bad)
        d.srv.shutdown()

    def test_refresh_liest_aktuelle_werte(self):
        d = dev("voll", steuerung=True, einstellungen_schreiben=True)
        d.steuerung.refresh(force=True)
        self.assertEqual(d.steuerung.state["master"], "80"); self.assertEqual(d.steuerung.state["soll_sensor_0"], "25")
        self.assertEqual(d.steuerung.state["soll_kh"], "8"); self.assertEqual(d.steuerung.state["name_sensor_0"], "Temperatur")
        self.assertIn("06:00=0", d.steuerung.state["kurve_0"])
        d.srv.shutdown()

    def test_kurve_parser(self):
        self.assertEqual(_kvp("06:30=0, 7:00=20%"), [(390, 0.0), (420, 20.0)])
        self.assertEqual(kurve_text([(390, 0.0), (420, 20.5)]), "06:30=0, 07:00=20.5")


class Mitras(unittest.TestCase):
    def test_erkennung_und_inventar(self):
        d = dev("mitras", steuerung=True, einstellungen_schreiben=True)
        self.assertEqual(d.inv.model, "Mitras")
        self.assertEqual([l["i"] for l in d.inv.lights], [0, 1, 2]); self.assertTrue(d.inv.heatsink)
        self.assertTrue(any(it["key"] == "heatsink" for it in d.items))
        d.srv.shutdown()

    def test_profilux_wird_nicht_als_mitras_erkannt(self):
        for prof in ("klein", "voll", "gross"):
            d = dev(prof); self.assertEqual(d.inv.model, "ProfiLux", prof); d.srv.shutdown()

    def test_typ_ueberschreiben(self):
        srv, port = server("voll")
        d = FakeDev(port, {}, typ="mitras")
        self.assertEqual(d.inv._detect_model(), "Mitras")
        srv.shutdown()

    def test_nur_passende_funktionen(self):
        d = dev("mitras", steuerung=True, einstellungen_schreiben=True)
        ids = {c["id"] for c in d.steuerung.controls()}
        self.assertIn("wartung_1", ids); self.assertIn("lichtszene_1", ids); self.assertIn("gewitter_start", ids)
        self.assertIn("kurve_0", ids)
        self.assertFalse(any(i.startswith(("futterpause", "wasserwechsel", "kh_", "ion_", "soll_", "name_")) for i in ids))
        self.assertTrue(d.steuerung.handle("lichtszene_2", "ON"))
        self.assertTrue(d.steuerung.handle("kurve_1", "07:00=0, 09:00=80, 19:00=80, 21:00=0"))
        d.srv.shutdown()


class Inventar(unittest.TestCase):
    def test_timer_nur_mit_schreibfreigabe(self):
        d = dev("voll"); self.assertEqual(d.inv.timers, []); d.srv.shutdown()
        d = dev("voll", einstellungen_schreiben=True)
        self.assertEqual([t["name"] for t in d.inv.timers], ["Licht an", "Pumpe"]); d.srv.shutdown()

    def test_geraeteuhr(self):
        srv, port = server("voll")
        c = GhlClient("127.0.0.1", port, timeout=3)
        import time
        self.assertLess(abs(float(c.get("GET SYSTEM UNIXTIME")) - time.time()), 5)
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
