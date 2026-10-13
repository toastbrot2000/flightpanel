"""Mitgelieferte Daten aus data/ (DATA_DIR):

  airlines.json  Betreiber: ICAO-Code -> Anzeigename, Logo-Code, Airline oder nicht
  airports.json  Flughäfen mit Linienverkehr: IATA, ICAO, Position (OurAirports)
  rare.json      seltene Typen und Betreiber für die Karte "rare"

Fehlt eine Datei, fehlen nur die Funktionen, die sie brauchen."""

import os
from typing import NamedTuple

from .storage import read_json


class Airport(NamedTuple):
    iata: str
    icao: str               # vier Buchstaben oder leer
    lat: float
    lon: float
    elev_m: float | None = None


def load_airlines(data_dir: str) -> dict[str, dict]:
    """{"DLH": {"name": "Lufthansa", "iata": "LH", "airline": True, "label": "", "number": False}}.

    name: kurzer Anzeigename statt des langen Firmennamens bei adsbdb ("Deutsche
    Lufthansa AG"). iata: Logo-Code, die Marke statt des Tochterbetriebs (Malta Air
    -> FR); leer, wenn der IATA-Code doppelt vergeben ist, dann Logo per ICAO.
    airline false: Rettung, Polizei, Militär, mit label (Luftrettung) und number
    (CHX31 -> Christoph 31). adsbdb: der Name, unter dem adsbdb den Code führt, wenn
    er veraltet ist oder anders lautet; geprüft, /issues meldet ihn nicht mehr. album false:
    Kleinbetrieb (CPH AirTaxi), Name und Logo ja, im Sammelalbum aber nur ein Exot."""
    raw = read_json(os.path.join(data_dir, "airlines.json")) or {}
    return {code.upper(): {"name": v.get("name", ""), "iata": (v.get("iata") or "").upper(),
                           "airline": bool(v.get("airline", True)), "label": v.get("label", ""),
                           "number": bool(v.get("number", False)), "adsbdb": v.get("adsbdb", ""),
                           "album": bool(v.get("album", True))}
            for code, v in raw.items() if not code.startswith("_")}


def load_airports(data_dir: str) -> list[Airport]:
    raw = read_json(os.path.join(data_dir, "airports.json")) or {}
    out = []
    for r in raw.get("airports", []):
        try:
            elev = float(r[4]) if len(r) > 4 and r[4] is not None else None
            out.append(Airport(r[0], r[1], float(r[2]), float(r[3]), elev))
        except (IndexError, TypeError, ValueError):
            continue
    return out


GOV_LABELS = ("Militär", "Regierungsflug")      # Betreiber aus airlines.json, die als "Hoher Besuch" gelten


def load_rare(data_dir: str, names: dict[str, dict] | None = None) -> dict[str, dict[str, str]]:
    """{"types": {"A388": "A380"}, "callsigns": {"GAF": "Luftwaffe"}}. Callsigns: jeder
    Betreiber aus airlines.json mit label Militär oder Regierungsflug (unter seinem
    Namen), dazu die Callsign-Anfänge aus rare.json ohne eigenen Eintrag dort (AF1, SAM)."""
    raw = read_json(os.path.join(data_dir, "rare.json")) or {}
    out = {part: {k.upper(): v for k, v in raw.get(part, {}).items() if not k.startswith("_")}
           for part in ("types", "callsigns")}
    gov = {code: v["name"] for code, v in (names or {}).items() if not v["airline"] and v["label"] in GOV_LABELS}
    out["callsigns"] = gov | out["callsigns"]
    return out


def rare_match(rare: dict[str, dict[str, str]], callsign: str, actype: str) -> tuple[str, str]:
    """(Bezeichnung, "type" | "gov") für einen seltenen Typ oder Betreiber, sonst ("", "")."""
    if actype and actype in rare["types"]:
        return rare["types"][actype], "type"
    cs = callsign or ""
    for prefix, label in rare["callsigns"].items():
        if cs.startswith(prefix):
            return label, "gov"
    return "", ""
