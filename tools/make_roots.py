"""Vertrauensliste für HTTPS erzeugen: server/flightpanel/certs/roots.pem.

Die Panels prüfen damit das Zertifikat von Cloudflare (die Firmware bettet die Datei ein, siehe
firmware/embed_roots.py), und der Server prüft mit derselben Datei, ob sie noch passt. Ausgewählt werden die
Wurzeln unten aus Debians ca-certificates im festen Basis-Image, jede über ihren Namen (CN) und festgenagelt
über den SHA-256-Fingerabdruck. Fehlt eine Wurzel oder weicht ein Fingerabdruck ab, bricht das Skript ab und
schreibt nichts.

Im Basis-Image aus tools/check_firmware.sh ausführen (Git Bash oder Linux, im Repo-Wurzelverzeichnis):

    IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' tools/check_firmware.sh)
    MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" -w /repo "$IMAGE" \
        python3 -I tools/make_roots.py

Nur bei Bedarf neu erzeugen (neue Wurzel, Cloudflare wechselt die Zertifizierungsstelle); das Ergebnis liegt im
Repo. Danach Firmware neu bauen: Erst ein neues Image bringt die Liste auf die Panels.
"""

import base64
import hashlib
import os
import re
import ssl
import sys

BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "flightpanel", "certs", "roots.pem")

# (CN, SHA-256 des DER-Zertifikats, wofür). Gegengeprüft am 2026-10-14: jeder Fingerabdruck auf crt.sh
# (https://crt.sh/?q=<sha256>, crt.sh-ID in Klammern), Name und Laufzeit dort wie im Bündel
# (ca-certificates 20250419, Debian 13.7 im Basis-Image).
ROOTS = [
    # Let's Encrypt: RSA-Ketten enden bei X1, ECDSA-Ketten bei X2 (9314791, 3335562555)
    ("ISRG Root X1", "96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6", "Let's Encrypt RSA"),
    ("ISRG Root X2", "69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470", "Let's Encrypt ECDSA"),
    # Google Trust Services: WR1/WE1 hängen an R1/R4; Cloudflare liefert dazu R1/R4 mit Gegenzeichnung von
    # GlobalSign Root CA (3263026969, 3263026968, 88). mbedTLS sucht den Aussteller zuerst in dieser Liste und
    # endet bei R1/R4; GlobalSign Root CA deckt eine Kette ab, die direkt bei der Gegenzeichnung endet.
    ("GTS Root R1", "d947432abde7b7fa90fc2e6b59101b1280e0e1c7e4e40fa3c6887fff57a7f4cf", "Google Trust Services RSA"),
    ("GTS Root R4", "349dfa4058c5e263123b398ae795573c4e1313c83fe68f93556cd5e8031b3c7d", "Google Trust Services ECDSA"),
    ("GlobalSign Root CA", "ebd41040e4bb3ec742c9e381d31ef2a41a48b6685c96e7cef3c1df6cd4331c99",
     "Gegenzeichnung der GTS-Wurzeln"),
    # SSL.com: Cloudflare liefert Blatt <- Cloudflare TLS Issuing ECC CA 3 <- SSL.com TLS Transit ECC CA R2 <- SSL.com
    # TLS ECC Root CA 2022 <- (Gegenzeichnung) AAA Certificate Services; so am 2026-10-14 mit openssl s_client an
    # einer Seite hinter Cloudflare gesehen. mbedTLS findet die Root 2022 hier in der Liste und endet dort. AAA
    # fehlt absichtlich: Mozilla hat ihr 2025 das TLS-Vertrauen entzogen, seitdem liefert Cloudflare die von der
    # Root 2022 ausgestellte Fassung von Transit R2 statt der von AAA (Bugzilla 1966632; crt.sh kennt beide
    # Fassungen) (7439766705, 7439767372).
    ("SSL.com TLS ECC Root CA 2022", "c32ffd9f46f936d16c3673990959434b9ad60aafbb9e7cf33654f144cc1ba143",
     "SSL.com ECDSA"),
    ("SSL.com TLS RSA Root CA 2022", "8faf7d2e2cb4709bb8e0b33666bf75a5dd45b5de480f8ea8d4bfe6bebc17f2ed",
     "SSL.com RSA"),
    # Sectigo (bei Cloudflare nur Ersatzzertifikate): Ketten enden über die Gegenzeichnung bei USERTrust
    # (2841410, 1199354)
    ("USERTrust ECC Certification Authority", "4ff460d54b9c86dabfbcfc5712e0400d2bed3fbc4d4fbdaa86e06adcd2a9ad7a",
     "Sectigo ECDSA"),
    ("USERTrust RSA Certification Authority", "e793c9b02fd8aa13e21c31228accb08119643b749c898964b1746d46c3d4cbd2",
     "Sectigo RSA"),
    # Reserve, falls Cloudflare eine weitere Stelle dazunimmt (1730333109, 1730335161, 8656329)
    ("GlobalSign Root R46", "4fa3126d8d3a11d1c4855a4f807cbad6cf919d3a5a88b03bea2c6372d93c40c9", "Reserve"),
    ("GlobalSign Root E46", "cbb9c44d84b8043e1050ea31a69f514955d7bfd2e2c6b49301019ad61d9f5058", "Reserve"),
    ("DigiCert Global Root G2", "cb3ccbb76031e5e0138f8dd39a23f9de47ffc35e43c1144cea27d46a5ab1cb5f", "Reserve"),
]

