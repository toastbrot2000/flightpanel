"""Seed: Logos für alle Airlines aus data/airlines.json auf einmal holen,
damit beim ersten Flug nichts fehlt. Danach holt der Server Unbekanntes
selbst nach, sobald es auftaucht.

    python3 -m flightpanel.fetch_logos            # alle aus airlines.json
    python3 -m flightpanel.fetch_logos LH U2 FR   # nur diese Codes (IATA oder ICAO)
    python3 -m flightpanel.fetch_logos --force    # vorhandene überschreiben

Im Container: docker compose exec flightpanel python3 -m flightpanel.fetch_logos
"""

import asyncio
import logging
import os
import sys

import aiohttp

from .config import CFG
from .logos import make_logo_store
from .model import load_airlines


async def run(codes: list[tuple[str, str]], force: bool) -> int:
    """codes: (IATA, ICAO)-Paare, eins von beiden darf leer sein."""
    store = make_logo_store(CFG)
    store.fetch_enabled = True
    ok = skipped = 0
    failed: list[str] = []
    async with aiohttp.ClientSession(headers={"User-Agent": "flightpanel/1.0"}) as session:
        store.session = session
        for iata, icao in codes:
            code = store.key(iata, icao)
            if not force and (store.path_for(iata) or store.path_for(icao)):
                skipped += 1
                continue
            if force:
                for c in (iata, icao):
                    p = store.path_for(c)
                    if p:
                        os.remove(p)
            if await store.fetch_now(iata, icao):
                ok += 1
            else:
                failed.append(code)
            await asyncio.sleep(0.3)   # freundlich zum CDN
    print(f"geholt: {ok}, schon da: {skipped}, fehlgeschlagen: {len(failed)}")
    if failed:
        print("fehlgeschlagen:", " ".join(failed))
        print("-> Kein quadratisches Logo (nur Schriftzug) oder Netzfehler. Von Hand ablegen:")
        print("   data/logos/<IATA>.png, tools/make_logo.py hilft. Bis dahin: Layout A ohne Logo.")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    airlines = load_airlines(CFG.data_dir)
    if args:
        by_iata = {v["iata"]: k for k, v in airlines.items() if v["iata"]}
        codes = []
        for a in (a.upper() for a in args):
            if len(a) == 3 and a.isalpha():
                codes.append((airlines.get(a, {}).get("iata", ""), a))
            else:
                codes.append((a, by_iata.get(a, "")))
    else:
        codes = sorted({(v["iata"], k) for k, v in airlines.items() if v["airline"]})
    print(f"{len(codes)} Airlines: {' '.join(i or c for i, c in codes)}")
    sys.exit(asyncio.run(run(codes, force)))


if __name__ == "__main__":
    main()
