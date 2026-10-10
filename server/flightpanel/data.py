"""Mitgelieferte Daten aus data/ (DATA_DIR):

  airlines.json  Betreiber: ICAO-Code -> Anzeigename, Logo-Code, Airline oder nicht
  airports.json  Flughäfen mit Linienverkehr: IATA, ICAO, Position (OurAirports)
  rare.json      seltene Typen und Betreiber für die Karte "rare"
  jokes.json     Witze, Verspätungsgründe, Sprüche für die Spaß-Karten

Fehlt eine Datei, fehlen nur die Funktionen, die sie brauchen."""

import os
from typing import NamedTuple

from .storage import read_json


class Airport(NamedTuple):
    iata: str
    icao: str
    lat: float
    lon: float


def load_airlines(data_dir: str) -> dict[str, dict]:
    """{"DLH": {"name": "Lufthansa", "iata": "LH", "airline": True, "label": "", "number": False}}.

    name: kurzer Anzeigename statt des langen Firmennamens bei adsbdb ("Deutsche
    Lufthansa AG"). iata: Logo-Code, die Marke statt des Tochterbetriebs (Malta Air
    -> FR); leer, wenn der IATA-Code doppelt vergeben ist, dann Logo per ICAO.
    airline false: Rettung, Polizei, Militär, mit label (Luftrettung) und number
    (CHX31 -> Christoph 31)."""
    raw = read_json(os.path.join(data_dir, "airlines.json")) or {}
    return {code.upper(): {"name": v.get("name", ""), "iata": (v.get("iata") or "").upper(),
                           "airline": bool(v.get("airline", True)), "label": v.get("label", ""),
                           "number": bool(v.get("number", False))}
            for code, v in raw.items() if not code.startswith("_")}


def load_airports(data_dir: str) -> list[Airport]:
    raw = read_json(os.path.join(data_dir, "airports.json")) or {}
    out = []
    for r in raw.get("airports", []):
        try:
            out.append(Airport(r[0], r[1], float(r[2]), float(r[3])))
        except (IndexError, TypeError, ValueError):
            continue
    return out


def load_rare(data_dir: str) -> dict[str, dict[str, str]]:
    """{"types": {"A388": "A380"}, "callsigns": {"GAF": "Luftwaffe"}}."""
    raw = read_json(os.path.join(data_dir, "rare.json")) or {}
    return {part: {k.upper(): v for k, v in raw.get(part, {}).items() if not k.startswith("_")}
            for part in ("types", "callsigns")}


def load_fun(data_dir: str, airport: str) -> dict[str, list]:
    """Witze, Verspätungsgründe und Sprüche. Die Datei gehört zu einem Flughafen
    ("airport": "BER"); woanders bleibt sie ungenutzt, bis man eine eigene schreibt."""
    raw = read_json(os.path.join(data_dir, "jokes.json")) or {}
    if raw.get("airport") and raw["airport"].upper() != airport:
        return {}
    return {k: raw.get(k, []) for k in ("jokes", "delays", "plane")}
