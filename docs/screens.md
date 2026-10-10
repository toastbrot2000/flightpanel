# Screens und Prioritäten

Was das Panel zeigt, in festen Stufen: Eine Stufe kommt nur dran, wenn keine höhere etwas zu zeigen hat. Flüge (Stufe 1) zeigt `render.py`, alles andere sind Leerlauf-Karten (`server/flightpanel/cards/`, Auswahl in `idle.py`).

**Auswahl:** Innerhalb einer Stufe wechseln die Karten alle `IDLE_SECONDS` (12 s, ruhiger Modus 30 s): Dringendes zuerst (mehrere wechseln sich ab), sonst gewichteter Zufall, ohne die zuletzt gezeigten. Die Uhr läuft in jeder Leerlauf-Stufe mit, in etwa einem Drittel der Fälle (`IDLE_CLOCK_SHARE`). Dringend sind: neue Airline (2 min), Flieger in unter 3 min, seltener Typ (2 min), ISS gerade über dem Haus.

**Abschalten:** Jede Karte mit `CARD_<ID>=0` in `.env`, auch Uhr und Notfall. Hat keine eingeschaltete Karte etwas zu zeigen, bleibt das Panel dunkel. `txl` und `ber_opening` laufen nur, wenn der Heimatflughafen BER ist; die Witze und Verspätungsgründe aus `data/jokes.json` nur an dem Flughafen, für den die Datei geschrieben ist.

**Ruhiger Modus (`CALM=1`):** Nichts blinkt außer dem Notfall, Infoseiten 10 s, jeder Flug mindestens 20 s, Leerlauf-Karten 30 s, in der Infozeile nur Landezeit und Höhe/Tempo. Einzeln: `PAGE_SECONDS`, `FLIGHT_SECONDS`, `INFO_PAGES`, `BLINK`, `IDLE_SECONDS`.

`/idle` zeigt alle Karten mit Stufe, ob sie gerade etwas zu sagen haben und welche dran ist; `/frame.png?card=<id>` zeigt eine Karte, mit Beispieldaten, wenn sie gerade nichts zu sagen hat.

