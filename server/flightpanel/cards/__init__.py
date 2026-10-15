"""Die Leerlauf-Karten: was das Panel zeigt, wenn keine Airline im Sichtfeld ist.

Eine Karte sind zwei Funktionen und ein Eintrag in CARDS:

  <id>_cond(ctx) -> Hit | None     hat sie gerade etwas zu sagen? Hit(data, urgent, weight)
  <id>_draw(img, ctx, data, t_in, f)  zeichnet 128 x 32; t_in: Sekunden seit Beginn
                                   der Zeitscheibe (für Animationen), f: Helligkeit
  <id>_demo(ctx), optional         Beispieldaten für die Vorschau /frame.png?card=<id>

ctx (idle.Ctx) bringt Einstellungen, Display-Modell, Statistik, Wetter, ISS und
Logos mit. Stufen (tier) und Auswahl: idle.py und docs/screens.md. Jede Karte lässt
sich mit CARD_<ID>=0 abschalten.

  traffic.py  Notfall, nächster Flieger, Abflug, Landung, Radar
  runway.py   Landerichtung, Pistenwechsel, Wetter, Nachtflugverbot
  album.py    Tagesstatistik, Sammelalbum, neue und seltene Flugzeuge, Flaute
  sky.py      Uhr, ISS, Sonnenauf- und -untergang
  fun.py      Flugzeug und Taube, Weihnachten und Neujahr
"""

from ..idle import Card
from . import album, fun, runway, sky, traffic

ALERT = Card("alert", traffic.alert_cond, traffic.alert_draw, demo=traffic.alert_demo, tier=0, group="notfall",
             live=True)

CARDS = [
    ALERT,
    # Stufe 2: Verkehr am Platz
    Card("departures", traffic.departures_cond, traffic.departures_draw, weight=3.0, tier=2, group="nützlich",
         live=True, demo=traffic.departures_demo),
    Card("arrivals", traffic.arrivals_cond, traffic.arrivals_draw, weight=2.5, tier=2, group="nützlich", live=True,
         demo=traffic.arrivals_demo),
    Card("next", traffic.next_cond, traffic.next_draw, weight=3.0, tier=2, group="nützlich", live=True,
         demo=traffic.next_demo),
    Card("runway_change", runway.runway_change_cond, runway.runway_change_draw, tier=2, group="nützlich",
         demo=runway.runway_change_demo),
    # Stufe 3: wenig los; Saison an ihren Tagen auch, wenn etwas los ist
    Card("clock", sky.clock_cond, sky.clock_draw, tier=3, group="uhr"),
    Card("radar", traffic.radar_cond, traffic.radar_draw, weight=1.5, tier=3, group="nützlich", live=True,
         demo=traffic.radar_demo),
    Card("runway", runway.runway_cond, runway.runway_draw, weight=1.5, tier=3, group="nützlich",
         demo=runway.runway_demo),
    Card("weather", runway.weather_cond, runway.weather_draw, tier=3, group="nützlich", demo=runway.weather_demo),
    Card("curfew", runway.curfew_cond, runway.curfew_draw, tier=3, group="nützlich", demo=runway.curfew_demo),
    Card("scoreboard", album.scoreboard_cond, album.scoreboard_draw, weight=1.2, tier=3, group="nerdig",
         demo=album.scoreboard_demo),
    Card("pokedex", album.pokedex_cond, album.pokedex_draw, weight=1.0, tier=3, group="nerdig",
         demo=album.pokedex_demo),
    Card("new", album.new_cond, album.new_draw, tier=3, group="nerdig", demo=album.new_demo),
    Card("rare", album.rare_cond, album.rare_draw, tier=3, group="nerdig", demo=album.rare_demo),
    Card("quiet", album.quiet_cond, album.quiet_draw, weight=1.0, tier=3, group="nerdig", live=True,
         demo=album.quiet_demo),
    Card("iss", sky.iss_cond, sky.iss_draw, tier=3, group="himmel", demo=sky.iss_demo),
    Card("tiangong", sky.tiangong_cond, sky.tiangong_draw, tier=3, group="himmel", demo=sky.tiangong_demo),
    Card("starlink", sky.starlink_cond, sky.starlink_draw, weight=3.0, tier=3, group="himmel",
         demo=sky.starlink_demo),
    Card("sunset", sky.sunset_cond, sky.sunset_draw, tier=3, group="himmel", demo=sky.sunset_demo),
    Card("sunrise", sky.sunrise_cond, sky.sunrise_draw, tier=3, group="himmel", demo=sky.sunrise_demo),
    Card("santa", fun.santa_cond, fun.santa_draw, tier=3, group="saison", demo=fun.santa_demo),
    # Neujahr; um Mitternacht zeigt render.py das Feuerwerk ein paar Minuten vor allem außer dem Notfall
    Card("fireworks", fun.fireworks_cond, fun.fireworks_draw, tier=3, group="saison", demo=lambda ctx: {}),
    # Stufe 4: Spielereien, nur wenn gar nichts los ist
    Card("plane", fun.plane_cond, fun.plane_draw, weight=0.5, tier=4, group="spaß"),
    Card("pigeon", fun.pigeon_cond, fun.pigeon_draw, weight=0.3, tier=4, group="spaß"),
]

