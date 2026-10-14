"""Start des Servers: Datenbank, Panel home, einmalige Übernahme alter JSON-Dateien und .env-Werte."""

import asyncio
import json
import os
import shutil
import time

import aiohttp
import pytest
from aiohttp import web

from conftest import FIXTURES, make_cfg
from flightpanel.db import db_path
from flightpanel.main import App
from flightpanel.stats import Stats
from flightpanel.web import make_web_app


def test_old_json_files_are_taken_over_once(tmp_path):
    t = time.time()
    (tmp_path / "logos").mkdir()
    (tmp_path / "cache.json").write_text(json.dumps({"route:EZY1": [t + 3600, {"origin": "LGW"}]}))
    (tmp_path / "issues.json").write_text(json.dumps({"since": t - 100, "items": {"exception:x": {
        "kind": "exception", "key": "x", "seen": 1, "days": 1, "first": t, "last": t, "callsigns": []}}}))
    (tmp_path / "stats.json").write_text(json.dumps({"since": t - 1000, "dex": {"easyJet": {
        "first": t, "last": t, "code": "EZY", "name": "easyJet", "known": True, "callsign": "EZY1", "fanfare": False}},
        "course": {"now": 249, "prev": None, "since": t, "seen": t}}))
    (tmp_path / "logos" / ".missing.json").write_text(json.dumps({"FX": t}))
    shutil.copy(os.path.join(FIXTURES, "iss.tle"), tmp_path / "tle.txt")
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            assert app.cache.get("route:EZY1") == {"origin": "LGW"}
            assert "exception:x" in app.issues.items and "easyJet" in app.stats.dex and app.stats.course == 249
            assert app.logos.missing() == ["FX"] and app.iss.tle is not None
            assert app.model.landing_course == 249          # Startwert der Anzeige ist schon die übernommene Piste
            for name in ("cache.json", "issues.json", "stats.json", "tle.txt", "logos/.missing.json"):
                assert not (tmp_path / name).exists() and (tmp_path / f"{name}.imported").exists(), name
            again = App(cfg, env={})                      # zweiter Start: alles aus der Datenbank
            assert "easyJet" in again.stats.dex and again.cache.get("route:EZY1") and again.iss.tle == app.iss.tle
            assert again.issues.since == app.issues.since and again.model.landing_course == 249
            await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_env_panel_keys_are_taken_over_on_the_first_start_then_ignored(tmp_path, caplog):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        first = App(cfg, env={"HOME_LAT": "51.0", "CARD_PIGEON": "0", "CARD_CLOCK": "1"})
        try:
            assert first.cfg.home_lat == 51.0 and first.cfg.cards_off == {"pigeon"}
            assert first.db.panel_get("home")["settings"] == {"HOME_LAT": "51.0", "CARD_PIGEON": "0"}
            assert first.server_cfg.home_lat == cfg.home_lat          # der Server selbst bleibt, wie er war
        finally:
            await first.session.close()
        with caplog.at_level("WARNING"):
            second = App(cfg, env={"HOME_LAT": "40.0"})
        try:
            assert second.cfg.home_lat == 51.0
            assert "HOME_LAT" in caplog.text and "panels set home" in caplog.text
        finally:
            await second.session.close()

    asyncio.run(run())


