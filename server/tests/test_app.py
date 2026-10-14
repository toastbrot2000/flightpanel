"""Start des Servers: Datenbank, Panel home, einmalige Übernahme alter JSON-Dateien und .env-Werte,
verständliche Meldung, wenn DATA_DIR nicht beschreibbar ist."""

import asyncio
import json
import os
import shutil
import time

import aiohttp
import pytest
from aiohttp import web

from conftest import FIXTURES, make_cfg
from flightpanel.db import Db, db_path
from flightpanel.main import App, _main, check_data_dir
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


def stored(tmp_path, settings: dict) -> None:
    """Das Panel home mit diesen Einstellungen, wie es eine frühere Fassung oder eine Handänderung hinterließ."""
    db = Db(db_path(str(tmp_path)))
    db.panel_add("home", settings=settings)
    db.close()


def test_a_stored_setting_the_server_does_not_know_is_skipped_and_the_rest_applies(tmp_path, caplog):
    """Eine Einstellung, die eine spätere Fassung nicht mehr kennt (oder eine Handänderung): Der Server startet
    trotzdem, mit allem anderen, und sagt, wie man sie loswird. Bisher: Neustartschleife bis panels unset."""
    stored(tmp_path, {"HOME_LAT": "51.0", "OLD_SETTING": "1"})
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert app.cfg.home_lat == 51.0
            warning = next(r.getMessage() for r in caplog.records if "OLD_SETTING" in r.getMessage())
            assert "python -m flightpanel.panels unset home OLD_SETTING" in warning
            assert app.issues.items["setting:home OLD_SETTING"]["example"]["error"] == (
                "OLD_SETTING: keine Panel-Einstellung")
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_stored_value_that_no_longer_parses_is_skipped_and_the_rest_applies(tmp_path, caplog):
    """Breite 95 stand vor den Grenzen für Zahlen vielleicht schon in der Datenbank: Sie bleibt weg (Default), der
    Rest gilt, Log und /issues nennen den Wert und den Befehl. Zwei kaputte Werte: ein Befehl für beide, denn
    panels prüft beim Zurücksetzen des einen auch den anderen."""
    stored(tmp_path, {"HOME_LAT": "95", "VIEW_FROM": "45", "RADIUS_KM": "nan"})
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert (app.cfg.home_lat, app.cfg.radius_km, app.cfg.view_from) == (cfg.home_lat, cfg.radius_km, 45)
            warning = next(r.getMessage() for r in caplog.records if "HOME_LAT" in r.getMessage())
            assert "HOME_LAT='95'" in warning and "RADIUS_KM='nan'" in warning
            assert "python -m flightpanel.panels unset home HOME_LAT RADIUS_KM" in warning
            assert {k for k in app.issues.items if k.startswith("setting:")} == {
                "setting:home HOME_LAT", "setting:home RADIUS_KM"}
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_database_put_aside_at_the_start_shows_in_issues_ok_and_health(tmp_path, caplog):
    """Eine kaputte Datenbank beiseitezulegen heißt, leer zu starten: Ohne Hinweis merkt der Besitzer es erst, wenn das
    Album fehlt. Darum steht es in /issues, /ok bleibt rot, bis dort geleert ist, und /health nennt die Datei. Der
    Eintrag liegt in der neuen Datenbank und übersteht einen Neustart."""
    (tmp_path / "flightpanel.db").write_text("das ist keine Datenbank " * 100)
    (tmp_path / "flightpanel.db.bak").write_bytes(b"Kopie der Vornacht")
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0))

    async def run():
        with caplog.at_level("ERROR"):
            app = App(cfg, env={})
        try:
            (aside,) = tmp_path.glob("flightpanel.db.broken-*")
            assert "flightpanel.db.bak" in caplog.text                       # der Weg zurück steht im Log
            entry = app.issues.items[f"database:{aside.name}"]
            assert "flightpanel.db.bak" in entry["example"]["fix"] and entry["example"]["error"]
            now = time.time()
            reason = f"Datenbank beiseitegelegt, das Album startete leer: {aside.name} (siehe /issues)"
            assert app.problems(now) == [reason]
            assert app.health()["database"]["put_aside"] == [aside.name]
            app.save()
            again = App(cfg, env={})                                          # Neustart: die Meldung bleibt
            try:
                assert again.problems(now) == [reason]
                assert again.health()["database"]["put_aside"] == [aside.name]
                again.issues.clear()                                          # nachgesehen und geleert
                assert again.problems(now) == [] and again.health()["database"]["put_aside"] == []
            finally:
                await again.session.close()
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


