// Vorschauseite (web.py, GET /): das Bild alle 2 s neu, alle 10 s ein Hinweis aus /health (Ruhezeit, alte Daten).
// Eine Datei statt eines Skripts in der Seite: Die Content-Security-Policy erlaubt nur Skripte vom Server selbst.
"use strict";

(() => {
  const img = document.getElementById("f");
  const base = img.getAttribute("src");          // /frame.png?layout=…&card=…, wie die Seite es vorgibt
  setInterval(() => {
    img.src = base + "&t=" + Date.now();
  }, 2000);

  const note = document.getElementById("s");
  const health = note.dataset.health;            // nur, wenn die Seite /health verlinkt (LAN-Port)
  if (!health) return;
  const status = () => fetch(health).then((r) => r.json()).then((h) => {
    note.textContent =
      h.brightness_now === 0 ? "Ruhezeit (QUIET_HOURS): Das Panel ist dunkel, der Server ruft nichts ab."
      : h.model_stale ? "Keine frischen Flugdaten: Das Bild ist gedimmt, siehe /health und /issues." : "";
  }).catch(() => {});
  status();
  setInterval(status, 10000);
})();
