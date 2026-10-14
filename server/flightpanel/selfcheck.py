"""Täglicher TLS-Selbsttest des Zugangs von außen. Geladen nur mit den Schaltern dafür (main.App._start_public).

Die Panels der Freunde vertrauen nicht dem ganzen Web, sondern nur den Wurzelzertifikaten in certs/roots.pem (die
Firmware bettet dieselbe Datei ein). Stellt Cloudflare seine Zertifikatskette auf eine Wurzel außerhalb dieser Liste
um, scheitern alle Panels an der Zertifikatsprüfung und bleiben dunkel, bis eine Firmware mit neuer Liste kommt
(Rettungsmodus, docs/freunde.md, Abschnitt 9). Damit der Betreiber das vor den Freunden merkt, holt der Server einmal
am Tag https://<PUBLIC_HOST>/panel/firmware.txt so, wie ein Panel es täte: nur mit roots.pem als Vertrauensliste, mit
Prüfung des Namens, nur TLS 1.2 (mehr kann der ESP32 nicht). Jeder HTTP-Status heißt: TLS passt.

  - Scheitert die Zertifikatsprüfung: /ok meldet "Root-Liste veraltet" (main.App.problems), /issues trägt tls_roots
    mit dem Weg ein, bis ein Test wieder gelingt.
  - Jeder andere Fehler (Netz, DNS, Zeitüberschreitung, ein Handshake ohne TLS 1.2) sagt nichts über die Liste: nur
    source_error in /issues, /ok bleibt grün.
  - Fehlt roots.pem oder ist sie unbrauchbar, steht das einmal in /issues, und der Test ruht."""

import logging
import os
import ssl

import aiohttp

from .issues import Issues, error_text

log = logging.getLogger("selfcheck")

ROOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "roots.pem")
TIMEOUT_S = 30
FIX = ("Die Panels vertrauen nur den Wurzelzertifikaten in server/flightpanel/certs/roots.pem, und das Zertifikat von "
       "PUBLIC_HOST passt nicht mehr dazu. roots.pem aktualisieren und eine Firmware damit veröffentlichen "
       "(tools/fw_release.sh, docs/freunde.md, Abschnitt 9); die Panels holen sie über den Rettungsmodus.")
MISSING = ("Ohne lesbare roots.pem prüft der Server nicht, ob die Panels dem Zertifikat von PUBLIC_HOST noch trauen. "
           "Die Datei gehört nach server/flightpanel/certs/roots.pem, dieselbe, die die Firmware einbettet.")


def _why(e: Exception) -> str:
    """Ein Verbindungsfehler ohne die Hülle von aiohttp: Was los war, sagt der Fehler darunter (os_error), etwa "tlsv1
    alert protocol version", wenn die Gegenseite kein TLS 1.2 mehr spricht; aiohttp selbst schreibt dann nur [None]."""
    inner = getattr(e, "os_error", None)
    return error_text(inner if isinstance(inner, BaseException) else e)


def tls12_context(roots: str) -> ssl.SSLContext:
    """Wie ein Panel: nur die Wurzeln aus roots (SSLContext lädt von sich aus keine des Systems), Zertifikat und Name
    geprüft, nur TLS 1.2."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)          # verify_mode CERT_REQUIRED, check_hostname an
    ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_verify_locations(cafile=roots)
    return ctx


class TlsCheck:
    """url: https://<PUBLIC_HOST>/panel/firmware.txt, roots: die Vertrauensliste der Firmware. main.App.tls_loop ruft
    run() etwa eine Minute nach dem Start (FIRST_S), dann einmal am Tag (EVERY_S)."""

    FIRST_S = 60
    EVERY_S = 24 * 3600

    def __init__(self, session: aiohttp.ClientSession, issues: Issues, url: str, roots: str = ROOTS):
        self.session, self.issues, self.url, self.roots = session, issues, url, roots
        self.stale: str | None = None       # warum das Zertifikat nicht zur Liste passt; gesetzt heißt /ok rot
        self.last_ok: float | None = None   # wann ein Test zuletzt gelang
        self._unusable_noted = False

    async def run(self, now: float) -> None:
        try:
            ctx = tls12_context(self.roots)
        except OSError as e:                                # fehlt, oder kein Zertifikat darin (ssl.SSLError)
            if not self._unusable_noted:
                self._unusable_noted = True
                log.warning("TLS-Selbsttest ruht: %s fehlt oder ist unbrauchbar (%s)", self.roots, e)
                self.issues.note("tls_roots", "roots.pem fehlt", error=error_text(e), fix=MISSING)
            return
        try:
            async with self.session.get(self.url, ssl=ctx, allow_redirects=False,
                                        timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as r:
                status = r.status
        except aiohttp.ClientConnectorCertificateError as e:
            self.stale = getattr(e.certificate_error, "verify_message", None) or error_text(e.certificate_error)
            log.warning("TLS-Selbsttest: Root-Liste veraltet, die Panels trauen dem Zertifikat nicht mehr (%s)",
                        self.stale)
            self.issues.note("tls_roots", "roots.pem", error=self.stale, fix=FIX)
            return
        except Exception as e:                              # Netz, DNS, Zeit, Handshake: nichts über die Liste
            why = _why(e)
            log.warning("TLS-Selbsttest nicht möglich: %s", why)
            self.issues.source_error("tls_check", why)
            return
        if self.stale:
            log.info("TLS-Selbsttest: Das Zertifikat passt wieder zu roots.pem")
        self.stale, self.last_ok = None, now
        log.info("TLS-Selbsttest: Zertifikat passt zu roots.pem (HTTP %d)", status)
