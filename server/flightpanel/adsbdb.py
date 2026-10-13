"""adsbdb.com, kostenlos und ohne Account: Route und Airline zum Callsign,
Flugzeugtyp zur icao24, Betreibername zum ICAO-Code. Antworten im Cache (cache.py),
Routen 24 h, Flugzeuge und Betreiber 30 Tage.

Die Routen sind pro Callsign gespeichert und oft alt: Airlines verwenden Callsigns
für andere Strecken weiter. build.py prüft sie gegen die Position und fragt
adsb.im zuerst."""

import logging
import re

import aiohttp

from .cache import Cache
from .issues import Issues

log = logging.getLogger("adsbdb")

ROUTE_TTL = 24 * 3600
AIRCRAFT_TTL = 30 * 24 * 3600
NEGATIVE_TTL = 6 * 3600

# Werte kommen ungeprüft aus dem Netz und landen im URL-Pfad: nur gültige Formen durchlassen.
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")
ICAO24_RE = re.compile(r"^[0-9a-f]{6}$")
OPERATOR_RE = re.compile(r"^[A-Z]{3}$")


def _pos(airport: dict) -> list[float] | None:
    """[lat, lon] eines Flughafens aus der adsbdb-Antwort, für die Plausibilitätsprüfung."""
    try:
        return [float(airport["latitude"]), float(airport["longitude"])]
    except (KeyError, TypeError, ValueError):
        return None


class AdsbDb:
    def __init__(self, session: aiohttp.ClientSession, base: str, cache: Cache, issues: Issues | None = None):
        self.session = session
        self.base = base.rstrip("/")
        self.cache = cache
        self.issues = issues
        self.last_error: str | None = None

    async def _get(self, path: str) -> dict | list | None:
        """Das Feld "response": ein Objekt, bei /airline eine Liste; None bei 404."""
        try:
            async with self.session.get(f"{self.base}/{path}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                j = await r.json()
            self.last_error = None
            resp = j.get("response")
            return resp if isinstance(resp, dict | list) else None
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            log.warning("adsbdb %s: %s", path, self.last_error)
            if self.issues is not None:
                self.issues.source_error("adsbdb", self.last_error, path=path)
            raise

    async def route(self, callsign: str) -> dict:
        """{"origin": "LGW", "dest": "BER", "origin_pos": [lat, lon], "dest_pos": [lat, lon],
        "airline": "easyJet", "airline_icao": "EZY", "airline_iata": "U2"} oder {}."""
        if not CALLSIGN_RE.match(callsign or ""):
            return {}
        key = f"route:{callsign}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        try:
            resp = await self._get(f"callsign/{callsign}")
        except Exception:
            return {}
        out: dict = {}
        fr = (resp if isinstance(resp, dict) else {}).get("flightroute") or {}
        if fr:
            o, d, al = fr.get("origin") or {}, fr.get("destination") or {}, fr.get("airline") or {}
            out = {
                "origin": (o.get("iata_code") or "").upper(),
                "dest": (d.get("iata_code") or "").upper(),
                "origin_pos": _pos(o),
                "dest_pos": _pos(d),
                "airline": al.get("name") or "",
                "airline_icao": (al.get("icao") or "").upper(),
                "airline_iata": (al.get("iata") or "").upper(),
            }
        self.cache.set(key, out, ROUTE_TTL if out else NEGATIVE_TTL)
        return out

    async def aircraft(self, icao24: str) -> dict:
        """{"type": "A320", "type_long": "Airbus A320-214", "operator": ..., "reg": ...} oder {}.
        operator ist der Halter: bei Privatflugzeugen eine Person, nie anzeigen."""
        if not ICAO24_RE.match(icao24 or ""):
            return {}
        key = f"ac:{icao24}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        try:
            resp = await self._get(f"aircraft/{icao24}")
        except Exception:
            return {}
        out: dict = {}
        ac = (resp if isinstance(resp, dict) else {}).get("aircraft") or {}
        if ac:
            out = {
                "type": (ac.get("icao_type") or "").upper(),
                "type_long": ac.get("type") or "",
                "operator": ac.get("registered_owner") or "",
                "reg": ac.get("registration") or "",
            }
        self.cache.set(key, out, AIRCRAFT_TTL if out else NEGATIVE_TTL)
        return out

    async def airline(self, icao: str) -> str:
        """Betreibername zum ICAO-Code ("AWH" -> "Aerowest GmbH") oder "": für Flüge, deren
        Callsign adsbdb nicht kennt. Die ICAO vergibt die Codes an Firmen und Behörden, nie
        an Privatleute; ein Halter kommt hier also nicht heraus."""
        code = (icao or "").upper()
        if not OPERATOR_RE.match(code):
            return ""
        key = f"operator:{code}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        try:
            resp = await self._get(f"airline/{code}")
        except Exception:
            return ""
        rows = resp if isinstance(resp, list) else []
        name = next((str(r["name"]).strip() for r in rows if isinstance(r, dict) and r.get("name")), "")
        self.cache.set(key, name, AIRCRAFT_TTL if name else NEGATIVE_TTL)
        return name
