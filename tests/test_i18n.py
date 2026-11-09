"""Prüft die Übersetzungen:  python3 tests/test_i18n.py  (aus dem Ordner aquarium_waechter)"""
import ast
import glob
import re
import string
import sys

sys.path.insert(0, ".")
from waechter import bridge, dashboard, extern, ghl, report, settings  # noqa: E402
from waechter.translations import T  # noqa: E402

used = []
for f in sorted(glob.glob("waechter/*.py")):
    for node in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            used.append(node.args[0].value)
options = (list(ghl.CATEGORY_NAMES.values()) + list(settings.LEVEL_MODES) + list(ghl.SENSOR_ARTS)
           + [e for v in ghl.SENSOR_ARTS.values() for _u, e, _n, _d in v if e] + list(ghl.ART_SHORT.values())
           + [ghl.NO_STRIP] + list(ghl.STRIP_TYPES) + list(report.OPTIONS) + [extern.OFF]
           + list(bridge.Bridge.LABELS.values()) + list(dashboard.SHORT.values()) + [dashboard.HELP])
errors = []


def fields(s):
    return sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)


for de in set(used + options):
    if de not in T:
        errors.append(f"fehlt: {de!r}")
        continue
    for lang, tr in T[de].items():
        if fields(tr) != fields(de):
            errors.append(f"Platzhalter {lang}: {de!r} -> {tr!r}")
# Auswahlwerte müssen je Sprache eindeutig zurückübersetzbar sein
selects = list(settings.LEVEL_MODES) + list(ghl.SENSOR_ARTS) + [ghl.NO_STRIP] + list(ghl.STRIP_TYPES) \
    + list(report.OPTIONS) + [extern.OFF]
for lang in ("en", "fr", "es", "it"):
    for de in selects:
        shown = T[de][lang]
        others = [k for k, v in T.items() if v.get(lang) == shown and k != de]
        if others:
            errors.append(f"mehrdeutig {lang}: {shown!r} = {de!r} und {others}")
unused = [k for k in T if k not in used and k not in options]
print(f"{len(set(used + options))} Texte, {len(T)} Übersetzungen, ungenutzt: {unused}")
print("\n".join(errors) or "OK")
sys.exit(1 if errors else 0)
