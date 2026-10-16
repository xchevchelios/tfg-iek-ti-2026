// Demuestra, con la libreria PubSubClient real, el error del firmware v1:
// publish(topic, json, n) con json de tipo char* resuelve a la sobrecarga
// publish(topic, payload, retained), asi que n (distinto de 0) se toma como
// retained = true y cada medicion queda retenida en el broker.
//
// Se compara el primer byte del paquete PUBLISH que la libreria escribe en
// el socket: el bit 0 es la bandera RETAIN del protocolo MQTT.
#include "Arduino.h"
#include "WiFiClientSecure.h"
#include "PubSubClient.h"

int main() {
  WiFiClientSecure socket;
  for (uint8_t b : {0x20, 0x02, 0x00, 0x00}) socket.rx.push_back(b);   // CONNACK aceptado
  PubSubClient mqtt(socket);
  mqtt.setServer("broker", 1883);
  if (!mqtt.connect("prueba")) { printf("no conecto\n"); return 2; }

  char json[] = "{\"pm25\":12.3}";
  size_t n = strlen(json);

  socket.tx.clear();
  mqtt.publish("estaciones/estacion_01/data", json, n);                       // v1
  uint8_t v1 = socket.tx.at(0);

  socket.tx.clear();
  mqtt.publish("estaciones/estacion_01/data", (const uint8_t*)json, n, false); // v2
  uint8_t v2 = socket.tx.at(0);

  printf("v1  publish(topic, json, n)                    -> cabecera 0x%02X, RETAIN=%d\n", v1, v1 & 1);
  printf("v2  publish(topic, (uint8_t*)json, n, false)   -> cabecera 0x%02X, RETAIN=%d\n", v2, v2 & 1);
  return ((v1 & 1) == 1 && (v2 & 1) == 0) ? 0 : 1;
}
