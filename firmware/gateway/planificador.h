/*
 * Planificador de rondas del gateway: logica pura, sin hardware.
 *
 * Decide en cada instante que hay que transmitir, y lleva la cuenta de que
 * paso con cada nodo en la ronda. El .ino solo traduce sus decisiones a
 * bytes por la UART. Al no depender de Arduino, se prueba en la computadora
 * con firmware/pruebas, simulando el tiempo.
 *
 * Una ronda:
 *   ESPERA    hasta la hora de la proxima ronda
 *   MIDIENDO  difunde MEDIR (y lo repite una vez), espera la ventana
 *   PIDIENDO  pide el dato a un nodo y espera respuesta o timeout
 *   PAUSA     breve silencio antes de pedirle al siguiente
 *   CIERRE    informa el resultado y vuelve a ESPERA
 *
 * Las rondas arrancan a intervalos fijos de `periodo_s`, contados desde el
 * inicio de la anterior y no desde su fin: asi el periodo no se estira con
 * la cantidad de nodos. Si una ronda se pasa del periodo, la siguiente
 * arranca apenas termina, y la ronda queda marcada como excedida.
 */
#ifndef PLANIFICADOR_H
#define PLANIFICADOR_H

#include <stdint.h>
#include <string.h>
#include "protocolo.h"

const uint8_t MAX_NODOS = 32;   // el mismo tope que api/configuracion_red.py

struct ParametrosRed {
  uint16_t periodo_s;
  uint8_t  estabilizacion_s;
  uint16_t timeout_ms;
  bool     publicar;
};

const ParametrosRed PARAMETROS_POR_DEFECTO = {300, 30, 3000, true};

// Los mismos rangos que valida gestor.py. El firmware los vuelve a validar
// porque es la ultima barrera: ningun mensaje puede dejarlo saturando el canal.
const uint16_t PERIODO_MIN_S  = 60,  PERIODO_MAX_S  = 3600;
const uint8_t  ESTAB_MIN_S    = 15,  ESTAB_MAX_S    = 60;
const uint16_t TIMEOUT_MIN_MS = 500, TIMEOUT_MAX_MS = 5000;

// Entre el fin teorico de la ventana y el primer pedido: cubre la difusion
// de MEDIR, su repeticion y la ultima lectura del DHT21.
const uint32_t MARGEN_MEDICION_MS   = 2000;
// MEDIR no tiene confirmacion, asi que se difunde dos veces.
const uint32_t REPETICION_MEDIR_MS  = 400;
const uint32_t PAUSA_ENTRE_NODOS_MS = 100;

inline bool parametrosIguales(const ParametrosRed& a, const ParametrosRed& b) {
  return a.periodo_s == b.periodo_s && a.estabilizacion_s == b.estabilizacion_s &&
         a.timeout_ms == b.timeout_ms && a.publicar == b.publicar;
}

// nullptr si son validos; si no, el motivo.
inline const char* validarParametros(const ParametrosRed& p) {
  if (p.periodo_s < PERIODO_MIN_S || p.periodo_s > PERIODO_MAX_S)
    return "periodo_s fuera de rango (60 a 3600)";
  if (p.estabilizacion_s < ESTAB_MIN_S || p.estabilizacion_s > ESTAB_MAX_S)
    return "estabilizacion_s fuera de rango (15 a 60)";
  if (p.timeout_ms < TIMEOUT_MIN_MS || p.timeout_ms > TIMEOUT_MAX_MS)
    return "timeout_ms fuera de rango (500 a 5000)";
  return nullptr;
}

inline const char* validarNodos(const uint8_t* nodos, unsigned n) {
  if (n > MAX_NODOS) return "mas de 32 nodos";
  for (unsigned i = 0; i < n; i++) {
    if (nodos[i] < 1 || nodos[i] > 254) return "direccion fuera de rango (1 a 254)";
    for (unsigned j = 0; j < i; j++)
      if (nodos[i] == nodos[j]) return "direccion repetida";
  }
  return nullptr;
}

// Lo que tarda una ronda en el peor caso: todos los nodos agotan el timeout.
inline uint32_t duracionMaximaRonda_ms(const ParametrosRed& p, unsigned n) {
  return (uint32_t)p.estabilizacion_s * 1000UL + MARGEN_MEDICION_MS +
         (uint32_t)n * ((uint32_t)p.timeout_ms + PAUSA_ENTRE_NODOS_MS);
}

enum Accion    : uint8_t { NADA, DIFUNDIR_MEDIR, PEDIR_DATO, NODO_SIN_RESPUESTA, RONDA_TERMINADA };
enum Desenlace : uint8_t { PENDIENTE, FRESCA, VIEJA, SIN_RESPUESTA };
enum Recepcion : uint8_t { IGNORADA, ACEPTADA_FRESCA, ACEPTADA_VIEJA };

class Planificador {
 public:
  Planificador()
      : estado_(ESPERA), ronda_(RONDA_NINGUNA), n_(0), idx_(0), pendiente_(false),
        n_pend_(0), perdido_(0), proxInicio_(0), duracion_(0), excedida_(false) {
    params_ = params_pend_ = PARAMETROS_POR_DEFECTO;
  }

  // Se guarda y se aplica al empezar la proxima ronda, nunca a mitad de una.
  void configurar(const ParametrosRed& p, const uint8_t* nodos, uint8_t n) {
    if (n > MAX_NODOS) n = MAX_NODOS;
    params_pend_ = p;
    memcpy(nodos_pend_, nodos, n);
    n_pend_ = n;
    pendiente_ = true;
  }