@pytest.fixture
def like_an_ordinary_user(monkeypatch):
    """os.access nach den Rechtebits des Eigentümers. Die Tests laufen im Container als root, und für root sagt
    os.access auch bei einem Ordner mit 0o555 ja: Ohne diese Nachbildung prüften die Tests nichts. Die Rechtebits
    setzen sie selbst, mit os.chmod."""
    def access(path, mode, **kwargs):
        try:
            bits = os.stat(path).st_mode
        except OSError:
            return False
        need = (0o400 if mode & os.R_OK else 0) | (0o200 if mode & os.W_OK else 0) | (0o100 if mode & os.X_OK else 0)
        return bits & need == need

    monkeypatch.setattr(os, "access", access)


def test_a_usable_data_dir_gets_no_complaint(tmp_path):
    (tmp_path / "logos").mkdir()
    (tmp_path / "flightpanel.db").write_bytes(b"")
    assert check_data_dir(str(tmp_path)) is None
    assert check_data_dir(str(tmp_path / "noch-nicht-da")) is None          # fehlt ganz: der Start legt es an


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Rechtebits gibt es nur unter Linux und macOS")
@pytest.mark.parametrize(("name", "mode"), [("", 0o555), ("", 0o600), ("logos", 0o555), ("flightpanel.db", 0o444)],
                         ids=["data", "data-no-search", "logos", "database"])
def test_what_the_container_cannot_write_is_named_with_the_fix(tmp_path, monkeypatch, like_an_ordinary_user, name,
                                                               mode):
    """Was der Server nicht beschreiben darf (DATA_DIR, logos/, die Datenbank, etwa weil ein früherer Container sie
    als root anlegte oder data/ einem anderen Benutzer gehört): Der Satz nennt genau diesen Pfad, den Benutzer, als der
    der Server wirklich läuft (PUID:PGID, nicht fest 1000), und das Mittel dagegen. Ein Ordner ohne Durchgangsrecht
    (0o600) taugt so wenig wie einer ohne Schreibrecht."""
    (tmp_path / "logos").mkdir()
    (tmp_path / "flightpanel.db").write_bytes(b"")
    target = tmp_path / name
    before = os.stat(target).st_mode & 0o777
    os.chmod(target, mode)
    try:
        with monkeypatch.context() as m:
            m.setattr(os, "getuid", lambda: 1234)
            m.setattr(os, "getgid", lambda: 4321)
            problem = check_data_dir(str(tmp_path))
    finally:
        os.chmod(target, before)                    # sonst räumt pytest tmp_path nicht auf
    assert problem is not None and problem.startswith(f"{target} ist nicht beschreibbar")
    assert "Benutzer 1234:4321" in problem
    assert "PUID" in problem and "PGID" in problem and "id -u" in problem
    assert "sudo chown -R 1234:4321 data" in problem                # für root-Reste und docker run ohne compose


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Rechtebits gibt es nur unter Linux und macOS")
def test_the_message_works_without_unix_user_ids(tmp_path, monkeypatch, like_an_ordinary_user):
    """Wo es os.getuid nicht gibt, nennt der Satz keinen Benutzer, aber den Pfad und PUID/PGID."""
    before = os.stat(tmp_path).st_mode & 0o777
    os.chmod(tmp_path, 0o555)
    try:
        with monkeypatch.context() as m:
            m.delattr(os, "getuid")
            m.delattr(os, "getgid")
            problem = check_data_dir(str(tmp_path))
    finally:
        os.chmod(tmp_path, before)
    assert problem is not None and problem.startswith(f"{tmp_path} ist nicht beschreibbar")
    assert "PUID" in problem and "PGID" in problem and "Benutzer" not in problem and "chown" not in problem


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Rechtebits gibt es nur unter Linux und macOS")
def test_the_start_stops_when_data_is_not_writable(tmp_path, monkeypatch, caplog, like_an_ordinary_user):
    """Eine Meldung mit Pfad und Befehl, Exit-Code 2, und App entsteht gar nicht erst. Ohne die Prüfung käme
    "unable to open database file" aus dem Datenbankcode, bei jedem Neustart wieder."""
    def not_reached(cfg):
        raise AssertionError("App darf bei unbeschreibbarem DATA_DIR gar nicht erst entstehen")

    data = tmp_path / "data"
    data.mkdir()
    os.chmod(data, 0o555)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setattr("flightpanel.main.App", not_reached)
    try:
        with caplog.at_level("ERROR"), pytest.raises(SystemExit) as stopped:
            asyncio.run(_main())
    finally:
        os.chmod(data, 0o755)
    assert stopped.value.code == 2
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and errors[0].startswith(f"{data} ist nicht beschreibbar")
    assert "PUID" in errors[0] and "PGID" in errors[0]
    assert not (data / "flightpanel.db").exists()
