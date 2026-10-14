"""Start des Servers: Datenbank, Panel home, einmalige Übernahme alter JSON-Dateien und .env-Werte,
verständliche Meldung, wenn DATA_DIR nicht beschreibbar ist."""

import asyncio
import json
import os
import shlex
import shutil
import time
from dataclasses import replace

import aiohttp
import pytest
from aiohttp import web
from test_sources import response_error

from conftest import BER, DATA, FIXTURES, FakeAdsb, make_cfg, state
from flightpanel import panels
from flightpanel.db import Db, db_path
from flightpanel.geo import destination, haversine_km
from flightpanel.main import REST_S, App, _main, check_data_dir
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
            assert "exception:x" in app.issues.items and "easyJet" in app.lan.stats.dex and app.airfield.course == 249
            assert app.logos.missing() == ["FX"] and app.orbit.tle is not None
            assert app.airfield.landing_course == 249  # Startwert des ersten Abrufs ist schon die übernommene Piste
            for name in ("cache.json", "issues.json", "stats.json", "tle.txt", "logos/.missing.json"):
                assert not (tmp_path / name).exists() and (tmp_path / f"{name}.imported").exists(), name
            again = App(cfg, env={})                      # zweiter Start: alles aus der Datenbank
            assert "easyJet" in again.lan.stats.dex and again.cache.get("route:EZY1")
            assert again.orbit.tle == app.orbit.tle
            assert again.issues.since == app.issues.since and again.airfield.landing_course == 249
            await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_env_panel_keys_are_taken_over_on_the_first_start_then_ignored(tmp_path, caplog):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        first = App(cfg, env={"HOME_LAT": "51.0", "CARD_PIGEON": "0", "CARD_CLOCK": "1"})
        try:
            assert first.lan.cfg.home_lat == 51.0 and first.lan.cfg.cards_off == {"pigeon"}
            assert first.db.panel_get("home")["settings"] == {"HOME_LAT": "51.0", "CARD_PIGEON": "0"}
            assert first.server_cfg.home_lat == cfg.home_lat          # der Server selbst bleibt, wie er war
        finally:
            await first.session.close()
        with caplog.at_level("WARNING"):
            second = App(cfg, env={"HOME_LAT": "40.0"})
        try:
            assert second.lan.cfg.home_lat == 51.0
            assert "HOME_LAT" in caplog.text and "panels set home" in caplog.text
            assert "aus .env entfernen" in caplog.text                  # und wie die Warnung verschwindet
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
            assert app.lan.cfg.home_lat == 51.0
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
            assert app.lan.cfg.home_lat == 51.0
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
            assert (app.lan.cfg.home_lat, app.lan.cfg.radius_km, app.lan.cfg.view_from) == (
                cfg.home_lat, cfg.radius_km, 45)
            warning = next(r.getMessage() for r in caplog.records if "HOME_LAT" in r.getMessage())
            assert "HOME_LAT='95'" in warning and "RADIUS_KM='nan'" in warning
            assert "python -m flightpanel.panels unset home HOME_LAT RADIUS_KM" in warning
            assert {k for k in app.issues.items if k.startswith("setting:")} == {
                "setting:home HOME_LAT", "setting:home RADIUS_KM"}
        finally:
            await app.session.close()

    asyncio.run(run())