  // `rondaInicial` conviene que sea al azar: si el gateway se reinicia, un
  // nodo no puede confundir su medicion vieja con la de la ronda nueva por
  // coincidir el numero.
  void arrancar(uint32_t ahora, uint16_t rondaInicial, uint32_t demora_ms) {
    ronda_ = rondaInicial;
    proxInicio_ = ahora + demora_ms;
    estado_ = ESPERA;
  }

  Accion tick(uint32_t ahora) {
    switch (estado_) {
      case ESPERA: {
        if ((int32_t)(ahora - proxInicio_) < 0) return NADA;
        if (pendiente_) {
          params_ = params_pend_;
          memcpy(nodos_, nodos_pend_, n_pend_);
          n_ = n_pend_;
          pendiente_ = false;
        }
        const uint32_t periodo = (uint32_t)params_.periodo_s * 1000UL;
        inicioRonda_ = ahora;
        proxInicio_ += periodo;
        // Si aun asi quedo en el pasado (ronda muy excedida o primera
        // ronda), se reancla en el presente.
        if ((int32_t)(ahora - proxInicio_) >= 0) proxInicio_ = ahora + periodo;

        ronda_ = siguienteRonda(ronda_);
        for (uint8_t i = 0; i < n_; i++) { desenlace_[i] = PENDIENTE; sensores_[i] = 0; }
        if (n_ == 0) { estado_ = CIERRE; return NADA; }
        repetido_ = false;
        tMedir_ = ahora;
        estado_ = MIDIENDO;
        return DIFUNDIR_MEDIR;
      }

      case MIDIENDO:
        if (!repetido_ && ahora - tMedir_ >= REPETICION_MEDIR_MS) {
          repetido_ = true;
          return DIFUNDIR_MEDIR;
        }
        if (ahora - tMedir_ < (uint32_t)params_.estabilizacion_s * 1000UL + MARGEN_MEDICION_MS)
          return NADA;
        idx_ = 0;
        tPedido_ = ahora;
        estado_ = PIDIENDO;
        return PEDIR_DATO;

      case PIDIENDO:
        if (ahora - tPedido_ < params_.timeout_ms) return NADA;
        perdido_ = nodos_[idx_];
        desenlace_[idx_] = SIN_RESPUESTA;
        avanzar(ahora);
        return NODO_SIN_RESPUESTA;

      case PAUSA:
        if (ahora - tPausa_ < PAUSA_ENTRE_NODOS_MS) return NADA;
        tPedido_ = ahora;
        estado_ = PIDIENDO;
        return PEDIR_DATO;

      case CIERRE:
        duracion_ = ahora - inicioRonda_;
        excedida_ = (int32_t)(ahora - proxInicio_) > 0;
        estado_ = ESPERA;
        return RONDA_TERMINADA;
    }
    return NADA;
  }

  // Una respuesta solo cuenta si es del nodo al que se le esta pidiendo.
  // Si trae otra ronda, el nodo no escucho la orden de medir y lo que tiene
  // es viejo: se registra, pero no se publica.
  Recepcion recibir(const Respuesta& r, uint32_t ahora) {
    if (estado_ != PIDIENDO || r.id_nodo != nodos_[idx_]) return IGNORADA;
    Recepcion resultado;
    if (r.ronda == ronda_) {
      desenlace_[idx_] = FRESCA;
      sensores_[idx_] = r.estado;
      resultado = ACEPTADA_FRESCA;
    } else {
      desenlace_[idx_] = VIEJA;
      resultado = ACEPTADA_VIEJA;
    }
    avanzar(ahora);
    return resultado;
  }

  uint16_t ronda() const { return ronda_; }
  uint8_t nodoActual() const { return nodos_[idx_]; }
  uint8_t nodoPerdido() const { return perdido_; }   // tras NODO_SIN_RESPUESTA
  const ParametrosRed& parametros() const { return params_; }
  uint8_t cantidadNodos() const { return n_; }
  uint8_t nodo(uint8_t i) const { return nodos_[i]; }
  Desenlace desenlace(uint8_t i) const { return desenlace_[i]; }
  uint8_t sensores(uint8_t i) const { return sensores_[i]; }
  uint32_t duracionUltimaRonda() const { return duracion_; }
  bool ultimaExcedida() const { return excedida_; }

 private:
  enum Estado : uint8_t { ESPERA, MIDIENDO, PIDIENDO, PAUSA, CIERRE };

  void avanzar(uint32_t ahora) {
    idx_++;
    if (idx_ < n_) {
      tPausa_ = ahora;
      estado_ = PAUSA;
    } else {
      estado_ = CIERRE;
    }
  }

  Estado        estado_;
  ParametrosRed params_, params_pend_;
  uint16_t      ronda_;
  uint8_t       nodos_[MAX_NODOS], nodos_pend_[MAX_NODOS];
  uint8_t       n_, idx_;
  bool          pendiente_;
  uint8_t       n_pend_;
  uint8_t       perdido_;
  Desenlace     desenlace_[MAX_NODOS];
  uint8_t       sensores_[MAX_NODOS];
  bool          repetido_;
  uint32_t      proxInicio_, inicioRonda_, tMedir_, tPedido_, tPausa_;
  uint32_t      duracion_;
  bool          excedida_;
};

#endif
