"""Helfer, die mehrere Karten brauchen: Lage relativ zum Sichtfeld, Zufall pro
Zeitscheibe."""

import random

from ..idle import Ctx


def in_cone(ctx: Ctx, az: float, el: float | None = None, margin: float = 0) -> bool:
    """Liegt die Richtung (und der Höhenwinkel) im Sichtfeld, mit margin Grad Spielraum?"""
    c = ctx.cfg
    if c.view_span >= 360:
        ok = True
    else:
        ok = (az - c.view_from + margin) % 360 <= c.view_span + 2 * margin
    return ok and (el is None or c.view_min_elev <= el <= c.view_max_elev)


def side_arrow(ctx: Ctx, az: float) -> str:
    """← oder →: auf welcher Seite des Fensters etwas auftaucht. Leer bei Rundumsicht."""
    if ctx.cfg.view_span >= 300:
        return ""
    d = (az - ctx.cfg.view_center + 180) % 360 - 180
    return "←" if d < 0 else "→"


def slot_rng(ctx: Ctx, salt: str) -> random.Random:
    """Zufall, der innerhalb einer Zeitscheibe gleich bleibt (jeder Abruf zeigt dasselbe)."""
    return random.Random(f"{int(ctx.now // max(1, ctx.cfg.idle_seconds))}:{salt}")
