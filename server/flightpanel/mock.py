"""Fantasieflüge (MOCK=1): Layout entwerfen und Hardware testen ohne Netz."""

import random

from .config import Config
from .model import ARR, DEP, OVF, DisplayModel, Flight

_POOL = [
    ("EZY45KZ", "EZY", "U2", "easyJet", "LGW", "BER", "A320", ARR),
    ("DLH2041", "DLH", "LH", "Lufthansa", "BER", "LIS", "A321", DEP),
    ("RYR8DK", "RYR", "FR", "Ryanair", "DUB", "BER", "B738", ARR),
    ("EWG7PL", "EWG", "EW", "Eurowings", "BER", "PMI", "A319", DEP),
    ("KLM1823", "KLM", "KL", "KLM", "AMS", "BER", "E190", ARR),
    ("THY1725", "THY", "TK", "Turkish", "IST", "BER", "A333", ARR),
    ("UAE59", "UAE", "EK", "Emirates", "DXB", "WAW", "B77W", OVF),
    ("WZZ3RU", "WZZ", "W6", "Wizz Air", "BER", "BUD", "A21N", DEP),
]


def mock_model(cfg: Config, seed: int) -> DisplayModel:
    """Wechselnde Flüge, Radar und "Nächster"; der Seed wechselt alle 20 s."""
    rnd = random.Random(seed)
    n = rnd.choice([0, 1, 2, 2, 2]) if cfg.max_flights >= 2 else 1
    picks = rnd.sample(_POOL, k=min(n, len(_POOL)))
    flights = []
    for i, (cs, al_icao, al_iata, al, o, d, typ, st) in enumerate(picks):
        alt = rnd.randint(1800, 9500) if st != OVF else rnd.randint(30000, 39000)
        flights.append(Flight(
            icao24=f"3c{seed % 0xFFF:03x}{i}", callsign=cs, status=st,
            dist_km=round(4 + i * 9 + rnd.random() * 6, 1), bearing=rnd.randint(0, 359),
            alt_ft=alt, speed_kt=rnd.randint(160, 460), track=rnd.randint(0, 359),
            vrate_fpm=(-rnd.randint(500, 1500) if st == ARR else rnd.randint(500, 2500) if st == DEP else 0),
            origin=o, dest=d, airline=al, airline_icao=al_icao, airline_iata=al_iata, actype=typ,
        ))
    if flights and rnd.random() < 0.25:
        # ab und zu nur ein Privatflieger: kommt als Leerlauf-Karte, ohne Vorrang
        p = flights[0]
        p.callsign, p.kind, p.status, p.actype, p.alt_ft = "DEFGH", "other", OVF, "C172", 2500
        p.airline = p.airline_icao = p.airline_iata = p.origin = p.dest = ""
        flights = [p]
    for n, f in enumerate(sorted((f for f in flights if f.status == ARR), key=lambda f: f.dist_km)):
        f.seq = n + 1
    nearby = [{"callsign": f.callsign, "icao24": f.icao24, "az": f.bearing, "elev": 8.0, "dist_km": f.dist_km,
               "alt_ft": f.alt_ft, "in_view": "now", "seq": f.seq, "eta_s": 0, "status": f.status}
              for f in flights]
    # Weitere Flugzeuge im Radius, außerhalb des Sichtfelds: für Radar und "Nächster"
    for i in range(rnd.randint(2, 7)):
        cs, *_, st = rnd.choice(_POOL)
        nearby.append({"callsign": f"{cs[:3]}{rnd.randint(10, 9999)}", "icao24": f"3d{i:04x}",
                       "az": rnd.randint(0, 359), "elev": round(rnd.uniform(0.5, 25), 1),
                       "dist_km": round(rnd.uniform(5, 40), 1), "alt_ft": rnd.randint(2000, 20000),
                       "in_view": "no", "seq": None, "eta_s": rnd.choice([None, rnd.randint(40, 580)]),
                       "status": st})
    model = DisplayModel(flights=flights, source="mock", empty=not flights, nearby=nearby,
                         landing_course=cfg.runway_course, course_votes=2)
    if not flights:
        cs, al_icao, al_iata, al, o, d, typ, st = rnd.choice(_POOL)
        model.upcoming = Flight(icao24="3cffff", callsign=cs, status=st, dist_km=22.0, bearing=250,
                                alt_ft=4200, speed_kt=210, track=70, vrate_fpm=-800, origin=o, dest=d,
                                airline=al, airline_icao=al_icao, airline_iata=al_iata, actype=typ,
                                visible=False)
        model.upcoming_eta_s = rnd.randint(60, 480)
        model.upcoming_az = (cfg.view_from + rnd.choice([-5, 5])) % 360
        model.upcoming_via = "approach" if st == ARR else "track"
    return model