def test_the_command_suggested_for_skipped_settings_works(tmp_path, caplog, monkeypatch):
    """Der Befehl aus Log und /issues muss gehen, so wie er dasteht: Hier zerlegt ihn shlex wie die Shell, und panels
    führt ihn aus. Dazu gehören Schlüssel, die von Hand in die Datenbank kamen und die unset nicht fand oder nicht
    löschen konnte: klein geschrieben (home_lon, auch ein veralteter Name oder eine Karte) oder mit Leerzeichen
    (Anführungszeichen). Danach meldet der nächste Start nichts mehr, und es gilt, was der Server angenommen hatte."""
    stored(tmp_path, {"HOME_LAT": "51.0", "home_lon": "13.4", "old_setting": "1", "card_pigeon": "0",
                      "view from": "45", "RADIUS_KM": "nan"})
    monkeypatch.setenv("DATA_DIR", str(tmp_path))                       # panels.main() liest die Umgebung
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def start() -> set[str]:
        """Ein Start des Servers; die Befehle, die er für übergangene Einstellungen nennt (ein Befehl für alle)."""
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert app.lan.cfg.home_lat == 51.0
            return {it["example"]["fix"] for key, it in app.issues.items.items() if key.startswith("setting:")}
        finally:
            await app.session.close()
            app.db.close()

    (fix,) = asyncio.run(start())
    assert any(fix in r.getMessage() for r in caplog.records)           # dasselbe steht im Log
    argv = shlex.split(fix)
    assert argv[:5] == ["python", "-m", "flightpanel.panels", "unset", "home"]
    assert set(argv[5:]) == {"home_lon", "old_setting", "card_pigeon", "view from", "RADIUS_KM"}
    assert panels.main(argv[3:]) == 0
    assert asyncio.run(start()) == set()
    again = Db(db_path(str(tmp_path)), put_aside=False)
    assert again.panel_get("home")["settings"] == {"HOME_LAT": "51.0"}


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


def test_a_database_put_aside_is_in_the_new_database_before_any_save(tmp_path):
    """Der Hinweis wird sofort gespeichert, nicht erst vom nächsten save(): Käme der Server davor nicht weiter
    (Neustartschleife, docker compose down), fände der nächste Start eine gesunde leere Datenbank, /ok wäre grün, und
    die nächtliche Kopie ersetzte den Weg zurück zum Album."""
    (tmp_path / "flightpanel.db").write_text("das ist keine Datenbank " * 100)
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})                                  # kein save(): nur, was der Start festgeschrieben hat
        try:
            (aside,) = tmp_path.glob("flightpanel.db.broken-*")
            reader = Db(db_path(str(tmp_path)), put_aside=False)  # eine zweite Verbindung sieht nur Festgeschriebenes
            try:
                assert [r["key"] for r in reader.query("SELECT key FROM issues")] == [f"database:{aside.name}"]
            finally:
                reader.close()
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
            assert again.lan.stats.last_obs == app.lan.stats.last_obs > 0   # observe() lief und wurde geschrieben
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


