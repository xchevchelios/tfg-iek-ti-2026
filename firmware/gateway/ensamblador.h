/*
 * Arma tramas de largo fijo a partir de los bytes que entrega el modulo
 * LoRa por UART.
 *
 * COPIA IDENTICA en firmware/gateway/ y firmware/nodo/.
 *
 * El E22 entrega cada paquete recibido como una rafaga continua de bytes
 * (a 9600 baudios, uno por milisegundo). Por eso un silencio de mas de
 * SILENCIO_MS marca el limite entre tramas: si quedo una trama a medias,
 * se descarta. Es el mismo criterio que usa Modbus RTU. Ademas, una trama
 * tiene que empezar con la marca esperada; cualquier otro byte inicial se
 * ignora hasta encontrarla.
 */
#ifndef ENSAMBLADOR_H
#define ENSAMBLADOR_H

#include <stdint.h>
#include <string.h>

template <unsigned N>
class Ensamblador {
 public:
  static const uint32_t SILENCIO_MS = 20;

  explicit Ensamblador(uint8_t magia) : magia_(magia), pos_(0), ultimo_(0) {}

  // Agrega un byte. Devuelve true cuando se completo una trama, que queda
  // copiada en `salida` (N bytes).
  bool empujar(uint8_t b, uint32_t ahora_ms, void* salida) {
    if (pos_ > 0 && (uint32_t)(ahora_ms - ultimo_) > SILENCIO_MS) pos_ = 0;
    ultimo_ = ahora_ms;

    if (pos_ == 0 && b != magia_) return false;
    buffer_[pos_++] = b;
    if (pos_ < N) return false;

    memcpy(salida, buffer_, N);
    pos_ = 0;
    return true;
  }

  void reiniciar() { pos_ = 0; }

 private:
  uint8_t  magia_;
  uint8_t  buffer_[N];
  unsigned pos_;
  uint32_t ultimo_;
};

#endif
