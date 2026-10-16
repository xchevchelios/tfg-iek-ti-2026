/*
 * Ventana de medicion del nodo: logica pura, sin hardware.
 *
 * Al llegar la orden MEDIR, el SDS011 se despierta y trabaja durante
 * `estabilizacion_s` segundos. Las primeras lecturas no sirven: el
 * ventilador recien arranca y la camara todavia tiene el aire de antes.
 * Por eso solo se promedia el tramo final de la ventana (la mitad final,
 * como maximo 10 s), y al cerrar se devuelve el promedio junto con cuantas
 * lecturas entraron.
 *
 * Al no depender de Arduino, se prueba en la computadora con
 * firmware/pruebas. Todos los tiempos son de millis() y se comparan por
 * diferencia, asi que el desborde de millis() a los 49 dias no la afecta.
 */
#ifndef MEDICION_H
#define MEDICION_H

#include <stdint.h>
#include "protocolo.h"

struct ResultadoMedicion {
  uint16_t ronda;
  uint8_t  estado;
  uint8_t  muestras_pm;
  float    pm25;
  float    pm10;
  float    temperatura;
  float    humedad;
};

class VentanaMedicion {
 public:
  static const uint32_t PROMEDIO_MAX_MS = 10000;

  VentanaMedicion() : activa_(false) {}

  bool activa() const { return activa_; }
  uint16_t ronda() const { return ronda_; }

  // Devuelve false si ya habia una ventana en curso: la orden MEDIR se
  // repite por si un nodo no escucho la primera, y la repeticion no tiene
  // que reiniciar una medicion que ya empezo.
  bool iniciar(uint16_t ronda, uint8_t estabilizacion_s, uint32_t ahora) {
    if (activa_) return false;
    activa_ = true;
    ronda_ = ronda;
    inicio_ = ahora;
    duracion_ = (uint32_t)estabilizacion_s * 1000UL;
    uint32_t tramo = duracion_ / 2;
    if (tramo > PROMEDIO_MAX_MS) tramo = PROMEDIO_MAX_MS;
    desde_ = duracion_ - tramo;
    n_pm_ = n_th_ = 0;
    suma_pm25_ = suma_pm10_ = suma_t_ = suma_h_ = 0;
    return true;
  }

  uint32_t transcurrido(uint32_t ahora) const { return ahora - inicio_; }

  bool enTramoDePromedio(uint32_t ahora) const {
    return activa_ && transcurrido(ahora) >= desde_;
  }

  bool vencida(uint32_t ahora) const {
    return activa_ && transcurrido(ahora) >= duracion_;
  }

  void agregarPM(float pm25, float pm10, uint32_t ahora) {
    if (!enTramoDePromedio(ahora) || pm25 < 0 || pm10 < 0) return;
    if (n_pm_ == 255) return;
    suma_pm25_ += pm25;
    suma_pm10_ += pm10;
    n_pm_++;
  }

  void agregarTH(float temperatura, float humedad, uint32_t ahora) {
    if (!enTramoDePromedio(ahora)) return;
    if (temperatura != temperatura || humedad != humedad) return;  // NaN
    if (n_th_ == 255) return;
    suma_t_ += temperatura;
    suma_h_ += humedad;
    n_th_++;
  }

  ResultadoMedicion cerrar() {
    ResultadoMedicion r;
    r.ronda = ronda_;
    r.estado = 0;
    r.muestras_pm = n_pm_;
    r.pm25 = r.pm10 = r.temperatura = r.humedad = 0;
    if (n_pm_ > 0) {
      r.estado |= ESTADO_PM_OK;
      r.pm25 = suma_pm25_ / n_pm_;
      r.pm10 = suma_pm10_ / n_pm_;
    }
    if (n_th_ > 0) {
      r.estado |= ESTADO_TH_OK;
      r.temperatura = suma_t_ / n_th_;
      r.humedad = suma_h_ / n_th_;
    }
    activa_ = false;
    return r;
  }

 private:
  bool     activa_;
  uint16_t ronda_;
  uint32_t inicio_, duracion_, desde_;
  uint8_t  n_pm_, n_th_;
  float    suma_pm25_, suma_pm10_, suma_t_, suma_h_;
};

#endif
