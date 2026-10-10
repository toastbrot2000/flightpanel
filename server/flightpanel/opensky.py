"""OpenSky-Client: OAuth2 Client Credentials, Bounding-Box-Abfrage, Credits."""

import logging
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("opensky")

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
STATES_URL = "https://opensky-network.org/api/states/all"


@dataclass
class State:
    icao24: str
    callsign: str
    lon: float
    lat: float
    baro_alt_m: float | None
    on_ground: bool
    velocity_ms: float | None
    track_deg: float | None
    vrate_ms: float | None


class OpenSky:
    def __init__(self, session: aiohttp.ClientSession, client_id: str, client_secret: str):
        self.session = session
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_exp: float = 0
        self.credits_remaining: int | None = None
        self.last_ok: float = 0
        self.last_error: str | None = None
        self.backoff_until: float = 0

    @property
    def anonymous(self) -> bool:
        return not (self.client_id and self.client_secret)

    async def _ensure_token(self) -> None:
        if self.anonymous:
            return
        if self._token and time.time() < self._token_exp - 300:
            return
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with self.session.post(TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            j = await r.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 1800))
        log.info("OpenSky token erneuert, gültig %ss", j.get("expires_in"))

    async def fetch(self, bbox: tuple[float, float, float, float]) -> list[State]:
        """Liefert die Flugzeuge in der Box. Wirft bei Fehlern, damit der Aufrufer
        den letzten Stand behalten kann."""
        if time.time() < self.backoff_until:
            raise RuntimeError("OpenSky im Backoff")
        await self._ensure_token()
        lamin, lomin, lamax, lomax = bbox
        params = {"lamin": f"{lamin:.4f}", "lomin": f"{lomin:.4f}", "lamax": f"{lamax:.4f}", "lomax": f"{lomax:.4f}"}
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        async with self.session.get(STATES_URL, params=params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
            rem = r.headers.get("X-Rate-Limit-Remaining")
            if rem is not None:
                try:
                    self.credits_remaining = int(rem)
                except ValueError:
                    pass
            if r.status == 429:
                self.backoff_until = time.time() + 300
                self.last_error = "429 rate limited"
                raise RuntimeError("OpenSky 429, 5 min Pause")
            if r.status == 401 and not self.anonymous:
                self._token = None  # beim nächsten Mal neu holen
            r.raise_for_status()
            j = await r.json()
        self.last_ok = time.time()
        self.last_error = None
        out: list[State] = []
        for s in j.get("states") or []:
            try:
                if s[5] is None or s[6] is None:
                    continue
                out.append(State(
                    icao24=str(s[0]).lower(),
                    callsign=(s[1] or "").strip().upper(),
                    lon=float(s[5]), lat=float(s[6]),
                    baro_alt_m=float(s[7]) if s[7] is not None else None,
                    on_ground=bool(s[8]),
                    velocity_ms=float(s[9]) if s[9] is not None else None,
                    track_deg=float(s[10]) if s[10] is not None else None,
                    vrate_ms=float(s[11]) if s[11] is not None else None,
                ))
            except (IndexError, TypeError, ValueError):
                continue
        return out
