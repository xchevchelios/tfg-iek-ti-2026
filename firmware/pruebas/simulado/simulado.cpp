// Definiciones globales del entorno simulado y cuerpos vacios de las
// librerias de sensores (para las pruebas solo importa que compilen).
#include "Arduino.h"
#include "WiFi.h"
uint32_t g_millis = 0;
bool g_eco_serial = false;
HardwareSerial Serial(true), Serial1, Serial2;
WiFiClass WiFi;
