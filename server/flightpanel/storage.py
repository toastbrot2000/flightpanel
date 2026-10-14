"""Dateien im Datenverzeichnis lesen und schreiben.

Geschrieben wird erst in eine .tmp-Datei und dann umbenannt: Ein Absturz oder
Stromausfall beim Speichern hinterlässt nie eine halbe Datei. Eine fehlende Datei
ist kein Fehler, eine kaputte steht im Log und zählt wie eine fehlende; beides
soll den Start nicht verhindern."""

import json
import logging
import os
from typing import Any

log = logging.getLogger("storage")


def read_json(path: str) -> Any | None:
    """Inhalt der JSON-Datei, None wenn sie fehlt oder nicht lesbar ist."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("%s nicht lesbar (%s), ignoriert", os.path.basename(path), e)
        return None


def is_number(x) -> bool:
    """Eine Zahl, mit der sich rechnen und die sich speichern lässt. Alte JSON-Dateien, die jemand von Hand geändert
    hat (main.absorb_legacy), haben statt Zeitpunkten und Zählern mitunter Text, null, true, nan oder 1e999; bool gilt
    nicht, auch wenn es in Python eine Zahl ist. Mit abs() < 1e15 fallen nan und inf weg, ohne dass ein riesiges int
    überläuft."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and abs(x) < 1e15


def write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path: str, obj: Any) -> None:
    write_atomic(path, json.dumps(obj, ensure_ascii=False))
