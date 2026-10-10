"""data/airports.json aus OurAirports (gemeinfrei) erzeugen: Flughäfen mit
IATA-Code und Linienverkehr. Gebraucht für den Heimatflughafen (Position, Höhe,
METAR-Station, config.py) und den Abflughafen aus der Flugspur (origins.py).

    python3 tools/make_airports.py                 # lädt airports.csv von OurAirports
    python3 tools/make_airports.py airports.csv    # oder aus einer vorhandenen Datei

Nur bei Bedarf neu erzeugen; das Ergebnis liegt im Repo.
"""

import csv
import io
import json
import os
import sys
import urllib.request

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "airports.json")
M_PER_FT = 0.3048


def icao_code(r: dict) -> str:
    """Vierbuchstabiger ICAO-Code, sonst "" (manche Plätze haben nur lokale Kennungen wie "4A2")."""
    for key in ("icao_code", "gps_code", "ident"):
        code = (r.get(key) or "").strip().upper()
        if len(code) == 4 and code.isalpha():
            return code
    return ""


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            text = f.read()
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "flightpanel/1.0"})
        text = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        iata = (r.get("iata_code") or "").strip().upper()
        if len(iata) != 3 or not iata.isalpha():
            continue
        if r.get("scheduled_service") != "yes" and r.get("type") not in ("large_airport", "medium_airport"):
            continue
        try:
            lat, lon = round(float(r["latitude_deg"]), 4), round(float(r["longitude_deg"]), 4)
        except (KeyError, ValueError):
            continue
        try:
            elev = round(float(r["elevation_ft"]) * M_PER_FT)
        except (KeyError, ValueError):
            elev = None
        rows.append([iata, icao_code(r), lat, lon, elev])
    rows.sort()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"_comment": "IATA, ICAO (4 Buchstaben oder leer), Breite, Länge, Höhe in m (oder null). "
                               "Quelle: OurAirports (gemeinfrei), erzeugt mit tools/make_airports.py",
                   "airports": rows}, f, separators=(",", ":"))
        f.write("\n")
    print(f"{len(rows)} Flughäfen -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
