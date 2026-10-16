/*
 * Protocolo LoRa entre el gateway y los nodos — version 2 (dos fases).
 *
 * COPIA IDENTICA en firmware/gateway/ y firmware/nodo/: el IDE de Arduino
 * solo compila archivos de la carpeta del sketch. Las pruebas de
 * firmware/pruebas verifican que las dos copias no se separen.
 *
 * Ronda de medicion:
 *   1. El gateway difunde MEDIR a todos los nodos (destino 0xFF).
 *      Cada nodo despierta su SDS011, mide durante la ventana de
 *      estabilizacion, lo vuelve a dormir y guarda el resultado con el
 *      numero de ronda.
 *   2. Terminada la ventana, el gateway pide el dato nodo por nodo con
 *      ENVIAR, y cada uno responde al instante con lo que tiene guardado.
 *
 * Las tramas son estructuras binarias empaquetadas. El primer byte es una
 * marca que distingue las del gateway (0xA5) de las de un nodo (0x5A):
 * con un solo canal compartido, cada nodo escucha tambien las respuestas de
 * los demas, y la marca le permite descartarlas sin interpretarlas. Ademas,
 * ninguna trama del protocolo v1 empieza con esos valores, asi que un
 * equipo con firmware viejo se ignora en vez de malinterpretarse.
 */
#ifndef PROTOCOLO_H
#define PROTOCOLO_H

#include <stdint.h>

const uint8_t MAGIA_GATEWAY = 0xA5;
const uint8_t MAGIA_NODO    = 0x5A;

const uint8_t CMD_MEDIR  = 0x10;
const uint8_t CMD_ENVIAR = 0x11;

// Direcciones validas de nodo: 1..254. El 0 queda sin usar y el 255 es la
// difusion a todos.
const uint8_t DIRECCION_TODOS = 0xFF;

// Ronda 0 = "este nodo todavia no midio nunca". Las rondas reales van de 1 a
// 65535 y al dar la vuelta saltan el 0.
const uint16_t RONDA_NINGUNA = 0;

// Bits de Respuesta.estado
const uint8_t ESTADO_PM_OK = 0x01;   // el SDS011 entrego al menos una lectura
const uint8_t ESTADO_TH_OK = 0x02;   // el DHT21 entrego al menos una lectura

typedef struct __attribute__((packed)) {
  uint8_t  magia;             // MAGIA_GATEWAY
  uint8_t  tipo;              // CMD_MEDIR o CMD_ENVIAR
  uint8_t  destino;           // direccion del nodo, o DIRECCION_TODOS
  uint16_t ronda;             // ronda en curso
  uint8_t  estabilizacion_s;  // MEDIR: segundos de trabajo del SDS011
  uint16_t periodo_s;         // MEDIR: cada cuanto llega la proxima orden
} Comando;                    // 8 bytes

typedef struct __attribute__((packed)) {
  uint8_t  magia;             // MAGIA_NODO
  uint8_t  id_nodo;
  uint16_t ronda;             // ronda a la que pertenece la medicion
  uint8_t  estado;            // ESTADO_PM_OK | ESTADO_TH_OK
  uint8_t  muestras_pm;       // lecturas del SDS011 que entraron al promedio
  float    pm25;
  float    pm10;
  float    temperatura;
  float    humedad;
} Respuesta;                  // 22 bytes

static_assert(sizeof(Comando) == 8, "Comando debe ocupar 8 bytes");
static_assert(sizeof(Respuesta) == 22, "Respuesta debe ocupar 22 bytes");

inline bool comandoValido(const Comando& c) {
  if (c.magia != MAGIA_GATEWAY) return false;
  if (c.tipo == CMD_MEDIR) return c.destino == DIRECCION_TODOS && c.estabilizacion_s > 0;
  if (c.tipo == CMD_ENVIAR) return c.destino >= 1 && c.destino <= 254;
  return false;
}

inline bool respuestaValida(const Respuesta& r) {
  return r.magia == MAGIA_NODO && r.id_nodo >= 1 && r.id_nodo <= 254;
}

inline uint16_t siguienteRonda(uint16_t ronda) {
  uint16_t s = ronda + 1;
  return s == RONDA_NINGUNA ? 1 : s;
}

#endif
