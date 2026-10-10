"""Was sich über einen Flug sagen lässt, wenn keine Airline-Route da ist,
und was in Zeile 2 steht.

- Kennzeichen: adsb.lol liefert es (r), sonst steckt es im Rufzeichen, wenn
  unter Kennzeichen geflogen wird: DEHGB -> D-EHGB, SPABC -> SP-ABC, N123AB.
- Klasse: aus der ADS-B-Kategorie (nur adsb.lol), sonst bei deutschen
  Kennzeichen aus dem Buchstaben nach "D-" (D-E einmotorig bis 2 t, D-H
  Hubschrauber ...).
- Betreiber ohne Linienflug (Luftrettung, Polizei, Luftwaffe) aus
  airlines.json mit "label", die Luftrettung mit Nummer: CHX31 -> Christoph 31.

Halternamen (adsbdb registered_owner, adsb.lol ownOp) kommen nie aufs Panel:
bei Privatflugzeugen sind das Privatpersonen.

Grundsatz für die Anzeige: nichts doppelt. Was schon in Zeile 1 oder 2 steht,
fällt aus den wechselnden Infoseiten; wo nichts Sinnvolles da ist, bleibt die
Zeile leer."""

import re

# Deutsche Kennzeichen, Buchstabe nach "D-" (LuftVZO). Kleinflugzeug = bis 5,7 t.
D_CLASS = {
    "A": "Großflugzeug", "B": "Großflugzeug", "C": "Geschäftsflugzeug",
    "E": "Kleinflugzeug", "F": "Kleinflugzeug", "G": "Kleinflugzeug", "I": "Kleinflugzeug",
    "H": "Hubschrauber", "K": "Motorsegler", "L": "Luftschiff", "M": "Ultraleicht", "O": "Ballon",
}
# ADS-B-Emitterkategorie
CATEGORY = {
    "A1": "Kleinflugzeug", "A2": "Geschäftsflugzeug", "A3": "Großflugzeug", "A4": "Großflugzeug",
    "A5": "Großflugzeug", "A6": "Großflugzeug", "A7": "Hubschrauber", "B1": "Segelflugzeug",
    "B2": "Ballon", "B4": "Ultraleicht", "B6": "Drohne",
}
# Länderpräfixe, deren Kennzeichen als Rufzeichen ohne Bindestrich gesendet werden:
# Präfix -> Buchstaben danach. Längere Präfixe zuerst prüfen.
REG_PREFIX = {
    "OE": 3, "HB": 3, "OK": 3, "OM": 3, "SP": 3, "PH": 3, "OO": 3, "OY": 3, "SE": 3, "LN": 3,
    "OH": 3, "EC": 3, "EI": 3, "HA": 3, "YR": 3, "LZ": 3, "LY": 3, "YL": 3, "ES": 3, "9A": 3,
    "S5": 3, "LX": 3, "CS": 3, "SX": 3, "D": 4, "G": 4, "F": 4, "I": 4,
}


def registration(callsign: str) -> str:
    """Kennzeichen, wenn das Rufzeichen eins ist, sonst "". Airline-Rufzeichen
    (drei Buchstaben, dann Ziffern) passen nie, weil der Rest nur aus Buchstaben besteht."""
    cs = (callsign or "").upper()
    if re.fullmatch(r"D\d{4}", cs):                       # Segelflugzeug D-1234
        return f"D-{cs[1:]}"
    for p, n in sorted(REG_PREFIX.items(), key=lambda kv: -len(kv[0])):
        rest = cs[len(p):]
        if cs.startswith(p) and len(rest) == n and rest.isalpha():
            return f"{p}-{rest}"
    if re.fullmatch(r"N[1-9]\d{0,4}[A-Z]{0,2}", cs):      # USA, ohne Bindestrich
        return cs
    return ""


def aircraft_class(category: str, reg: str) -> str:
    """Kleinflugzeug, Hubschrauber, ... aus ADS-B-Kategorie oder deutschem Kennzeichen."""
    if category in CATEGORY:
        return CATEGORY[category]
    if re.fullmatch(r"D-\d{4}", reg):
        return "Segelflugzeug"
    if re.fullmatch(r"D-[A-Z]{4}", reg):
        return D_CLASS.get(reg[2], "")
    return ""


def describe(callsign: str, category: str, reg_source: str, names: dict[str, dict]) -> tuple[str, str]:
    """(Name für Zeile 1, Bezeichnung für Zeile 2) eines Flugs ohne Airline.
    CHX31 -> ("Christoph 31", "Luftrettung"), BPOBER -> ("Bundespolizei", "Polizei"),
    DEHGB -> ("D-EHGB", "Kleinflugzeug"), BFX1073 -> ("BFX1073", "Kleinflugzeug")."""
    cs = callsign or ""
    reg_cs = registration(cs)
    cls = aircraft_class(category, reg_source or reg_cs)
    op = names.get(cs[:3])
    if op and not op.get("airline", True):
        name = op["name"]
        if op.get("number") and cs[3:].isdigit():
            name = f"{name} {int(cs[3:])}"
        return name, op.get("label") or cls
    return reg_cs or cs, cls


def second_line(origin: str, dest: str, status: str, label: str, local: str) -> str:
    """Zeile 2: Route, sonst was der Flug ist, sonst bei Landung oder Start am
    eigenen Platz die bekannte Hälfte (?->BER, BER->?), sonst nichts."""
    if origin or dest:
        return f"{origin or '?'}→{dest or '?'}"
    if label:
        return label
    if status == "ARR":
        return f"?→{local}"
    if status == "DEP":
        return f"{local}→?"
    return ""


def shown_elsewhere(callsign: str, *texts: str) -> bool:
    """Steht das Rufzeichen schon da (auch als Kennzeichen mit Bindestrich)?
    Dann fällt es aus den wechselnden Infoseiten."""
    cs = (callsign or "").replace("-", "")
    return bool(cs) and any(cs in (t or "").replace("-", "").replace(" ", "") for t in texts)
