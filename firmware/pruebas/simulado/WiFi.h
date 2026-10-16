#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WL_DISCONNECTED 6
#define WIFI_OFF 0
#define WIFI_STA 1
class WiFiClass {
 public:
  void mode(int) {}
  void begin(const char*, const char*) {}
  void disconnect() {}
  int status() { return conectado ? WL_CONNECTED : WL_DISCONNECTED; }
  bool conectado = true;
};
extern WiFiClass WiFi;
