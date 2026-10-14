// Einstellungsseite (settings.py, GET /settings): speichert als JSON, zeigt Fehler am Feld, übernimmt eingefügte
// Koordinaten oder den Standort des Telefons und eicht das Sichtfeld mit den Flugzeugen aus display.json, als Liste
// und als Kompass. Eine Datei statt eines Skripts in der Seite: Die Content-Security-Policy erlaubt nur Skripte vom
// Server selbst. Was von außen kommt (Callsigns und Typen aus ADS-B, Fehlertexte mit dem Getippten), kommt nur als
// textContent auf die Seite, nie als HTML.
"use strict";

(() => {
  const form = document.getElementById("settings");
  const status = document.getElementById("status");
  const saveButton = form.querySelector(".save");
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let dirty = false;
  let saving = false;

  // ------------------------------------------------------------------------------------------ Speichern --
  function say(text, tone) {
    status.textContent = text;
    status.className = tone ? "status " + tone : "status";
  }

  function changed() {
    dirty = true;
    say("Noch nicht gespeichert.", "dirty");
  }

  // Das Bedienelement einer Zeile, das man sehen und antippen kann (nicht das versteckte Feld dahinter)
  function visibleControl(row) {
    return row.querySelector("input:not([type=hidden]), select, textarea");
  }

  function clearError(row) {
    row.classList.remove("invalid");
    const error = row.querySelector(".error");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    const control = visibleControl(row);
    if (control) control.removeAttribute("aria-invalid");
  }

  function showError(key, message) {
    const row = typeof key === "string" ? document.getElementById("row-" + key) : null;
    const error = row && row.querySelector(".error");
    if (!error) {
      say(message, "bad");
      return;
    }
    const group = row.closest("details");
    if (group) group.open = true;
    error.textContent = friendly(message);
    error.hidden = false;
    row.classList.add("invalid");
    const control = visibleControl(row);
    if (control) {
      control.setAttribute("aria-invalid", "true");
      control.focus({ preventScroll: true });
    }
    row.scrollIntoView({ block: "center" });      // sofort: ein sanftes Scrollen bricht die Liste beim Eichen ab
    say("Nicht gespeichert, ein Feld passt nicht.", "bad");
  }

  // Am Feld ohne den Namen und das Getippte davor, die stehen ja daneben: "HOME_LAT='95': erlaubt: -90 bis 90"
  // wird "Erlaubt: -90 bis 90."
  function friendly(message) {
    const text = message.replace(/^[A-Z][A-Z0-9_]*='(?:[^']|'(?!:))*':\s*/, "").trim();
    if (!text) return message;
    return text.charAt(0).toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? "" : ".");
  }

  function flash(row) {
    if (!row) return;
    row.classList.remove("took");
    void row.offsetWidth;                       // die Animation neu starten
    row.classList.add("took");
  }

  function setField(key, value) {
    const control = document.getElementById("f-" + key);
    if (!control) return;
    control.value = value;
    const row = document.getElementById("row-" + key);
    if (row) {
      clearError(row);
      flash(row);
    }
    changed();
    drawCompass();
  }

  // Alle Felder: leer heißt Standard (null). Wer data-default trägt (Schalter, Regler, Uhrzeiten, Aufteilung,
  // Infoseiten), zeigt den Standard als Wert; steht er noch da, geht auch null.
  function collect() {
    const settings = {};
    for (const control of form.querySelectorAll("[data-key]")) {
      const key = control.dataset.key;
      const fallback = control.dataset.default;
      let value = control.type === "checkbox" ? (control.checked ? "1" : "0") : control.value.trim();
      if (value === "" || (fallback !== undefined && value === fallback)) value = null;
      settings[key] = value;
    }
    return settings;
  }

  async function save(event) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    for (const row of form.querySelectorAll(".invalid")) clearError(row);
    saveButton.disabled = true;
    saveButton.textContent = "Speichere …";
    say("Speichere …", "busy");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: collect() }),
      });
      let answer = {};
      try {
        answer = await response.json();
      } catch (e) {
        // keine JSON-Antwort, etwa ein Fehler unterwegs: unten der Status
      }
      if (response.ok && answer.ok === true) {
        dirty = false;
        say("Gespeichert. Das Panel zeigt es gleich.", "good");
      } else {
        showError(answer.key, typeof answer.error === "string" ? answer.error
          : "Nicht gespeichert (HTTP " + response.status + ").");
      }
    } catch (e) {
      say("Keine Verbindung zum Server, nichts gespeichert.", "bad");
    } finally {
      saving = false;
      saveButton.disabled = false;
      saveButton.textContent = "Speichern";
    }
  }

  form.addEventListener("submit", save);
  for (const type of ["input", "change"]) {
    form.addEventListener(type, (event) => {
      const control = event.target;
      const row = control.closest && control.closest(".field");
      if (!row || control.id === "paste") return;   // Einfügen setzt selbst
      if (row.classList.contains("invalid")) clearError(row);
      changed();
    });
  }
  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // ------------------------------------------------------------------- Zusammengesetzte Bedienelemente --
  // Sie schreiben ihren Wert in das versteckte Feld mit data-key, das collect() liest.
  const hiddenFor = (box) => document.getElementById("f-" + box.dataset.for);

  for (const box of form.querySelectorAll(".layouts")) {
    box.addEventListener("change", (event) => {
      if (event.target.type === "radio") hiddenFor(box).value = event.target.value;
    });
  }

  for (const box of form.querySelectorAll(".check-chips")) {
    box.addEventListener("change", () => {
      const picked = [...box.querySelectorAll("input:checked")].map((input) => input.value);
      hiddenFor(box).value = picked.join(",");
    });
  }

  // Uhrzeiten; bei der Ruhezeit mit Schalter, aus ist ein leeres Fenster (00:00-00:00)
  function composeTimes(box) {
    const [from, to] = [...box.querySelectorAll("input[type=time]")].map((input) => input.value);
    hiddenFor(box).value = from && to ? from + "-" + to : "";
  }
  for (const box of form.querySelectorAll(".times")) {
    box.addEventListener("change", () => composeTimes(box));
  }
  for (const toggle of form.querySelectorAll(".times-on")) {
    const box = form.querySelector('.times[data-for="' + toggle.dataset.for + '"]');
    toggle.addEventListener("change", () => {
      box.hidden = !toggle.checked;
      if (toggle.checked) composeTimes(box);
      else hiddenFor(box).value = "00:00-00:00";
    });
  }

  // Regler: Anzeige in Prozent des Maximums
  for (const range of form.querySelectorAll("input[type=range]")) {
    const out = document.getElementById("out-" + range.dataset.key);
    const show = () => {
      const share = Number(range.value) / Number(range.max || 1);
      out.textContent = Math.round(share * 100) + " %";
    };
    range.addEventListener("input", show);
    show();
  }

  // Sprungmarken: die Gruppe aufklappen, dann hinscrollen
  for (const link of document.querySelectorAll(".jump a")) {
    link.addEventListener("click", (event) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.open = true;
      target.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" });
    });
  }

  // --------------------------------------------------------------------------- Koordinaten einfügen --
  // "52.52, 13.40" oder "52.52 13.40", wie Karten-Apps sie kopieren: Breite und Länge mit Punkt.
  const COORDINATES = /^\s*(-?\d{1,3}(?:\.\d+)?)\s*(?:,\s*|\s+)(-?\d{1,3}(?:\.\d+)?)\s*$/;
  const paste = document.getElementById("paste");
  const pasteNote = document.getElementById("paste-note");
  const pasteHint = pasteNote.textContent;

  function pasteSay(text, tone) {
    pasteNote.textContent = text;
    pasteNote.className = tone ? "hint " + tone : "hint";
  }

  paste.addEventListener("input", () => {
    const text = paste.value;
    const match = COORDINATES.exec(text);
    if (!text.trim()) {
      pasteSay(pasteHint);
      return;
    }
    if (!match || Math.abs(Number(match[1])) > 90 || Math.abs(Number(match[2])) > 180) {
      pasteSay("Erwartet Breite und Länge mit Punkt, etwa 52.52, 13.40.", "bad");
      return;
    }
    setField("HOME_LAT", match[1]);
    setField("HOME_LON", match[2]);
    pasteSay("Übernommen: Breite " + match[1] + ", Länge " + match[2] + ".", "good");
  });

  // Standort des Telefons: nur über HTTPS (der Zugang von außen), im Heimnetz über HTTP gibt es ihn nicht
  const locate = document.getElementById("locate");
  if (window.isSecureContext && "geolocation" in navigator) {
    locate.hidden = false;
    locate.addEventListener("click", () => {
      locate.disabled = true;
      pasteSay("Frage den Standort ab …");
      navigator.geolocation.getCurrentPosition((position) => {
        locate.disabled = false;
        const lat = position.coords.latitude.toFixed(5);
        const lon = position.coords.longitude.toFixed(5);
        setField("HOME_LAT", lat);
        setField("HOME_LON", lon);
        pasteSay("Übernommen: Breite " + lat + ", Länge " + lon + " (auf etwa "
          + Math.round(position.coords.accuracy) + " m genau).", "good");
      }, () => {
        locate.disabled = false;
        pasteSay("Kein Standort: Der Browser hat ihn nicht freigegeben. Koordinaten lassen sich auch einfügen.",
          "bad");
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
    });
  }

  // -------------------------------------------------------------------------------------------- Eichen --
  const calib = document.getElementById("calib");
  const planes = document.getElementById("planes");
  const planesNote = document.getElementById("planes-note");
  const calibNote = document.getElementById("calib-note");
  const compass = document.getElementById("compass");
  const MAX_PLANES = 8;
  const REFRESH_MS = 5000;
  const HOLD_MS = 4000;                        // nach dem Antippen: die Liste steht still, nichts verrutscht
  const decimal = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
  const WINDS = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const VIEW = { now: ["im Sichtfeld", "view now"], soon: ["gleich im Sichtfeld", "view soon"] };
  const ACTIONS = [                            // Feld, Wert, Knopf, was es ist
    ["VIEW_FROM", "az", "Links", "Linker Rand des Sichtfelds"],
    ["VIEW_TO", "az", "Rechts", "Rechter Rand des Sichtfelds"],
    ["VIEW_MIN_ELEV", "elev", "Höhe", "Mindesthöhe"],
  ];
  let holdUntil = 0;
  let inView = true;
  let lastPlanes = [];

  const finite = (v) => typeof v === "number" && Number.isFinite(v);

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function note(text) {
    if (planesNote.textContent !== text) planesNote.textContent = text;
  }

  // Ein Feld als Zahl: was drinsteht, sonst der Standard aus dem Platzhalter
  function number(key) {
    const control = document.getElementById("f-" + key);
    if (!control) return NaN;
    const text = control.value.trim() || control.placeholder || "";
    return Number(text);
  }

  function plane(p) {
    const az = Math.round(p.az) % 360;
    const elev = Math.round(p.elev * 10) / 10;
    const head = node("div", "plane-head");
    head.append(node("span", "cs", String(p.callsign || p.icao24 || "?")));
    if (p.type) head.append(node("span", "type", String(p.type)));
    const view = VIEW[p.in_view];
    head.append(node("span", view ? view[1] : "view", view ? view[0] : "außerhalb"));
    const data = node("div", "plane-data");     // umbrochen wird nur zwischen den Angaben, nie vor "km"
    const facts = ["Richtung " + az + "° " + WINDS[Math.round(az / 22.5) % 16],
      "Höhenwinkel " + decimal.format(elev) + "°", decimal.format(p.dist_km) + " km"];
    facts.forEach((text, i) => {
      if (i) data.append(" · ");
      data.append(node("span", "fact", text));
    });
    const actions = node("div", "plane-actions");
    for (const [key, what, label, name] of ACTIONS) {
      const button = node("button", "", label);
      button.type = "button";
      button.dataset.set = key;              // kein data-key: das wäre ein Feld des Formulars
      button.dataset.value = String(what === "az" ? az : elev);
      button.dataset.name = name;
      button.setAttribute("aria-label", name + " übernehmen: " + button.dataset.value + "°");
      actions.append(button);
    }
    const item = node("li", "plane");
    item.append(head, data, actions);
    return item;
  }

  function show(nearby) {
    const usable = nearby.filter((p) => p && finite(p.az) && finite(p.elev) && finite(p.dist_km))
      .sort((a, b) => a.dist_km - b.dist_km);
    lastPlanes = usable;
    const shown = usable.slice(0, MAX_PLANES);
    planes.replaceChildren(...shown.map(plane));
    if (!usable.length) note("Gerade kein Flugzeug in der Nähe. Die Liste füllt sich von selbst.");
    else if (usable.length > shown.length) note("Die " + shown.length + " nächsten von " + usable.length
      + " Flugzeugen, alle 5 s neu.");
    else note("Alle 5 s neu.");
    drawCompass();
  }

  async function refresh() {
    if (document.hidden || !inView || Date.now() < holdUntil) return;
    try {
      const response = await fetch("display.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const model = await response.json();
      if (Date.now() < holdUntil) return;    // inzwischen angetippt
      show(Array.isArray(model.nearby) ? model.nearby : []);
    } catch (e) {
      note("Flugzeuge gerade nicht abrufbar, neuer Versuch in 5 s.");
    }
  }

  // Kompass von oben, Norden oben: das Sichtfeld als Fächer, die Flugzeuge als Punkte nach Richtung und Entfernung
  function drawCompass() {
    if (!compass || !compass.getContext) return;
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    const ratio = window.devicePixelRatio || 1;
    const size = compass.clientWidth || 280;
    if (compass.width !== Math.round(size * ratio)) {
      compass.width = compass.height = Math.round(size * ratio);
    }
    const ctx = compass.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const c = size / 2;
    const r = c - 22;
    // Maßstab: das fernste Flugzeug am Rand; ohne Flugzeuge der Umkreis
    const farthest = Math.max(0, ...lastPlanes.map((p) => p.dist_km));
    const range = Math.ceil(Math.max(5, farthest ? farthest * 1.1 : number("RADIUS_KM") || 40));
    const rad = (deg) => ((deg - 90) * Math.PI) / 180;

    // Ringe
    ctx.lineWidth = 1;
    ctx.strokeStyle = color("--line");
    for (const k of [1 / 3, 2 / 3, 1]) {
      ctx.beginPath();
      ctx.arc(c, c, r * k, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Sichtfeld
    let from = number("VIEW_FROM");
    let to = number("VIEW_TO");
    if (Number.isFinite(from) && Number.isFinite(to)) {
      const all = Math.abs(to - from) >= 360 || (from === 0 && to === 360);
      const reach = Math.min(1, (Number.isFinite(number("VIEW_MAX_KM")) ? number("VIEW_MAX_KM") : range) / range);
      ctx.fillStyle = color("--accent-soft");
      ctx.strokeStyle = color("--accent-line");
      ctx.beginPath();
      if (all) {
        ctx.arc(c, c, r * reach, 0, Math.PI * 2);
      } else {
        if (to < from) to += 360;
        ctx.moveTo(c, c);
        ctx.arc(c, c, r * reach, rad(from), rad(to));
        ctx.closePath();
      }
      ctx.fill();
      ctx.stroke();
    }

    // Himmelsrichtungen
    ctx.fillStyle = color("--muted");
    ctx.font = "600 12px " + color("--font");
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    [["N", 0], ["O", 90], ["S", 180], ["W", 270]].forEach(([label, deg]) => {
      ctx.fillText(label, c + Math.cos(rad(deg)) * (r + 12), c + Math.sin(rad(deg)) * (r + 12));
    });

    // Flugzeuge
    for (const p of lastPlanes) {
      const d = Math.min(1, p.dist_km / range) * r;
      const x = c + Math.cos(rad(p.az)) * d;
      const y = c + Math.sin(rad(p.az)) * d;
      ctx.beginPath();
      ctx.arc(x, y, p.in_view === "now" ? 4.5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = p.in_view === "now" ? color("--accent") : color("--text");
      ctx.fill();
    }

    // Das Fenster in der Mitte
    ctx.beginPath();
    ctx.arc(c, c, 4, 0, Math.PI * 2);
    ctx.fillStyle = color("--strong");
    ctx.fill();
    ctx.fillStyle = color("--muted");
    ctx.font = "11px " + color("--font");
    ctx.textAlign = "right";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(decimal.format(range) + " km", size - 4, size - 4);
  }

  planes.addEventListener("pointerdown", () => {
    holdUntil = Date.now() + HOLD_MS;
  });
  planes.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-set]");
    if (!button) return;
    holdUntil = Date.now() + HOLD_MS;
    setField(button.dataset.set, button.dataset.value);
    calibNote.textContent = button.dataset.name + ": " + button.dataset.value + "° übernommen. Speichern nicht "
      + "vergessen.";
  });
  for (const key of ["VIEW_FROM", "VIEW_TO", "VIEW_MAX_KM", "RADIUS_KM"]) {
    const control = document.getElementById("f-" + key);
    if (control) control.addEventListener("input", drawCompass);
  }
  window.addEventListener("resize", drawCompass);

  // Nur abrufen, solange das Eichen zu sehen ist: Sonst verschöbe die wachsende Liste, was darunter steht.
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      inView = entries.some((entry) => entry.isIntersecting);
      if (inView) refresh();
    }).observe(calib);
  }
  document.addEventListener("visibilitychange", refresh);
  drawCompass();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
