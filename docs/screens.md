# Screens und Prioritäten

Stand 10. Oktober 2026. Was das Panel zeigt, in festen Stufen: Eine Stufe kommt nur dran, wenn keine höhere etwas zu zeigen hat. Innerhalb einer Stufe wechseln die Screens alle `IDLE_SECONDS` (12 s): Dringendes zuerst, sonst gewichteter Zufall, ohne die zuletzt gezeigten. Die Uhr läuft in jeder Leerlauf-Stufe mit (`IDLE_CLOCK_SHARE`, etwa ein Drittel). Dringende Karten (ISS gerade über dem Haus, neue Airline, Flieger in unter 3 min) brechen durch jede Leerlauf-Stufe.

Code: Stufe pro Karte in `cards.py` (`tier=`), Auswahl in `idle.py` (`IdleScreen.pick`, `Ctx.quiet`). `/idle` zeigt alle Karten mit Stufe, ob sie gerade etwas zu sagen haben und welche dran ist.

| Stufe | Screen | zeigt | wann |
|---|---|---|---|
| **0 Notfall** | `alert` | Squawk 7700/7600/7500, Callsign, Typ, wo | Notfall-Squawk im Abrufradius, vor allem anderen |
| **1 Im Sichtfeld** | Flug (Layout C/A) | Airline, Route, Typ, Höhe, Entfernung, Landezeit; Punkte in Landefolge | Airline im Sichtfeld oder fliegt bis zum nächsten Abruf hinein |
| **2 Verkehr am BER** | `departures` | "↑ Abflug BER", Piste; bis zu zwei Flüge mit Ziel und "rollt" / "startet" / "vor 2 min" | am Boden am BER mit Route ab BER (rollt ab 5 kt, startet ab 40 kt), oder in der Luft unter 3000 m, steigend, vom Platz weg, in den letzten ~5 min gestartet |
| | `arrivals` | "↓ Landung BER", Piste; die nächsten zwei mit Herkunft und "~4 min" | in der Landefolge, landet in den nächsten 15 min |
| | `next` | nächster Flieger: "Sichtbar in ~3 min", von welcher Seite | kommt in 15 s bis 10 min ins Sichtfeld (unter 3 min dringend) |
| | `private` | Privatflieger, Hubschrauber, Polizei, Militär wie ein Flug | im Sichtfeld |
| | `runway_change` | Pistenwechsel, was er fürs Fenster heißt | bis 1 h nach einem Wechsel |
| **3 Wenig los** | `radar` | Rundumblick, Sichtfeld als Band, Punkte | Flugzeuge im Radius |
| | `runway` | "BER landet auf 25", Wind, ob An- oder Abflüge durchs Fenster kommen | Landerichtung stabil |
| | `weather` | warum der Himmel leer ist (Gewitter, Nebel, tiefe Wolken) | METAR erklärt es |
| | `rare`, `new` | seltener Typ oder Betreiber in Reichweite; Airline zum ersten Mal gesehen | wie gehabt, kurz dringend |
| | `iss`, `sunset`, `sunrise` | ISS-Überflug, Sonnenauf- und -untergang, Gegenlicht | wie gehabt |
| | `scoreboard`, `pokedex`, `quiet` | Tagesstatistik, Sammelalbum, Flaute | wie gehabt |
| | `curfew` | Nachtflugverbot | 0 bis 5 Uhr |
| | `santa`, `fireworks` | Saison | 24.12. ab 15 Uhr, Silvester |
| | `clock` | Uhr und Datum | immer |
| **4 Gar nichts los** | `joke`, `delay`, `txl`, `ber_opening`, `plane`, `pigeon` | Witze, echte Verspätungsgründe vom BER, Tegel, BER-Eröffnung, Animationen | nur wenn gar nichts los ist (siehe unten), dann gemischt mit Stufe 3 |

## Entscheidungen (10. Oktober 2026)

1. **Gar nichts los**: seit 15 Minuten kein Start und keine Landung am BER, und in den nächsten 10 Minuten landet keiner. Erst dann kommen Spielereien. Start = ein Flug in der Abflug-Liste, Landung = Landezeit unter 90 s; der Server merkt sich die letzte Bewegung (`last_ber_move`).
2. **Abflug und Landung BER** als eigene Karten in Stufe 2. Am Boden zählt als Abflug nur, wer laut Route am BER abfliegt: Rollen nach der Landung sähe sonst genauso aus.
3. **Privatflieger, Hubschrauber, Polizei, Militär** als Karte in Stufe 2, nicht als Flug in Stufe 1.
4. **Saison-Karten** in Stufe 3: an ihren Tagen auch, wenn etwas los ist, aber nach Flügen und BER-Verkehr.
5. **Radar** in Stufe 3 statt 2: Es hat fast immer etwas zu zeigen und hätte Stufe 3 sonst tagsüber nie drangelassen; Stufe 2 bleibt Verkehr am BER.
