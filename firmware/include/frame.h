#pragma once
// Bild holen: GET /panel/frame mit If-None-Match (letztes ETag) und X-Panel (Diagnose für den Server).
// Antwort 200: genau FRAME_LEN Byte mit gültigem Kopf; 304: Bild unverändert, das alte bleibt stehen.
#include <stdint.h>

namespace frame {
enum class Result { Fresh, Same, Failed };   // neues Bild in buf, unverändert (304), Fehler
Result fetch(uint8_t* buf);                   // buf: FRAME_LEN Byte; bei Fehler kann er halb beschrieben sein
void forgetEtag();                            // wenn etwas anderes gezeichnet wurde: nächstes Mal ganzes Bild
}  // namespace frame
