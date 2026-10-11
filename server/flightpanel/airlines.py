"""Wer fliegt: Betreiber aus dem Callsign, Airline oder nicht, kurze Anzeigenamen,
und ob eine Route aus einer Datenbank zur Position des Flugzeugs passt.

names ist data/airlines.json (data.load_airlines): ICAO-Code -> Name, Logo-Code,
Airline oder nicht."""

from .geo import haversine_km
from .issues import Issues, names_match
from .model import State

# ADS-B-Kategorien, die nie eine Airline sind: leicht (A1), Hubschrauber (A7),
# Segler, Ballon, Fallschirm, Ultraleicht, Drohne, Raumfahrt (B1-B7)
NON_AIRLINE_CATEGORIES = {"A1", "A7", "B1", "B2", "B3", "B4", "B6", "B7"}
# Groß und schwer (A320/737 aufwärts); Businessjets sind A1/A2
AIRLINER_CATEGORIES = {"A3", "A4", "A5", "A6"}
# Landen nicht auf der Piste: Hubschrauber, Segler, Ballon, Fallschirm, UL, Drohne, Raumfahrt
NOT_RUNWAY_CATEGORIES = {"A7", "B1", "B2", "B3", "B4", "B6", "B7"}

# Fällt aus langen Firmennamen weg: "Deutsche Lufthansa AG" -> "Lufthansa"
_LEGAL_WORDS = {"AG", "GMBH", "LTD", "LIMITED", "PLC", "SA", "S.A.", "INC", "LLC", "CO", "COMPANY",
                "AIRLINES", "AIRLINE", "AIRWAYS", "DEUTSCHE", "THE", "GROUP", "EUROPE", "UK"}


def callsign_airline(callsign: str) -> str:
    """ICAO-Betreibercode aus dem Callsign: drei Buchstaben, dann eine Ziffer
    (EZY45KZ, DLH2041). Kennzeichen wie DEABC oder N123AB sind keine Airline."""
    cs = callsign or ""
    if len(cs) >= 4 and cs[:3].isalpha() and cs[3].isdigit():
        return cs[:3]
    return ""


def is_airline(callsign: str, route: dict, names: dict[str, dict], category: str = "") -> bool:
    """Linien-, Charter- und Frachtflüge. Private Flüge haben kein Airline-Callsign
    (DEABC), Rettung, Polizei und Militär stehen in airlines.json mit
    "airline": false (CHX31 = Christoph 31), Hubschrauber und Leichtflugzeuge
    erkennt adsb.lol an der Kategorie. Unbekannte Betreiber zählen, wenn adsbdb
    eine Route mit Airline kennt oder das Flugzeug groß ist (SVI8222, ein
    ägyptischer Charter, A3); BFX1073, ein Businessjet ohne Route, zählt nicht."""
    code = callsign_airline(callsign)
    if not code or category in NON_AIRLINE_CATEGORIES:
        return False
    if code in names:
        return names[code]["airline"]
    return bool(route.get("airline_icao")) or category in AIRLINER_CATEGORIES


def short_airline(name: str, icao: str, names: dict[str, dict]) -> str:
    """Anzeigename: aus airlines.json, sonst der eingedampfte Firmenname."""
    if icao in names and names[icao]["name"]:
        return names[icao]["name"]
    if not name:
        return ""
    words = [w for w in name.replace(",", " ").split() if w.upper().strip(".") not in _LEGAL_WORDS]
    short = " ".join(words) if words else name
    return short[:12]


def route_plausible(route: dict, lat: float, lon: float) -> bool:
    """Routendatenbanken kennen Strecken pro Callsign, und Airlines verwenden
    Callsigns für andere Strecken weiter. Liegt das Flugzeug weit neben der
    Strecke (Umweg über seine Position > 20 % + 100 km), ist die Route veraltet.
    Von X nach X sagt nichts darüber, woher es kommt. Ohne Koordinaten (alte
    Cache-Einträge) gilt sie als plausibel."""
    if route.get("origin") and route.get("origin") == route.get("dest"):
        return False
    o, d = route.get("origin_pos"), route.get("dest_pos")
    if not (o and d):
        return True
    direct = haversine_km(o[0], o[1], d[0], d[1])
    via = haversine_km(o[0], o[1], lat, lon) + haversine_km(lat, lon, d[0], d[1])
    return via <= direct * 1.2 + 100


def note_operator(issues: Issues, s: State, route: dict, names: dict[str, dict], airline: bool) -> None:
    """Unbekannte Betreiber und Namen, die nicht zu adsbdb passen: Kandidaten fürs
    Nachpflegen von airlines.json (GET /issues)."""
    code = callsign_airline(s.callsign)
    if not code:
        return
    if code not in names:
        issues.note("unknown_operator", code, s.callsign, adsbdb=route.get("airline", ""),
                    category=s.category, type=s.actype, counted_as="airline" if airline else "other")
    elif (route.get("airline") and route.get("airline_icao") == code
          and route["airline"] != names[code].get("adsbdb")       # geprüft, siehe data.load_airlines
          and not names_match(names[code]["name"], route["airline"])):
        issues.note("name_mismatch", code, s.callsign, ours=names[code]["name"], adsbdb=route["airline"])