def test_hand_edited_old_files_lose_only_their_odd_entries_and_the_start_goes_on(tmp_path):
    """Alte JSON-Dateien mit je einem Eintrag, den jemand von Hand zerstört hat, zwischen guten: Die guten werden
    übernommen und gespeichert, der Server startet (sonst wäre es bei restart: unless-stopped eine Neustartschleife),
    und die Dateien heißen danach *.imported."""
    t = time.time()
    issue = {"kind": "exception", "key": "x", "seen": 1, "days": 1, "first": t, "last": t, "callsigns": []}
    album = {"first": t, "last": t, "code": "EZY", "name": "easyJet", "known": True, "callsign": "EZY1",
             "fanfare": False}
    (tmp_path / "cache.json").write_text(json.dumps({"route:A": [t + 3600, {"origin": "LGW"}],
                                                     "route:S": [str(t + 3600), {"origin": "S"}], "route:X": "Text"}))
    (tmp_path / "issues.json").write_text(json.dumps({"since": "gestern", "items": {"exception:x": issue, "k": 1}}))
    (tmp_path / "stats.json").write_text(json.dumps({"since": t - 1000, "dex": {"easyJet": album, "k": {"code": "X"}},
                                                     "history": {"2026-10-09": "viel"}}))
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            assert app.cache.get("route:A") == {"origin": "LGW"} and app.cache.get("route:S") == {"origin": "S"}
            assert len(app.cache) == 2 and set(app.issues.items) == {"exception:x"}
            assert set(app.lan.stats.dex) == {"easyJet"}
            for name in ("cache.json", "issues.json", "stats.json"):
                assert not (tmp_path / name).exists() and (tmp_path / f"{name}.imported").exists(), name
            app.save()                                  # und jedes weitere Speichern geht
            again = App(cfg, env={})
            try:
                assert set(again.lan.stats.dex) == {"easyJet"} and set(again.issues.items) == {"exception:x"}
                assert again.cache.get("route:S") == {"origin": "S"}
            finally:
                await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_stats_file_of_the_wrong_shape_stays_and_nothing_of_it_is_taken(tmp_path, caplog):
    """Ist der Aufbau der Datei hin (dex kein Objekt), wird nichts übernommen und nichts umbenannt, auch die
    Landerichtung darin nicht (Stats kommt vor Airfield an die Reihe): Eine halbe Übernahme umzubenennen verlöre den
    Rest, den man von Hand noch retten könnte."""
    (tmp_path / "stats.json").write_text(json.dumps({"since": 5, "dex": ["kaputt"],
                                                     "course": {"now": 249, "since": 5, "seen": 5}}))
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert (tmp_path / "stats.json").exists() and not (tmp_path / "stats.json.imported").exists()
            assert app.lan.stats.dex == {} and app.lan.stats.since != 5 and app.airfield.course is None
            assert "stats.json nicht übernommen" in caplog.text
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_failing_rename_after_the_import_is_a_warning_and_the_start_goes_on(tmp_path, monkeypatch, caplog):
    """Die Übernahme ist gespeichert; lässt sich eine alte Datei nicht umbenennen (ein Sync-Programm hält sie), ist
    das eine Warnung und kein Grund, nicht zu starten. Die anderen Dateien werden trotzdem umbenannt."""
    t = time.time()
    (tmp_path / "cache.json").write_text(json.dumps({"route:A": [t + 3600, {"origin": "LGW"}]}))
    (tmp_path / "issues.json").write_text(json.dumps({"since": t, "items": {}}))
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)
    real = os.replace

    def replace(src, dst):
        if str(src).endswith("cache.json"):
            raise PermissionError("gesperrt")
        return real(src, dst)

    async def run():
        with monkeypatch.context() as m, caplog.at_level("WARNING"):
            m.setattr(os, "replace", replace)
            app = App(cfg, env={})
        try:
            assert app.cache.get("route:A") == {"origin": "LGW"}
            assert (tmp_path / "cache.json").exists() and not (tmp_path / "cache.json.imported").exists()
            assert (tmp_path / "issues.json.imported").exists()
            assert "cache.json" in caplog.text and "gesperrt" in caplog.text
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
            assert app.orbit.tle is None and app.cache.get("route:EZY1") == {"origin": "LGW"}
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


def test_save_keeps_what_each_esp32_reported_in_the_same_transaction(tmp_path, monkeypatch):
    """panels list zeigt, wann jedes Panel zuletzt abgerufen hat und was sein ESP32 dabei meldete (vier Zahlen):
    save() schreibt es mit allem anderen in einer Transaktion, und es zählt nicht als Änderung der Einstellungen."""
    panel_rows(tmp_path, anna=ANNA, ben={})
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    def boom(self):
        raise RuntimeError("Platte voll")

    async def run():
        app = App(cfg, env={})
        try:
            before = app.db.panel_get("anna")["updated"]
            app.lan.fetched(1000.0, {})                                      # ohne X-Panel: nur der Zeitpunkt
            app.panels["anna"].fetched(1000.0, {"fw": 2610131200, "up": 60, "rssi": -61, "heap": 45000})
            with monkeypatch.context() as m:
                m.setattr(Stats, "save", boom)
                with pytest.raises(RuntimeError, match="Platte voll"):
                    app.save()
            assert app.db.panel_get("home")["seen_at"] is None               # zurückgerollt mit allem anderen
            app.save()
            home, anna, ben = (app.db.panel_get(pid) for pid in ("home", "anna", "ben"))
            assert (anna["seen_at"], anna["fw_version"], anna["uptime_s"], anna["rssi"], anna["min_heap"]) == (
                1000.0, 2610131200, 60, -61, 45000)
            assert (home["seen_at"], home["fw_version"], home["rssi"]) == (1000.0, None, None)
            assert ben["seen_at"] is None                                     # sein ESP32 war noch nicht da
            assert anna["updated"] == before and app.panels["anna"].updated == before
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
            assert app.lan.cfg.home_lat == 51.0 and app.db.panel_get("home")["settings"] == {"HOME_LAT": "51.0"}
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
            assert [p["id"] for p in app.db.panel_list()] == ["wohnzimmer"] and app.lan.stats.panel_id == "wohnzimmer"
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
    # chown nur für Reste von root und auf den Benutzer des Hosts, nie auf den des Servers (ohne PUID und PGID 1000):
    # Sonst wäre data/ dem git pull von tools/update.sh verschlossen. PUID und PGID als Weg der Wahl stehen davor.
    assert 'sudo chown -R "$(id -u):$(id -g)" data' in problem
    assert "chown -R 1234:4321" not in problem
    assert problem.index("PUID") < problem.index("chown")
    assert "docker run" in problem and "--user" in problem          # ohne compose gibt es kein PUID


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