| Stufe | Karte | zeigt | wann |
|---|---|---|---|
| **0 Notfall** | `alert` | Squawk und Bedeutung, Callsign, Typ, wo, ob im Blick | Squawk 7700/7600/7500 im Abrufradius, vor allem anderen, auch vor Flügen |
| **1 Im Sichtfeld** | Flug (Layout C/A/B) | Airline, Route, Typ, wechselnd Landezeit, Höhe und Tempo, Entfernung und Kurs, Rufzeichen; Himmelspunkte in Landefolge | Airline im Sichtfeld oder fliegt bis zum nächsten Abruf hinein |
| **2 Verkehr am Platz** | `departures` | "↑ Abflug BER", Piste; bis zu zwei Flüge mit Ziel und "rollt" / "startet" / "vor 2 min" | am Boden mit Route ab hier (rollt ab 5 kt, startet ab 40 kt), oder in der Luft unter 3000 m, steigend, vom Platz weg, vor höchstens ~5 min gestartet |
| | `arrivals` | "↓ Landung BER", Piste; die nächsten zwei mit Herkunft und "~4 min" | in der Landefolge, landet in den nächsten 15 min |
| | `next` | nächster Flieger: "Sichtbar in ~3 min" (Zeit bis zum Sichtfeld, nicht bis zur Landung), Seite des Fensters (← →) | kommt in 15 s bis 10 min ins Sichtfeld, unter 3 min dringend; Landende über den Endanflug gerechnet |
| | `private` | Privatflieger, Hubschrauber, Polizei, Militär wie ein Flug | im Sichtfeld und keine Airline zu sehen |
| | `runway_change` | Pistenwechsel, was er fürs Fenster heißt | bis 1 h nach einem Wechsel (drei Abrufe in Folge bestätigt) |
| **3 Wenig los** | `radar` | Rundumblick, Sichtfeld als Band, Punkte nach Richtung und Höhenwinkel; der nächste blinkt | Flugzeuge im Radius |
| | `runway` | "BER landet auf 25", Wind, ob Landungen oder Starts durchs Sichtfeld kommen | Landerichtung seit 30 min stabil, in den letzten 2 h bestätigt, laut METAR kein Rückenwind |
| | `weather` | warum der Himmel leer ist: Gewitter, Nebel, tiefe Wolken, Schnee; wie viele darüber fliegen | METAR erklärt es (Sicht unter 2 km, Wolken unter 1500 m) |
| | `curfew` | Nachtflugverbot bis … | `CURFEW_HOURS` (BER 0 bis 5 Uhr), wenn `QUIET_HOURS` das Panel dann nicht abschaltet |
| | `scoreboard` | Tagesstatistik: Flugzeuge, Airlines, wie viele im Sichtfeld, das höchste | ab 10 Flugzeugen am Tag |
| | `pokedex` | Sammelalbum: gesehene Marken aus `airlines.json`, dazu Exoten | ab der ersten Airline |
| | `new` | "NEU!" mit Logo | Airline zum ersten Mal gesehen, nicht in den ersten 6 h nach dem ersten Start |
| | `rare` | "Weit weg, aber selten: A380 Emirates, 70 km SO" | Typ oder Betreiber aus `data/rare.json` im Abrufradius |
| | `quiet` | Flaute und Tagesrekord | ab 10 min ohne Flugzeug im Sichtfeld; nach einer Abruflücke beginnt die Uhr neu |
| | `iss` | ISS-Überflug: Richtung, Höhe, ob durchs Fenster | sichtbarer Überflug in 30 min, oder gerade (dringend) |
| | `sunset`, `sunrise` | Uhrzeit; "Gegenlicht", wenn die Sonne im Sichtfeld steht | 2 h vor Sonnenuntergang, 90 min vor Sonnenaufgang |
| | `santa`, `fireworks` | Schlitten 1 im Anflug, Feuerwerk | 24.12. ab 15 Uhr, Silvester ab 18 Uhr |
| | `clock` | Uhr und Datum | immer |
| **4 Gar nichts los** | `joke`, `delay` | Witze, echte Verspätungsgründe des Flughafens (`data/jokes.json`); Jahrestage an ihrem Tag öfter | nur wenn gar nichts los ist (siehe unten), dann gemischt mit Stufe 3 |
| | `plane`, `pigeon` | Animationen | wie oben |
| | `txl`, `ber_opening` | Tegel, BER-Eröffnung | wie oben, nur am BER |

## Entscheidungen

10. Oktober 2026:

1. **Gar nichts los**: seit 15 Minuten kein Start und keine Landung am Platz, und in den nächsten 10 Minuten landet keiner. Erst dann kommen Spielereien. Start = ein Flug in der Abflug-Liste, Landung = Landezeit unter 90 s; der Server merkt sich die letzte Bewegung (`last_airport_move`).
2. **Abflug und Landung** als eigene Karten in Stufe 2. Am Boden zählt als Abflug nur, wer laut Route hier abfliegt: Rollen nach der Landung sähe sonst genauso aus.
3. **Privatflieger, Hubschrauber, Polizei, Militär** als Karte in Stufe 2, nicht als Flug in Stufe 1.
4. **Saison-Karten** in Stufe 3: an ihren Tagen auch, wenn etwas los ist, aber nach Flügen und Verkehr am Platz.
5. **Radar** in Stufe 3 statt 2: Es hat fast immer etwas zu zeigen und hätte Stufe 3 sonst tagsüber nie drangelassen.
6. **Alles abschaltbar** (`CARD_<ID>=0`), und der **ruhige Modus** (`CALM=1`) für weniger Unruhe: kein Blinken außer beim Notfall, seltener Wechsel, nur Landezeit und Höhe/Tempo.
7. **Heimatflughafen einstellbar**: Texte nennen den eingestellten Flughafen; was nur für Berlin stimmt, läuft nur am BER.
