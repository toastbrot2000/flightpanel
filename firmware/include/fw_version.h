#pragma once
// Firmware-Version: Build-Zeit als JJMMTTHHMM (UTC), genau 10 Ziffern. Verglichen wird sie als Text, nie als
// Zahl (2610131200 passt nicht in 32 Bit). Ein Build-Skript setzt sie per -DFW_VERSION=\"...\"; ohne es gilt
// "0000000000". Der Server bekommt sie im Header X-Panel (frame.cpp).
#ifndef FW_VERSION
#define FW_VERSION "0000000000"
#endif
static_assert(sizeof(FW_VERSION) == 11, "FW_VERSION muss genau 10 Zeichen lang sein");