def test_the_server_starts_with_a_restrictive_umask(tmp_path, monkeypatch):
    """Datenbank, ihre Kopien und die Logos entstehen mit dem, was die umask übrig lässt: 027 heißt, andere Benutzer
    des Rechners lesen sie nicht (Standort, Album). Gesetzt wird sie beim Start (_main), nicht beim Import."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("flightpanel.main.check_data_dir", lambda data_dir: "für den Test abgebrochen")
    before = os.umask(0o022)
    try:
        with pytest.raises(SystemExit):
            asyncio.run(_main())
        assert os.umask(before) == 0o027
    finally:
        os.umask(before)


def test_a_failing_opensky_request_leaves_no_position_in_log_or_issues(tmp_path, caplog):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            e = response_error("https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001")
            with caplog.at_level("WARNING"):
                app._note_poll_error(e)
            assert "opensky-network.org" in caplog.text and "52.1234" not in caplog.text
            assert "52.1234" not in json.dumps(app.issues_report()) and app.issues.items
        finally:
            await app.session.close()

    asyncio.run(run())


def panel_rows(tmp_path, **panels) -> None:
    """Panels in der Datenbank, wie panels.py sie anlegt: home und die übergebenen (id=Einstellungen)."""
    db = Db(db_path(str(tmp_path)))
    for pid, settings in {"home": {}, **panels}.items():
        db.panel_add(pid, settings=settings)
    db.close()


ANNA = {"HOME_LAT": "52.40", "HOME_LON": "14.05", "RADIUS_KM": "10", "SCAN_RADIUS_KM": "10"}   # östlich von BER


def test_one_poll_serves_every_panel_with_one_fetch(tmp_path):
    """Zwei Panels, ein Abruf: Sein Umkreis deckt beide ab. Landefolge und Landerichtung sind in beiden gleich, die
    Statistik zählt je Panel nur, was in seinem Abrufradius war, und /issues zählt einmal je Abruf."""
    shutil.copy(os.path.join(DATA, "airlines.json"), tmp_path)
    panel_rows(tmp_path, anna=ANNA)
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                   route_adsbim=False, track_origin=False)
    lat, lon = destination(BER[0], BER[1], 69, 15)
    states = [state(icao="a1", cs="EZY1", lat=52.55, lon=13.42, alt=1500),          # nur bei home
              state(icao="b1", cs="DLH2", lat=52.40, lon=14.06, alt=1500),          # nur bei anna
              # landet, Betreiber XYZ steht nicht in airlines.json: beide Panels sehen ihn in der Landefolge
              state(icao="3c56ec", cs="XYZ9SK", lat=lat, lon=lon, alt=800, track=249, vrate=-4)]
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return states

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            await app.poll_once(time.time())
            (lat0, lon0, r), = calls
            home, anna = app.panels["home"], app.panels["anna"]
            for p in (home, anna):
                assert haversine_km(lat0, lon0, p.cfg.home_lat, p.cfg.home_lon) + p.cfg.fetch_radius_km <= r + 1e-9
            assert home.model.landing_course == anna.model.landing_course
            assert [a["callsign"] for a in home.model.arrivals] == [a["callsign"] for a in anna.model.arrivals]
            assert "XYZ9SK" in [a["callsign"] for a in home.model.arrivals]
            assert "a1" in home.stats.day["aircraft"] and "a1" not in anna.stats.day["aircraft"]
            assert "b1" in anna.stats.day["aircraft"] and "b1" not in home.stats.day["aircraft"]
            assert app.issues.items["unknown_operator:XYZ"]["seen"] == 1          # einmal je Abruf, nicht je Panel
            assert app.counters["polls"] == 1 and app.last_model_ok > 0
        finally:
            await app.session.close()

    asyncio.run(run())


def test_panels_cli_changes_apply_without_a_restart(tmp_path):
    """panels.py schreibt neben dem laufenden Server; der übernimmt es beim nächsten Durchgang: neue Panels,
    geänderte Einstellungen (Album und Statistik bleiben), gelöschte Panels (ohne dass das Speichern scheitert).
    Ein gelöschtes und gleich wieder angelegtes Panel beginnt mit leerem Album."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0))

    async def run():
        app = App(cfg, env={})
        cli = Db(db_path(str(tmp_path)), put_aside=False)             # wie panels.py, neben dem laufenden Server
        try:
            t = time.time()
            await app.poll_once(t)
            home, stats = app.lan, app.lan.stats
            cli.panel_set_settings("home", {"VIEW_FROM": "90"})
            cli.panel_add("anna", settings={"HOME_LAT": "52.40", "HOME_LON": "14.05"})
            await app.poll_once(t + 20)
            assert app.lan is home and home.cfg.view_from == 90 and home.stats is stats
            anna = app.panels["anna"]
            assert anna.cfg.home_lon == 14.05 and anna.model.source == "mock"
            cli.panel_remove("anna")
            app.save()                                                # schreibt für anna nichts mehr: kein Fehler
            assert set(app.panels) == {"home"}
            cli.panel_add("anna")
            await app.poll_once(t + 40)
            assert app.panels["anna"] is not anna and app.panels["anna"].stats.dex == {}
        finally:
            cli.close()
            await app.session.close()

    asyncio.run(run())