def test_a_bad_old_env_value_stops_the_start_and_is_not_stored(tmp_path):
    """Wie früher bei .env: Unsinn bricht den Start mit dem Namen ab. Er landet aber nicht in
    der Datenbank, sonst hülfe es nicht mehr, die .env zu reparieren."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with pytest.raises(ValueError, match="HOME_LAT='hier'"):
            App(cfg, env={"HOME_LAT": "hier"})
        app = App(cfg, env={"HOME_LAT": "51.0"})                # .env repariert: jetzt übernommen
        try:
            assert app.cfg.home_lat == 51.0
        finally:
            await app.session.close()

    asyncio.run(run())


def test_save_writes_everything_and_the_stores_reload_it(tmp_path):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            await app._poll(time.time())
            app.cache.set("route:X", {"origin": "LGW"}, ttl=600)
            app.issues.note("exception", "KeyError x.py:1")
            app.save()
            again = App(cfg, env={})
            assert again.cache.get("route:X") == {"origin": "LGW"} and "exception:KeyError x.py:1" in again.issues.items
            assert again.stats.last_obs == app.stats.last_obs > 0           # observe() lief und wurde geschrieben
            await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_damaged_old_file_is_left_alone_and_the_start_goes_on(tmp_path, caplog):
    (tmp_path / "issues.json").write_text(json.dumps({"since": 1, "items": None}))     # von Hand verändert
    (tmp_path / "cache.json").write_text("[1, 2]")                                        # kein Objekt
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert app.issues.items == {} and len(app.cache) == 0
            assert (tmp_path / "issues.json").exists() and (tmp_path / "cache.json").exists()
            assert "issues.json nicht übernommen" in caplog.text and "cache.json nicht übernommen" in caplog.text
        finally:
            await app.session.close()

    asyncio.run(run())


@pytest.mark.parametrize("content", [b"\xff\xfe\x00 kein Text", b"hier stehen keine Bahndaten"],
                         ids=["binary", "no-orbit"])
def test_an_unusable_tle_file_is_left_alone_and_the_rest_is_taken(tmp_path, caplog, content):
    """tle.txt, die kein Text ist oder keine Bahndaten enthält: Warnung, die Datei bleibt, der Start geht weiter.
    Was sich übernehmen lässt, wird trotzdem übernommen und umbenannt."""
    t = time.time()
    (tmp_path / "tle.txt").write_bytes(content)
    (tmp_path / "cache.json").write_text(json.dumps({"route:EZY1": [t + 3600, {"origin": "LGW"}]}))
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert app.iss.tle is None and app.cache.get("route:EZY1") == {"origin": "LGW"}
            assert (tmp_path / "tle.txt").read_bytes() == content and not (tmp_path / "tle.txt.imported").exists()
            assert not (tmp_path / "cache.json").exists() and (tmp_path / "cache.json.imported").exists()
            assert "tle.txt nicht übernommen" in caplog.text
        finally:
            await app.session.close()

    asyncio.run(run())


def test_old_files_stay_when_saving_the_import_fails(tmp_path, monkeypatch):
    """Erst gespeichert, dann umbenannt: Scheitert das Speichern, bleiben die alten Dateien liegen,
    und der nächste Start übernimmt sie."""
    t = time.time()
    (tmp_path / "cache.json").write_text(json.dumps({"route:EZY1": [t + 3600, {"origin": "LGW"}]}))
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)
    real, made = aiohttp.ClientSession, []

    def tracked(**kwargs):                          # der abgebrochene Start lässt seine Sitzung offen: schließen
        made.append(real(**kwargs))
        return made[-1]

    def boom(self):
        raise RuntimeError("Platte voll")

    async def run():
        with monkeypatch.context() as m:
            m.setattr(aiohttp, "ClientSession", tracked)
            m.setattr(Stats, "save", boom)
            with pytest.raises(RuntimeError, match="Platte voll"):
                App(cfg, env={})
        for session in made:
            await session.close()
        assert (tmp_path / "cache.json").exists() and not (tmp_path / "cache.json.imported").exists()
        app = App(cfg, env={})
        try:
            assert app.cache.get("route:EZY1") == {"origin": "LGW"}
            assert not (tmp_path / "cache.json").exists() and (tmp_path / "cache.json.imported").exists()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_failing_save_writes_nothing_and_the_next_one_writes_everything(tmp_path, monkeypatch):
    """Eine Transaktion für alle Speicher: Scheitert einer, steht von den anderen nichts in der Datenbank,
    und das nächste Speichern holt alles nach."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    def boom(self):
        raise RuntimeError("Platte voll")

    async def run():
        app = App(cfg, env={})
        try:
            app.cache.set("route:X", {"origin": "LGW"}, ttl=600)
            app.issues.note("exception", "KeyError x.py:1")
            with monkeypatch.context() as m:
                m.setattr(Stats, "save", boom)
                with pytest.raises(RuntimeError, match="Platte voll"):
                    app.save()
            assert app.db.query("SELECT key FROM cache") == [] and app.db.query("SELECT key FROM issues") == []
            app.save()
            assert [r["key"] for r in app.db.query("SELECT key FROM cache")] == ["route:X"]
            assert [r["key"] for r in app.db.query("SELECT key FROM issues")] == ["exception:KeyError x.py:1"]
        finally:
            await app.session.close()

    asyncio.run(run())


def test_stop_saves_once_more_and_closes_the_database(tmp_path):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        runner = web.AppRunner(make_web_app(app), access_log=None)
        await runner.setup()                        # nicht gestartet (kein Port), stop() räumt nur auf
        app.cache.set("route:X", {"origin": "LGW"}, ttl=600)
        await app.stop(runner)
        with pytest.raises(Exception, match="closed database"):
            app.db.query("SELECT 1")
        again = App(cfg, env={})
        try:
            assert again.cache.get("route:X") == {"origin": "LGW"}
        finally:
            await again.session.close()

    asyncio.run(run())


def test_without_env_the_process_environment_counts(tmp_path, monkeypatch):
    """So startet _main(): App(cfg) ohne env."""
    monkeypatch.setenv("HOME_LAT", "51.0")
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg)
        try:
            assert app.cfg.home_lat == 51.0 and app.db.panel_get("home")["settings"] == {"HOME_LAT": "51.0"}
        finally:
            await app.session.close()

    asyncio.run(run())


def test_the_database_lives_in_the_data_dir_under_its_documented_name(tmp_path):
    """Sicherungen und Doku nennen data/flightpanel.db: Unter anderem Namen bliebe die vorhandene Datenbank liegen."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            assert (tmp_path / "flightpanel.db").is_file() and app.db.path == db_path(str(tmp_path))
        finally:
            await app.session.close()

    asyncio.run(run())


def test_the_lan_panel_is_the_one_named_in_the_config(tmp_path):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                   lan_panel="wohnzimmer")

    async def run():
        app = App(cfg, env={"HOME_LAT": "51.0"})
        try:
            assert [p["id"] for p in app.db.panel_list()] == ["wohnzimmer"] and app.stats.panel_id == "wohnzimmer"
            assert app.db.panel_get("wohnzimmer")["settings"] == {"HOME_LAT": "51.0"}
        finally:
            await app.session.close()

    asyncio.run(run())