OID_CN = bytes.fromhex("550403")   # 2.5.4.3 commonName


def _tlv(der: bytes, i: int) -> tuple[int, int, int]:
    """Ein DER-Element ab i: (Tag, Anfang des Inhalts, Ende)."""
    tag, n = der[i], der[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(der[i:i + k], "big")
        i += k
    return tag, i, i + n


def _children(der: bytes, start: int, end: int):
    i = start
    while i < end:
        tag, s, e = _tlv(der, i)
        yield tag, s, e
        i = e


def _cn(der: bytes, start: int, end: int) -> str:
    """commonName eines X.509-Namens (SEQUENCE OF SET OF {OID, Wert})."""
    for _, rs, re_ in _children(der, start, end):
        for _, a_s, a_e in _children(der, rs, re_):
            (_, os_, oe), (_, vs, ve) = list(_children(der, a_s, a_e))[:2]
            if der[os_:oe] == OID_CN:
                return der[vs:ve].decode("utf-8")
    return ""


def _date(raw: bytes) -> str:
    """UTCTime (JJMMTT…) oder GeneralizedTime (JJJJMMTT…) als JJJJ-MM-TT."""
    s = raw.decode("ascii")
    if len(s) == 13:   # UTCTime: Jahre 50-99 sind 19xx
        s = ("19" if int(s[:2]) >= 50 else "20") + s
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def describe(der: bytes) -> tuple[str, str, str, str]:
    """(CN des Inhabers, CN des Ausstellers, gültig ab, gültig bis) aus dem tbsCertificate."""
    _, cs, ce = _tlv(der, 0)
    _, ts, te = next(_children(der, cs, ce))
    fields = list(_children(der, ts, te))
    if fields[0][0] == 0xA0:   # [0] version
        fields = fields[1:]
    _, iss_s, iss_e = fields[2]
    _, val_s, val_e = fields[3]
    _, sub_s, sub_e = fields[4]
    (_, nb_s, nb_e), (_, na_s, na_e) = list(_children(der, val_s, val_e))
    return _cn(der, sub_s, sub_e), _cn(der, iss_s, iss_e), _date(der[nb_s:nb_e]), _date(der[na_s:na_e])


def main() -> None:
    bundle = sys.argv[1] if len(sys.argv) > 1 else BUNDLE
    if not os.path.isfile(bundle):
        sys.exit(f"{bundle} fehlt: im Basis-Image ausführen, siehe Anfang von tools/make_roots.py")
    with open(bundle, encoding="ascii") as f:
        blocks = re.findall(r"-----BEGIN CERTIFICATE-----\s+(.*?)-----END CERTIFICATE-----", f.read(), re.S)
    by_cn: dict[str, list[bytes]] = {}
    for b64 in blocks:
        der = base64.b64decode(b64)
        by_cn.setdefault(describe(der)[0], []).append(der)

    errors, out = [], []
    for cn, pin, purpose in ROOTS:
        found = by_cn.get(cn, [])
        der = next((d for d in found if hashlib.sha256(d).hexdigest() == pin), None)
        if der is None:
            seen = ", ".join(hashlib.sha256(d).hexdigest() for d in found) or "keins"
            errors.append(f"{cn}: {'Fingerabdruck weicht ab, im Bündel ' + seen if found else 'fehlt im Bündel'}")
            continue
        subject, issuer, not_before, not_after = describe(der)
        if issuer != subject:
            errors.append(f"{cn}: nicht selbst signiert (Aussteller {issuer})")
            continue
        out.append(f"# {cn} ({purpose}), gültig {not_before} bis {not_after}\n# SHA-256 {pin}\n"
                   + ssl.DER_cert_to_PEM_cert(der))
    if errors:
        sys.exit("Vertrauensliste NICHT geschrieben:\n  " + "\n  ".join(errors))

    header = ("# Vertrauensliste für HTTPS zu Cloudflare: Firmware (eingebettet) und Server (Selbsttest).\n"
              "# Erzeugt von tools/make_roots.py aus Debians ca-certificates, nicht von Hand ändern.\n"
              "# Jede Wurzel ist über ihren SHA-256-Fingerabdruck festgelegt; Begründungen im Skript.\n\n")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n".join(out))
    print(f"{len(out)} Wurzeln nach {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
