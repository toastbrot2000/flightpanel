"""adsbdb.com: Route aus Callsign, Flugzeugdaten aus icao24. Mit TTL-Cache,
der stündlich als JSON auf die Platte geschrieben wird."""

import json
import logging
import os
import time

import aiohttp

log = logging.getLogger("adsbdb")

ROUTE_TTL = 24 * 3600
AIRCRAFT_TTL = 30 * 24 * 3600
NEGATIVE_TTL = 6 * 3600


class Cache:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, tuple[float, object]] = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            self.data = {k: (exp, v) for k, (exp, v) in raw.items() if exp > now}
            log.info("Cache geladen: %d Einträge", len(self.data))
        except FileNotFoundError:
            pass
        except Exception as e:  # defekte Datei soll den Start nicht verhindern
            log.warning("Cache nicht lesbar (%s), starte leer", e)

    def save(self) -> None:
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)
        self.dirty = False

    def get(self, key: str):
        item = self.data.get(key)
        if item is None:
            return None
        exp, v = item
        if exp < time.time():
            del self.data[key]
            return None
        return v

    def set(self, key: str, value, ttl: int) -> None:
        self.data[key] = (time.time() + ttl, value)
        self.dirty = True

    def __len__(self) -> int:
        return len(self.data)


class AdsbDb:
    def __init__(self, session: aiohttp.ClientSession, base: str, cache: Cache):
        self.session = session
        self.base = base.rstrip("/")
        self.cache = cache
        self.last_error: str | None = None

    async def _get(self, path: str) -> dict | None:
        try:
            async with self.session.get(f"{self.base}/{path}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                j = await r.json()
            self.last_error = None
            resp = j.get("response")
            return resp if isinstance(resp, dict) else None
        except Exception as e:
            self.last_error = str(e)
            log.warning("adsbdb %s: %s", path, e)
            raise

    async def route(self, callsign: str) -> dict:
        """{'origin': 'LGW', 'dest': 'BER', 'airline': 'easyJet', 'airline_icao': 'EZY', 'airline_iata': 'U2'} oder {}."""
        if not callsign:
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
        fr = (resp or {}).get("flightroute") or {}
        if fr:
            o, d, al = fr.get("origin") or {}, fr.get("destination") or {}, fr.get("airline") or {}
            out = {
                "origin": (o.get("iata_code") or "").upper(),
                "dest": (d.get("iata_code") or "").upper(),
                "airline": al.get("name") or "",
                "airline_icao": (al.get("icao") or "").upper(),
                "airline_iata": (al.get("iata") or "").upper(),
            }
        self.cache.set(key, out, ROUTE_TTL if out else NEGATIVE_TTL)
        return out

    async def aircraft(self, icao24: str) -> dict:
        """{'type': 'A320', 'type_long': 'Airbus A320-214', 'operator': 'easyJet Europe'} oder {}."""
        if not icao24:
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
        ac = (resp or {}).get("aircraft") or {}
        if ac:
            out = {
                "type": (ac.get("icao_type") or "").upper(),
                "type_long": ac.get("type") or "",
                "operator": ac.get("registered_owner") or "",
                "reg": ac.get("registration") or "",
            }
        self.cache.set(key, out, AIRCRAFT_TTL if out else NEGATIVE_TTL)
        return out
