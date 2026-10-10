"""Routen von adsb.im, dem Routendienst hinter den tar1090-Karten (auch adsb.lol).

Aktueller als adsbdb, das Callsigns für wechselnde Strecken weiterverwendet:
EWG55E stand bei adsbdb als London-Düsseldorf, adsb.im kannte Palma-Berlin;
THY5MH Istanbul-Van statt Berlin-Istanbul. OpenSky taugt dafür nicht, dessen
Routendienst ist seit 2023 nicht mehr gepflegt.

Eine Abfrage für mehrere Flüge mit Position, adsb.im prüft die Plausibilität
selbst mit. Pro Callsign 30 min im Cache (Strecken wechseln von Tag zu Tag,
nicht im Flug), also etwa ein Abruf pro neuem Flug.

    POST https://adsb.im/api/0/routeset  {"planes": [{"callsign", "lat", "lng"}]}
    -> [{"callsign", "_airport_codes_iata": "PMI-BER", "_airports": [{iata, lat, lon}, ...],
         "airline_code": "EWG", "plausible": true}]       unbekannt: "unknown"
"""

import logging

import aiohttp

from .adsbdb import Cache

log = logging.getLogger("adsbim")

URL = "https://adsb.im/api/0/routeset"
TTL = 30 * 60
MAX_PER_REQUEST = 20


def parse(r: dict) -> dict:
    """Antwort für einen Flug -> {"origin", "dest", "origin_pos", "dest_pos"} wie
    bei adsbdb, oder {} wenn unbekannt oder unplausibel. Bei Zwischenlandungen
    (A-B-C) zählen erste und letzte Station."""
    if not r.get("plausible"):
        return {}
    airports = [a for a in r.get("_airports") or [] if a.get("iata")]
    if len(airports) < 2:
        return {}
    o, d = airports[0], airports[-1]
    try:
        return {"origin": str(o["iata"]).upper(), "dest": str(d["iata"]).upper(),
                "origin_pos": [float(o["lat"]), float(o["lon"])], "dest_pos": [float(d["lat"]), float(d["lon"])]}
    except (KeyError, TypeError, ValueError):
        return {}


class AdsbIm:
    def __init__(self, session: aiohttp.ClientSession, cache: Cache):
        self.session = session
        self.cache = cache
        self.last_error: str | None = None
        self.issues = None      # Issues, setzt main

    async def routes(self, planes: list[tuple[str, float, float]]) -> dict[str, dict]:
        """(callsign, lat, lon) -> {callsign: Route oder {}}. Fehler: Flug fehlt im
        Ergebnis und wird beim nächsten Mal wieder gefragt."""
        out: dict[str, dict] = {}
        ask = []
        for cs, lat, lon in planes:
            if not cs:
                continue
            hit = self.cache.get(f"im:{cs}")
            if hit is not None:
                out[cs] = hit
            else:
                ask.append({"callsign": cs, "lat": round(lat, 3), "lng": round(lon, 3)})
        for i in range(0, len(ask), MAX_PER_REQUEST):
            try:
                async with self.session.post(URL, json={"planes": ask[i:i + MAX_PER_REQUEST]},
                                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    resp = await r.json(content_type=None)
                self.last_error = None
            except Exception as e:
                self.last_error = str(e) or type(e).__name__
                log.warning("adsb.im: %s", self.last_error)
                if self.issues is not None:
                    self.issues.note("rate_limit" if "429" in self.last_error else "source_error", "adsbim",
                                     error=self.last_error)
                continue
            for item in resp if isinstance(resp, list) else []:
                cs = str(item.get("callsign") or "").strip().upper()
                if cs:
                    route = parse(item)
                    self.cache.set(f"im:{cs}", route, TTL)
                    out[cs] = route
        return out
