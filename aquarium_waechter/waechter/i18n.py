"""Sprachen der Oberfläche: Deutsch (Quelle), English, Français, Español, Italiano.

Im Code steht der deutsche Text: _("Steckdose {n}", n=3). Für en/fr wird er in
translations.py nachgeschlagen; fehlt eine Übersetzung, bleibt es deutsch.
Gespeichert (Einstellungen, Auswahlwerte) wird immer der deutsche Text – so bleibt
alles erhalten, wenn die Sprache wechselt. Auswahlfelder: anzeigen mit _(), was
HA zurückschickt, mit back() wieder auf Deutsch.
"""

from .translations import T

LANGS = ("de", "en", "fr", "es", "it")
LANG = "de"


def set_lang(lang):
    global LANG
    lang = (lang or "").lower()[:2]
    LANG = lang if lang in LANGS else "en"
    return LANG


def _(text, **kw):
    if LANG != "de":
        text = T.get(text, {}).get(LANG) or text
    return text.format(**kw) if kw else text


def back(shown):
    """Angezeigten (übersetzten) Text zurück auf den deutschen Schlüssel."""
    if LANG == "de":
        return shown
    for de, tr in T.items():
        if tr.get(LANG) == shown:
            return de
    return shown


def dec():
    """Dezimaltrennzeichen."""
    return "." if LANG == "en" else ","
