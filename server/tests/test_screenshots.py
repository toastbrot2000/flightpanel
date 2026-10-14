"""tools/screenshots.py: Die Doku-Bilder entstehen auf jedem Rechner gleich, in welcher Zeitzone er auch steht."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "screenshots.py"

# Lädt das Werkzeug als Modul (sein Code läuft beim Import), rendert alle Bilder nach argv[2] statt nach docs/images
# und meldet die Zeitzone des Prozesses und den Zeitpunkt, auf den die meisten Beispiele gerendert sind.
RENDER = """
import importlib.util, json, sys, time
spec = importlib.util.spec_from_file_location("screenshots", sys.argv[1])
shots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shots)
shots.OUT = sys.argv[2]
shots.main()
print(json.dumps([list(time.tzname), shots.NOW]))
"""


def render(tmp_path: Path, tz: str) -> tuple[dict[str, bytes], list]:
    """Alle Bilder des Werkzeugs, gerendert von einem eigenen Prozess auf einem Rechner in der Zeitzone tz (Bytes je
    Dateiname), dazu was es über seine Zeitzone meldet. Ein eigener Prozess, weil das Werkzeug die Zeitzone des
    Prozesses festlegt, und das dürfen die übrigen Tests nicht erben."""
    out, scratch = tmp_path / tz.replace("/", "_"), tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)               # das Werkzeug legt ein temporäres Logo-Verzeichnis an
    env = {**os.environ, "TZ": tz, "TMPDIR": str(scratch)}
    done = subprocess.run([sys.executable, "-c", RENDER, str(TOOL), str(out)], env=env, check=True,
                          capture_output=True, text=True)
    return {p.name: p.read_bytes() for p in sorted(out.glob("*.png"))}, json.loads(done.stdout.splitlines()[-1])


def test_the_images_are_rendered_in_berlin_time_wherever_the_machine_is(tmp_path):
    """Das Feuerwerk wird aus dem Zeitpunkt gewürfelt, und die Beispiele sind Ortszeiten (Neujahr 0:00:04): Dasselbe
    Werkzeug malte ein anderes fireworks.png, wenn der Rechner in UTC statt in der Zeitzone aus .env stand. Berlin,
    weil die eingecheckten Bilder so entstanden sind."""
    utc, utc_info = render(tmp_path, "UTC")
    auckland, auckland_info = render(tmp_path, "Pacific/Auckland")
    berlin_now = datetime(2026, 10, 10, 15, 42, 6, tzinfo=UTC).timestamp()           # 17:42:06 Uhr MESZ
    assert utc_info == auckland_info == [["CET", "CEST"], berlin_now]
    assert "fireworks.png" in utc and utc.keys() == auckland.keys()
    assert [name for name in utc if utc[name] != auckland[name]] == []
