#pragma once
#include "Client.h"
// Cliente TLS simulado: lo que se escribe queda en `tx`, lo que se carga en
// `rx` es lo que "responde" el broker.
class WiFiClientSecure : public Client {
 public:
  void setInsecure() {}
  int connect(IPAddress, uint16_t) override { conectado = true; return 1; }
  int connect(const char*, uint16_t) override { conectado = true; return 1; }
  size_t write(uint8_t b) override { tx.push_back(b); return 1; }
  size_t write(const uint8_t* b, size_t n) override { tx.insert(tx.end(), b, b + n); return n; }
  int available() override { return (int)rx.size(); }
  int read() override { if (rx.empty()) return -1; int b = rx.front(); rx.pop_front(); return b; }
  int read(uint8_t* b, size_t n) override { size_t i = 0; for (; i < n && !rx.empty(); i++) b[i] = (uint8_t)read(); return (int)i; }
  int peek() override { return rx.empty() ? -1 : rx.front(); }
  void flush() override {}
  void stop() override { conectado = false; }
  uint8_t connected() override { return conectado; }
  operator bool() override { return conectado; }
  bool conectado = false;
  std::vector<uint8_t> tx;
  std::deque<uint8_t> rx;
};