def test_a_resting_panel_is_dark_and_outside_the_fetch(tmp_path):
    """home in der Ruhezeit, anna wach: Abgerufen wird nur für anna, home ist schwarz. Ruhen beide, ruft der Server
    nichts ab und sieht in einer Minute wieder nach."""
    panel_rows(tmp_path, anna={**ANNA, "QUIET_HOURS": "00:00-00:00"})
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 24 * 60),
                   route_adsbim=False, track_origin=False)
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return []

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            await app.poll_once(time.time())
            anna = app.panels["anna"]
            assert calls == [(anna.cfg.home_lat, anna.cfg.home_lon, anna.cfg.fetch_radius_km)]
            assert app.lan.model.stale and app.lan.model.empty
            anna.cfg = replace(anna.cfg, quiet_hours=(0, 24 * 60))   # nun ruhen beide
            assert await app.poll_once(time.time()) == REST_S and len(calls) == 1
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_panel_too_far_from_the_airport_stays_dark_and_widens_nothing(tmp_path):
    """Ein weiteres Panel in München (BER ist der Flughafen; panels.py ließe das nicht zu): Es ruft nicht ab, ist
    schwarz, /issues und Log sagen warum; der Abruf ist der von home allein."""
    panel_rows(tmp_path, far={"HOME_LAT": "48.35", "HOME_LON": "11.79"})
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                   route_adsbim=False, track_origin=False)
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return []

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            far = app.panels["far"]
            assert "PANEL_MAX_KM" in far.off and not far.active(time.time())
            assert "PANEL_MAX_KM" in app.issues.items["setting:far HOME"]["example"]["error"]
            await app.poll_once(time.time())
            home = app.lan.cfg
            assert calls == [(home.home_lat, home.home_lon, home.fetch_radius_km)]
            assert far.model.stale and app.health()["panels"]["far"]["off"] == far.off
        finally:
            await app.session.close()

    asyncio.run(run())


def test_only_the_lan_panels_esp32_counts_for_ok(tmp_path):
    """Ein Freund zieht den Stecker: kein Grund, den Container ungesund zu melden. Das ESP32 am LAN-Port schon."""
    panel_rows(tmp_path, anna=ANNA)
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0))

    async def run():
        app = App(cfg, env={})
        try:
            now = time.time()
            app.last_model_ok = now
            anna = app.panels["anna"]
            anna.frame_count, anna.last_frame_fetch = 5, now - 3600
            assert app.problems(now) == []
            app.lan.frame_count, app.lan.last_frame_fetch = 5, now - 3600
            assert any("ESP32" in p for p in app.problems(now))
            assert set(app.health()["panels"]) == {"home", "anna"}
        finally:
            await app.session.close()

    asyncio.run(run())
