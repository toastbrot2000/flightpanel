"""panels.py: Einstellungen je Panel setzen und prüfen, Panels anlegen und löschen."""

import re
from dataclasses import fields

import pytest

from flightpanel import panels
from flightpanel.config import Config
from flightpanel.db import Db


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """panels.main() liest os.environ: Eine exportierte .env des Entwicklers (LAN_PANEL, LOCAL_AIRPORT_IATA ...)
    soll nicht ändern, was die Befehle tun. Welche Variablen es gibt, sagt Config."""
    for f in fields(Config):
        if f.metadata.get("env"):
            monkeypatch.delenv(f.metadata["env"], raising=False)


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    Db(str(tmp_path / "flightpanel.db")).panel_add("home")        # legt sonst der Server an

    def call(*argv):
        code = panels.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


def test_set_show_unset(run):
    code, out, _ = run("set", "home", "home_lat=52.5", "VIEW_FROM=110", "CARD_PIGEON=0")
    assert code == 0 and "3 Einstellungen" in out
    assert run("show", "home")[1] == "CARD_PIGEON=0\nHOME_LAT=52.5\nVIEW_FROM=110\n"
    assert run("set", "home", "VIEW_FROM=")[0] == 0                   # leer: zurück auf den Default
    assert run("unset", "home", "card_pigeon")[0] == 0
    assert run("show", "home")[1] == "HOME_LAT=52.5\n"


def test_bad_values_name_the_setting_and_change_nothing(run):
    code, _, err = run("set", "home", "HOME_LAT=hier")
    assert code == 2 and "HOME_LAT='hier'" in err
    code, _, err = run("set", "home", "DATA_DIR=/x")
    assert code == 2 and "keine Panel-Einstellung: DATA_DIR" in err
    code, _, err = run("set", "home", "HOME_LAT")
    assert code == 2 and "KEY=WERT" in err
    code, _, err = run("set", "nirgends", "HOME_LAT=1")
    assert code == 2 and "gibt es nicht" in err
    assert run("show", "home")[1].startswith("home: nichts gesetzt")


def test_keys_lists_every_panel_setting_with_its_default(run):
    _, out, _ = run("keys")
    assert re.search(r"^HOME_LAT\s+52\.52\s+Standort: ", out, re.M)
    assert re.search(r"^QUIET_HOURS\s+00:00-06:30\s+Helligkeit: ", out, re.M)
    assert "CARD_<ID>" in out and "DATA_DIR" not in out and "POLL_INTERVAL" not in out


def test_add_list_remove(run):
    assert run("add", "anna", "--name", "Anna")[0] == 0
    assert run("add", "Anna!")[0] == 2 and run("add", "anna")[0] == 2
    _, out, _ = run("list")
    assert "home" in out and "anna" in out and "Anna" in out and "LAN-Port" in out
    code, _, err = run("remove", "anna")
    assert code == 2 and "--yes" in err
    assert run("remove", "anna", "--yes")[0] == 0 and "anna" not in run("list")[1]
    assert run("remove", "home", "--yes")[0] == 2                      # das Panel am LAN-Port bleibt


def test_unknown_cards_are_refused(run):
    """Ein Tippfehler in CARD_<ID> bliebe sonst still wirkungslos."""
    code, _, err = run("set", "home", "CARD_PIGEN=0")
    assert code == 2 and "unbekannte Karte: CARD_PIGEN" in err and "pigeon" in err
    assert run("set", "home", "card_pigeon=0")[0] == 0


@pytest.mark.parametrize("argv, wanted", [
    (("set", "home", "DATA_DIR="), "keine Panel-Einstellung: DATA_DIR"),
    (("set", "home", "HOME_LATT="), "keine Panel-Einstellung: HOME_LATT"),
    (("unset", "home", "HOME_LATT", "DATA_DIR"), "keine Panel-Einstellung: DATA_DIR, HOME_LATT"),
    (("unset", "home", "HOME_LAT=52.5"), "keine Panel-Einstellung: HOME_LAT=52.5"),        # KEY=WERT in unset
    (("set", "home", "CARD_PIGEN="), "unbekannte Karte: CARD_PIGEN"),
    (("unset", "home", "card_pigen"), "unbekannte Karte: CARD_PIGEN"),
    # ein Treffer, ein Tippfehler: es wird nichts gespeichert, auch der Treffer bleibt
    (("unset", "home", "HOME_LAT", "HOME_LATT"), "keine Panel-Einstellung: HOME_LATT"),
    (("set", "home", "HOME_LAT=", "HOME_LATT="), "keine Panel-Einstellung: HOME_LATT"),
])
def test_resetting_checks_the_name_and_changes_nothing(run, argv, wanted):
    """Auch zurücksetzen (KEY= leer, unset) prüft den Namen wie set: Ein Tippfehler, ein Server-Schlüssel oder KEY=WERT
    in unset bliebe sonst still wirkungslos. Gespeichert wird erst, wenn alle Namen stimmen."""
    assert run("set", "home", "HOME_LAT=52.5")[0] == 0
    code, out, err = run(*argv)
    assert code == 2 and wanted in err and out == ""
    assert run("show", "home")[1] == "HOME_LAT=52.5\n"


def test_a_stale_stored_key_can_still_be_removed(run, tmp_path):
    """Was eine spätere Fassung nicht mehr kennt, steht noch in der Datenbank und hält den Server vom Start ab:
    unset und KEY= räumen es weg, solange es gespeichert ist."""
    Db(str(tmp_path / "flightpanel.db")).panel_set_settings(
        "home", {"HOME_LAT": "52.5", "OLD_SETTING": "1", "CARD_GONE": "0"})
    assert run("unset", "home", "old_setting")[0] == 0
    assert run("show", "home")[1] == "CARD_GONE=0\nHOME_LAT=52.5\n"
    assert run("set", "home", "card_gone=")[0] == 0                        # eine Karte, die es nicht mehr gibt
    assert run("show", "home")[1] == "HOME_LAT=52.5\n"
    code, _, err = run("unset", "home", "old_setting")                     # weg: wieder nur ein unbekannter Name
    assert code == 2 and "keine Panel-Einstellung: OLD_SETTING" in err


def test_backup(run, tmp_path):
    assert run("backup", str(tmp_path / "copy.db"))[0] == 0 and (tmp_path / "copy.db").exists()


def test_a_backup_that_cannot_be_written_is_reported(run, tmp_path):
    """Ein Tippfehler im Zielverzeichnis ist eine Meldung, kein Traceback."""
    code, _, err = run("backup", str(tmp_path / "gibt-es-nicht" / "copy.db"))
    assert code == 2 and err.startswith("Fehler: ")


def test_a_broken_database_is_reported_and_left_alone(tmp_path, monkeypatch, capsys):
    """Der Befehl läuft neben dem Server: eine kaputte Datei nennt er und lässt sie liegen."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    junk = "kaputt, keine Datenbank " * 100
    (tmp_path / "flightpanel.db").write_text(junk)
    assert panels.main(["list"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Fehler: ") and "flightpanel.db" in err and "Traceback" not in err
    assert (tmp_path / "flightpanel.db").read_text() == junk and not list(tmp_path.glob("*.broken*"))