# Für Menschen: Name und ein Satz je Karte, Name je Gruppe (Vorschau und Einstellungsseite)
ABOUT = {
    "alert": ("Notfall in der Nähe", "Ein Flugzeug in der Nähe meldet einen Notfall. Kommt vor allem anderen."),
    "departures": ("Abflüge", "Wer gerade am Flughafen startet oder eben gestartet ist."),
    "arrivals": ("Landefolge", "Wer als Nächstes landet, mit Herkunft und Minuten bis zum Aufsetzen."),
    "next": ("Gleich im Blick", "Welches Flugzeug als Nächstes ins Sichtfeld kommt, und wann."),
    "runway_change": ("Pistenwechsel", "Wenn am Flughafen die Landerichtung gewechselt hat."),
    "clock": ("Uhr", "Uhrzeit und Datum."),
    "radar": ("Radar", "Rundumblick mit allen Flugzeugen, das Sichtfeld als Band in der Mitte."),
    "runway": ("Landerichtung", "Aus welcher Richtung gerade gelandet wird."),
    "weather": ("Wetter", "Wenn Nebel oder Wolken erklären, warum am Himmel nichts zu sehen ist."),
    "curfew": ("Nachtflugverbot", "Während der Nachtruhe am Flughafen, mit ihrem Ende."),
    "scoreboard": ("Tageszahlen", "Flugzeuge und Airlines des Tages und der höchste Flug."),
    "pokedex": ("Sammelalbum", "Wie viele Airlines schon vorbeikamen, und die neueste."),
    "new": ("Neue Airline", "Eine Airline, die das Panel zum ersten Mal sieht."),
    "rare": ("Seltener Besuch", "Ein seltener Flugzeugtyp oder Betreiber in der Nähe."),
    "quiet": ("Flaute", "Wie lange schon kein Flugzeug zu sehen war."),
    "iss": ("ISS", "Wann die Raumstation sichtbar vorbeizieht."),
    "tiangong": ("Tiangong", "Wann die chinesische Raumstation sichtbar vorbeizieht."),
    "starlink": ("Starlink-Kette", "Eine frisch gestartete Reihe von Starlink-Satelliten zieht als Lichterkette "
                 "vorbei."),
    "sunset": ("Sonnenuntergang", "Kurz bevor die Sonne untergeht."),
    "sunrise": ("Sonnenaufgang", "Kurz bevor die Sonne aufgeht."),
    "santa": ("Weihnachten", "An den Weihnachtstagen."),
    "fireworks": ("Neujahr", "Feuerwerk um Mitternacht an Silvester."),
    "plane": ("Flugzeug", "Ein kleines Flugzeug zieht vorbei, wenn gar nichts los ist."),
    "pigeon": ("Taube", "Eine Taube, wenn gar nichts los ist."),
}
GROUPS = {"notfall": "Notfall", "nützlich": "Am Flughafen", "uhr": "Uhr", "nerdig": "Statistik",
          "himmel": "Am Himmel", "saison": "Feiertage", "spaß": "Spielereien"}
