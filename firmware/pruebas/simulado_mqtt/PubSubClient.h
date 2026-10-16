#pragma once
// PubSubClient simulado que registra lo publicado. Las cuatro sobrecargas
// de publish() son las mismas que las de la libreria real, para que la
// resolucion de sobrecargas sea identica.
#include "Arduino.h"
#include "Client.h"
struct Publicacion { std::string topic, carga; bool retenido; };
class PubSubClient {
 public:
  explicit PubSubClient(Client&) {}
  PubSubClient& setServer(const char*, uint16_t) { return *this; }
  PubSubClient& setCallback(std::function<void(char*, uint8_t*, unsigned int)> cb) { callback = cb; return *this; }
  boolean setBufferSize(uint16_t) { return true; }
  boolean connect(const char*, const char*, const char*, const char*, uint8_t, boolean, const char*) { conectado = true; return true; }
  boolean connected() { return conectado; }
  boolean loop() { return true; }
  int state() { return 0; }
  boolean publish(const char* t, const char* p) { return registrar(t, p, strlen(p), false); }
  boolean publish(const char* t, const char* p, boolean r) { return registrar(t, p, strlen(p), r); }
  boolean publish(const char* t, const uint8_t* p, unsigned int n) { return registrar(t, (const char*)p, n, false); }
  boolean publish(const char* t, const uint8_t* p, unsigned int n, boolean r) { return registrar(t, (const char*)p, n, r); }
  boolean subscribe(const char* t, uint8_t) { suscripciones.push_back(t); return true; }
  std::function<void(char*, uint8_t*, unsigned int)> callback;
  std::vector<Publicacion> publicadas;
  std::vector<std::string> suscripciones;
  bool conectado = false;
 private:
  boolean registrar(const char* t, const char* p, size_t n, bool r) {
    publicadas.push_back({t, std::string(p, n), r});
    return true;
  }
};
