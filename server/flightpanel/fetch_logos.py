"""Seed: Logos für alle Airlines aus data/airlines.json auf einmal holen,
damit beim ersten Flug nichts fehlt. Danach holt der Server Unbekanntes
selbst nach, sobald es auftaucht.

    python3 -m flightpanel.fetch_logos            # alle aus airlines.json
    python3 -m flightpanel.fetch_logos LH U2 FR   # nur diese IATA-Codes
    python3 -m flightpanel.fetch_logos --force    # vorhandene überschreiben

Im Container: docker compose exec flightpanel python3 -m flightpanel.fetch_logos
"""

import asyncio
import logging
import os
import sys

import aiohttp

from .config import CFG
from .logos import LogoStore
from .model import load_airlines


async def run(codes: list[str], force: bool) -> int:
    store = LogoStore(os.path.join(CFG.data_dir, "logos"), CFG.logo_size, fetch=True, url_template=CFG.logo_url)
    ok = skipped = 0
    failed: list[str] = []
    async with aiohttp.ClientSession(headers={"User-Agent": "flightpanel/1.0"}) as session:
        store.session = session
        for code in codes:
            if not force and store.path_for(code):
                skipped += 1
                continue
            if force:
                p = store.path_for(code)
                if p:
                    os.remove(p)
            if await store.fetch_now(code):
                ok += 1
            else:
                failed.append(code)
            await asyncio.sleep(0.3)   # freundlich zum CDN
    print(f"geholt: {ok}, schon da: {skipped}, fehlgeschlagen: {len(failed)}")
    if failed:
        print("fehlgeschlagen:", " ".join(failed))
        print("-> Netzfehler: später noch einmal; sonst Datei von Hand ablegen: data/logos/<IATA>.png, tools/make_logo.py hilft")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    if args:
        codes = [a.upper() for a in args]
    else:
        airlines = load_airlines(CFG.data_dir)
        codes = sorted({v["iata"] for v in airlines.values() if len(v["iata"]) == 2})
    print(f"{len(codes)} Codes: {' '.join(codes)}")
    sys.exit(asyncio.run(run(codes, force)))


if __name__ == "__main__":
    main()
