#pragma once
// NVS simulada, con la misma interfaz que la de arduino-esp32. Guarda en
// memoria y sobrevive a "reinicios" del sketch dentro de la prueba.
#include "Arduino.h"
#include <map>
class Preferences {
 public:
  bool begin(const char* nombre, bool soloLectura = false, const char* = NULL) {
    espacio_ = nombre; soloLectura_ = soloLectura; abierta_ = true; return true;
  }
  void end() { abierta_ = false; }
  size_t putUChar(const char* k, uint8_t v) { return putBytes(k, &v, 1); }
  size_t putBytes(const char* k, const void* v, size_t n) {
    if (!abierta_ || soloLectura_ || !v || !n) return 0;   // como la real: no guarda bloques vacios
    datos()[clave(k)] = std::vector<uint8_t>((const uint8_t*)v, (const uint8_t*)v + n);
    escrituras++;
    return n;
  }
  bool isKey(const char* k) { return datos().count(clave(k)) > 0; }
  uint8_t getUChar(const char* k, uint8_t def = 0) { uint8_t v = def; getBytes(k, &v, 1); return v; }
  size_t getBytesLength(const char* k) { return isKey(k) ? datos()[clave(k)].size() : 0; }
  size_t getBytes(const char* k, void* buf, size_t n) {
    if (!isKey(k)) return 0;
    auto& v = datos()[clave(k)];
    if (v.size() > n) return 0;
    memcpy(buf, v.data(), v.size());
    return v.size();
  }
  static std::map<std::string, std::vector<uint8_t>>& datos() {
    static std::map<std::string, std::vector<uint8_t>> d; return d;
  }
  static inline unsigned escrituras = 0;
 private:
  std::string clave(const char* k) { return espacio_ + "/" + k; }
  std::string espacio_;
  bool soloLectura_ = false, abierta_ = false;
};
