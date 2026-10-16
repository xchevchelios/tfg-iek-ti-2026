// Arduino + FreeRTOS simulados para compilar y probar el firmware en la
// computadora. Solo lo que usan los sketches y las librerias reales que se
// compilan contra esto (PubSubClient, DHT, SDS011). El tiempo es simulado:
// millis() devuelve g_millis, y delay()/vTaskDelay() lo avanzan.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <deque>
#include <functional>
#include <string>
#include <vector>

#ifndef ARDUINO
#define ARDUINO 10819
#endif
#ifndef ESP32
#define ESP32 1
#endif

typedef bool boolean;
typedef uint8_t byte;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define SERIAL_8N1 0x800001c

extern uint32_t g_millis;
extern bool g_eco_serial;
inline uint32_t millis() { return g_millis; }
inline uint32_t micros() { return g_millis * 1000; }
inline void delay(uint32_t ms) { g_millis += ms; }
inline void delayMicroseconds(uint32_t) {}
inline void yield() {}
inline void pinMode(uint8_t, uint8_t) {}
inline void digitalWrite(uint8_t, uint8_t) {}
inline int digitalRead(uint8_t) { return 0; }
inline long random(long a, long) { return a; }
inline uint32_t esp_random() { return 0xBEEF; }
inline void btStop() {}
inline void noInterrupts() {}
#define PROGMEM
#define pgm_read_byte_near(a) (*(const uint8_t*)(a))
#define pgm_read_byte(a) (*(const uint8_t*)(a))
#define strlen_P strlen
inline void interrupts() {}
inline void configTime(long, int, const char*, const char* = nullptr, const char* = nullptr) {}

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* b, size_t n) { for (size_t i = 0; i < n; i++) write(b[i]); return n; }
  size_t write(const char* s) { return write((const uint8_t*)s, strlen(s)); }
  size_t print(const char* s) { return write(s); }
  size_t print(long v) { char b[24]; snprintf(b, sizeof b, "%ld", v); return write(b); }
  size_t print(int v) { return print((long)v); }
  size_t print(unsigned v) { return print((long)v); }
  size_t print(unsigned long v) { return print((long)v); }
  size_t print(double v) { char b[32]; snprintf(b, sizeof b, "%.2f", v); return write(b); }
  size_t println() { return write("\n"); }
  template <typename T> size_t println(T v) { size_t n = print(v); return n + println(); }
  size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
    char b[512];
    va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    return write(b);
  }
};

class Stream : public Print {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() { return -1; }
  virtual void flush() {}
  size_t readBytes(char* buf, size_t n) {
    size_t i = 0;
    for (; i < n && available(); i++) buf[i] = (char)read();
    return i;
  }
};

class HardwareSerial : public Stream {
 public:
  explicit HardwareSerial(bool eco = false) : eco_(eco) {}
  void begin(unsigned long, uint32_t = SERIAL_8N1, int8_t = -1, int8_t = -1) {}
  int available() override { return (int)rx.size(); }
  int read() override { if (rx.empty()) return -1; int b = rx.front(); rx.pop_front(); return b; }
  size_t write(uint8_t b) override {
    if (eco_) { if (g_eco_serial) fputc(b, stdout); salida.push_back((char)b); }
    else tx.push_back(b);
    return 1;
  }
  using Print::write;
  std::deque<uint8_t> rx;     // lo que "llega" por este puerto
  std::vector<uint8_t> tx;    // lo que el sketch escribio
  std::string salida;         // texto del monitor serie (solo Serial)
 private:
  bool eco_;
};

extern HardwareSerial Serial, Serial1, Serial2;

// ---------------------------- FreeRTOS ------------------------------
typedef void* QueueHandle_t;
typedef void* TaskHandle_t;
typedef int BaseType_t;
#define pdPASS 1
#define pdTRUE 1
#define pdFALSE 0
#define portTICK_PERIOD_MS 1
#define portMAX_DELAY 0xffffffffUL

struct ColaSimulada {
  size_t tam, cap;
  std::deque<std::vector<uint8_t>> items;
};
inline QueueHandle_t xQueueCreate(unsigned largo, unsigned tam) { return new ColaSimulada{tam, largo, {}}; }
inline BaseType_t xQueueSend(QueueHandle_t h, const void* p, uint32_t) {
  ColaSimulada* c = (ColaSimulada*)h;
  if (c->items.size() >= c->cap) return pdFALSE;
  c->items.emplace_back((const uint8_t*)p, (const uint8_t*)p + c->tam);
  return pdPASS;
}
inline BaseType_t xQueueOverwrite(QueueHandle_t h, const void* p) {
  ColaSimulada* c = (ColaSimulada*)h;
  c->items.clear();
  c->items.emplace_back((const uint8_t*)p, (const uint8_t*)p + c->tam);
  return pdPASS;
}
inline BaseType_t xQueueReceive(QueueHandle_t h, void* p, uint32_t) {
  ColaSimulada* c = (ColaSimulada*)h;
  if (c->items.empty()) return pdFALSE;
  memcpy(p, c->items.front().data(), c->tam);
  c->items.pop_front();
  return pdPASS;
}
inline void vTaskDelay(uint32_t ticks) { g_millis += ticks; }
inline BaseType_t xTaskCreatePinnedToCore(void (*)(void*), const char*, uint32_t, void*, unsigned,
                                          TaskHandle_t*, int) { return pdPASS; }
typedef struct { int dummy; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED {0}
#define portENTER_CRITICAL(m) (void)(m)
#define portEXIT_CRITICAL(m) (void)(m)
