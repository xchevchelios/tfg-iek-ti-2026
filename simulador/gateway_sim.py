#!/usr/bin/env python3
"""
Simulador del gateway LoRa del TFG.

Reemplaza al ESP32 para poder probar el sistema completo sin hardware: emula
los nodos, el enlace LoRa y el propio gateway, y publica en el broker MQTT real
exactamente el mismo topic y el mismo JSON que publica el firmware.

Lo que replica del gateway real (gateway_master.ino):

  - Polling master/slave: un solo nodo por vez, porque hay un unico canal y un
    unico SF. El gateway pregunta, espera, y recien entonces pasa al siguiente.
  - Las mismas tramas binarias empaquetadas (ComandoPolling y PayloadSensores),
    con el mismo layout de bytes. Si el firmware cambia la estructura y este
    simulador deja de coincidir, es una senal de que algo se desincronizo.
  - Los mismos tiempos: 2 s entre nodos, 3 s de timeout.
  - El mismo topic: estaciones/estacion_NN/data
  - El mismo payload: timestamp en UTC con sufijo Z, pm25, pm10, temperatura,
    humedad. El id de la estacion viaja en el topic, no en el cuerpo.
  - El mismo Last Will: estaciones/estado con {"gateway_status":"OFFLINE"}
    retenido, y el ONLINE retenido al conectar.

Lo que agrega, por ser un simulador:

  - Perdida de paquetes configurable, para ejercitar el camino del timeout.
  - Modo historico: genera dias de mediciones pasadas de una sola vez, para
    tener con que llenar el dashboard sin esperar en tiempo real.
  - Modo seco: imprime lo que publicaria, sin tocar el broker.

Uso tipico:

    python3 gateway_sim.py --clave 'la-clave-de-tfg_nodo'
    python3 gateway_sim.py --clave '...' --historico 7 --cadencia 300
    python3 gateway_sim.py --seco
"""
import argparse
import json
import logging
import math
import random
import signal
import ssl
import struct
import sys
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

log = logging.getLogger("gateway-sim")

# --- Tramas binarias: mismo layout que las structs packed del firmware ---
# ComandoPolling  { uint8 target_id; uint8 command; }
FORMATO_COMANDO = "<BB"
# PayloadSensores { uint8 id_nodo; uint16 secuencia; float pm25, pm10, temperatura, humedad; }
FORMATO_TRAMA = "<BHffff"

CMD_SOLICITAR_DATOS = 0x01

TOPIC_ESTADO = "estaciones/estado"
FORMATO_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


# =====================================================================
# Modelo ambiental
# =====================================================================
class ModeloAmbiental:
    """Genera series plausibles para un nodo.

    No son numeros al azar: hay un ciclo diario y reversion a la media, para que
    dos muestras consecutivas se parezcan entre si. Una serie de ruido blanco se
    nota a simple vista en el grafico y no sirve para mostrar el sistema.
    """

    def __init__(self, semilla, sesgo_pm=0.0, huso_horas=-3.0):
        self.rng = random.Random(semilla)
        self.sesgo_pm = sesgo_pm
        self.huso = timedelta(hours=huso_horas)
        self.pm25 = 12.0 + self.rng.uniform(-3.0, 3.0)

    def _hora_decimal(self, momento):
        # El ciclo diario se calcula en hora LOCAL del campus, aunque el
        # timestamp que se publica sea UTC: el pico de trafico ocurre a las 7 de
        # la manana de Asuncion, no del meridiano de Greenwich.
        #
        # El huso se pasa explicito y no se toma de la maquina: la VM corre en
        # UTC, asi que confiar en la hora del sistema desplazaria los picos tres
        # horas segun donde se ejecute el simulador.
        local = momento + self.huso
        return local.hour + local.minute / 60.0

    def muestrear(self, momento):
        hora = self._hora_decimal(momento)

        # Dos picos de material particulado, en los horarios de trafico.
        trafico = 6.0 * math.exp(-((hora - 7.5) ** 2) / 2.0)
        trafico += 7.0 * math.exp(-((hora - 18.5) ** 2) / 2.5)
        objetivo = 11.0 + self.sesgo_pm + trafico

        # Reversion a la media: la serie tiende al objetivo sin saltar.
        self.pm25 += (objetivo - self.pm25) * 0.25 + self.rng.gauss(0, 1.1)
        self.pm25 = max(1.0, self.pm25)

        # El PM10 incluye al PM2.5 y le suma la fraccion gruesa, asi que siempre
        # es mayor. La relacion tipica en aire urbano va de 1.8 a 2.6.
        pm10 = self.pm25 * self.rng.uniform(1.8, 2.6) + self.rng.gauss(0, 1.5)

        # Temperatura y humedad: ciclo diario, con pico termico a media tarde y
        # la humedad moviendose en contrafase.
        fase = math.cos(2 * math.pi * (hora - 15.0) / 24.0)
        temperatura = 25.0 + 7.0 * fase + self.rng.gauss(0, 0.4)
        humedad = 65.0 - 25.0 * fase + self.rng.gauss(0, 2.0)

        return {
            "pm25": round(self.pm25, 2),
            "pm10": round(max(self.pm25 + 1.0, pm10), 2),
            "temperatura": round(temperatura, 2),
            "humedad": round(min(98.0, max(20.0, humedad)), 2),
        }


# =====================================================================
# Nodo y enlace LoRa simulados
# =====================================================================
class NodoSimulado:
    """Un ESP32 con SDS011 y DHT21 esperando que el gateway lo interrogue."""

    def __init__(self, id_nodo, semilla=None, sesgo_pm=0.0, huso_horas=-3.0):
        self.id_nodo = id_nodo
        self.secuencia = 0
        self.modelo = ModeloAmbiental(
            semilla if semilla is not None else id_nodo * 1000, sesgo_pm, huso_horas
        )

    def responder(self, momento):
        """Arma la trama binaria de respuesta, igual que el firmware del nodo."""
        lectura = self.modelo.muestrear(momento)
        self.secuencia = (self.secuencia + 1) % 65536
        trama = struct.pack(
            FORMATO_TRAMA,
            self.id_nodo,
            self.secuencia,
            lectura["pm25"],
            lectura["pm10"],
            lectura["temperatura"],
            lectura["humedad"],
        )
        return trama


class EnlaceLoRaSimulado:
    """El aire entre el gateway y los nodos.

    Es lo unico que en el sistema real puede fallar sin que sea un bug: un nodo
    sin bateria, una colision, un obstaculo. Por eso la perdida es configurable.
    """

    def __init__(self, nodos, probabilidad_perdida=0.0, semilla=None):
        self.nodos = {n.id_nodo: n for n in nodos}
        self.probabilidad_perdida = probabilidad_perdida
        self.rng = random.Random(semilla)

    def transmitir(self, comando_bytes, momento):
        target_id, comando = struct.unpack(FORMATO_COMANDO, comando_bytes)

        nodo = self.nodos.get(target_id)
        if nodo is None or comando != CMD_SOLICITAR_DATOS:
            return None

        if self.rng.random() < self.probabilidad_perdida:
            return None  # la trama se perdio: el gateway va a dar timeout

        return nodo.responder(momento)


# =====================================================================
# Gateway
# =====================================================================
class GatewaySimulado:
    def __init__(self, opciones):
        self.op = opciones
        self.enlace = EnlaceLoRaSimulado(
            [
                NodoSimulado(i, sesgo_pm=indice * 6.0, huso_horas=opciones.huso)
                for indice, i in enumerate(opciones.nodos)
            ],
            probabilidad_perdida=opciones.perdida,
            semilla=opciones.semilla,
        )
        self.cliente = None
        self.corriendo = True
        self.publicadas = 0
        self.timeouts = 0

    # --- MQTT ---
    def conectar(self):
        if self.op.seco:
            log.info("Modo seco: no se conecta al broker.")
            return

        client_id = f"Gateway_Sim_{random.randint(0, 1000)}"
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)

        if self.op.usuario:
            self.cliente.username_pw_set(self.op.usuario, self.op.clave)

        if self.op.tls:
            self.cliente.tls_set(cert_reqs=ssl.CERT_NONE)
            # El firmware usa espClient.setInsecure(), asi que el simulador hace
            # lo mismo para reproducir su comportamiento. No es lo deseable en
            # produccion, pero aca el objetivo es ser fiel al gateway real.
            self.cliente.tls_insecure_set(True)

        # Last Will: si el gateway se cae sin avisar, el broker publica esto.
        self.cliente.will_set(
            TOPIC_ESTADO, json.dumps({"gateway_status": "OFFLINE"}), qos=0, retain=True
        )

        log.info("Conectando a %s:%s ...", self.op.broker, self.op.puerto)
        self.cliente.connect(self.op.broker, self.op.puerto, keepalive=60)
        self.cliente.loop_start()

        self.cliente.publish(
            TOPIC_ESTADO, json.dumps({"gateway_status": "ONLINE"}), qos=0, retain=True
        )
        log.info("Conectado. Publicado gateway_status ONLINE.")

    def desconectar(self):
        if self.cliente is None:
            return
        self.cliente.publish(
            TOPIC_ESTADO, json.dumps({"gateway_status": "OFFLINE"}), qos=0, retain=True
        )
        time.sleep(0.2)
        self.cliente.loop_stop()
        self.cliente.disconnect()
        log.info("Desconectado del broker.")

    def publicar(self, trama, momento):
        """Desempaqueta la trama y la publica igual que TareaGestionRed()."""
        id_nodo, secuencia, pm25, pm10, temperatura, humedad = struct.unpack(
            FORMATO_TRAMA, trama
        )

        topic = f"estaciones/estacion_{id_nodo:02d}/data"
        payload = json.dumps(
            {
                "timestamp": momento.astimezone(timezone.utc).strftime(FORMATO_TIMESTAMP),
                "pm25": round(pm25, 2),
                "pm10": round(pm10, 2),
                "temperatura": round(temperatura, 2),
                "humedad": round(humedad, 2),
            }
        )

        if self.op.seco:
            log.info("[SECO] %s <- %s", topic, payload)
        else:
            resultado = self.cliente.publish(topic, payload, qos=0)
            if resultado.rc != mqtt.MQTT_ERR_SUCCESS:
                log.error("Fallo al publicar en %s (rc=%s)", topic, resultado.rc)
                return
            log.info(
                "[CLOUD] %s | sec #%d | PM2.5 %.1f | temp %.1f",
                topic, secuencia, pm25, temperatura,
            )

        self.publicadas += 1

    # --- Modo historico ---
    def generar_historico(self):
        """Publica mediciones pasadas, para llenar el dashboard de una sola vez.

        No pasa por la maquina de estados del polling: no tendria sentido
        esperar 2 segundos por muestra para generar una semana de datos. Igual
        sale por el broker, como cualquier medicion real: no se escribe en la
        base directamente.
        """
        fin = datetime.now(timezone.utc)
        inicio = fin - timedelta(days=self.op.historico)
        paso = timedelta(seconds=self.op.cadencia)

        total = int((fin - inicio) / paso) * len(self.op.nodos)
        log.info(
            "Generando %d dias de historico cada %d s (~%d mediciones)...",
            self.op.historico, self.op.cadencia, total,
        )

        momento = inicio
        enviadas = 0
        while momento < fin and self.corriendo:
            for id_nodo in self.op.nodos:
                nodo = self.enlace.nodos[id_nodo]
                self.publicar(nodo.responder(momento), momento)
                enviadas += 1
                # Un respiro para no saturar al servicio de ingesta, que hace
                # una transaccion de SQLite por mensaje.
                time.sleep(self.op.pausa_historico)
            momento += paso

        log.info("Historico completo: %d mediciones publicadas.", enviadas)

    # --- Bucle de polling, equivalente al loop() del firmware ---
    def bucle_polling(self):
        log.info(
            "Polling de los nodos %s cada %.1f s (timeout %.1f s, perdida %.0f%%).",
            self.op.nodos, self.op.intervalo, self.op.timeout, self.op.perdida * 100,
        )

        indice = 0
        while self.corriendo:
            id_objetivo = self.op.nodos[indice]

            comando = struct.pack(FORMATO_COMANDO, id_objetivo, CMD_SOLICITAR_DATOS)
            log.info("[LORA TX] Preguntando a estacion_%02d", id_objetivo)

            momento = datetime.now(timezone.utc)
            respuesta = self.enlace.transmitir(comando, momento)

            if respuesta is None:
                # El gateway real espera hasta TIMEOUT_MS antes de rendirse.
                self._dormir(self.op.timeout)
                self.timeouts += 1
                log.warning("[-] TIMEOUT: estacion_%02d no respondio.", id_objetivo)
            else:
                # Latencia de ida y vuelta del enlace LoRa.
                self._dormir(self.op.latencia)
                id_nodo = struct.unpack(FORMATO_TRAMA, respuesta)[0]
                if id_nodo != id_objetivo:
                    log.warning("[-] Respuesta de otro nodo (%d), descartada.", id_nodo)
                else:
                    log.info("[LORA RX] Trama de estacion_%02d", id_nodo)
                    self.publicar(respuesta, momento)

            indice = (indice + 1) % len(self.op.nodos)
            self._dormir(self.op.intervalo)

    def _dormir(self, segundos):
        """Duerme en tramos cortos para que Ctrl+C responda al instante."""
        fin = time.monotonic() + segundos
        while self.corriendo and time.monotonic() < fin:
            time.sleep(min(0.2, max(0.0, fin - time.monotonic())))

    def detener(self, *_):
        self.corriendo = False

    def ejecutar(self):
        signal.signal(signal.SIGINT, self.detener)
        signal.signal(signal.SIGTERM, self.detener)

        self.conectar()
        try:
            if self.op.historico > 0:
                self.generar_historico()
            if self.corriendo and not self.op.solo_historico:
                self.bucle_polling()
        finally:
            self.desconectar()
            log.info(
                "Resumen: %d mediciones publicadas, %d timeouts.",
                self.publicadas, self.timeouts,
            )


# =====================================================================
def parsear_argumentos(argv=None):
    p = argparse.ArgumentParser(
        description="Simulador del gateway LoRa: emula los nodos y publica en el broker MQTT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Uso tipico:")[-1],
    )

    g = p.add_argument_group("broker")
    g.add_argument("--broker", default="air-quality-campus-una.duckdns.org")
    g.add_argument("--puerto", type=int, default=8883)
    g.add_argument("--usuario", default="tfg_nodo")
    g.add_argument("--clave", default="", help="clave del usuario MQTT")
    g.add_argument("--sin-tls", dest="tls", action="store_false",
                   help="conectar en claro (para un broker local en 1883)")

    g = p.add_argument_group("red simulada")
    g.add_argument("--nodos", default="1,2", help="ids de los nodos, separados por coma")
    g.add_argument("--intervalo", type=float, default=2.0,
                   help="segundos entre nodos (INTERVALO_ENTRE_NODOS del firmware)")
    g.add_argument("--timeout", type=float, default=3.0,
                   help="espera antes de dar por perdido a un nodo (TIMEOUT_MS)")
    g.add_argument("--latencia", type=float, default=0.35,
                   help="ida y vuelta simulada del enlace LoRa")
    g.add_argument("--perdida", type=float, default=0.0,
                   help="probabilidad de perder una trama, entre 0 y 1")
    g.add_argument("--semilla", type=int, default=None)
    g.add_argument("--huso", type=float, default=-3.0,
                   help="desfase horario del campus respecto de UTC, para ubicar "
                        "los picos del ciclo diario (Paraguay: -3)")

    g = p.add_argument_group("historico")
    g.add_argument("--historico", type=int, default=0,
                   help="dias de mediciones pasadas a generar antes de arrancar")
    g.add_argument("--cadencia", type=int, default=300,
                   help="segundos entre muestras del historico")
    g.add_argument("--pausa-historico", type=float, default=0.01,
                   help="pausa entre publicaciones del historico")
    g.add_argument("--solo-historico", action="store_true",
                   help="generar el historico y salir, sin quedarse en vivo")

    p.add_argument("--seco", action="store_true",
                   help="imprimir lo que se publicaria, sin conectarse al broker")
    p.add_argument("-v", "--verboso", action="store_true")

    op = p.parse_args(argv)
    op.nodos = [int(x) for x in op.nodos.split(",") if x.strip()]
    if not op.nodos:
        p.error("hay que indicar al menos un nodo")
    if not 0.0 <= op.perdida <= 1.0:
        p.error("--perdida tiene que estar entre 0 y 1")
    if not op.seco and op.usuario and not op.clave:
        p.error("falta --clave (o usar --seco para probar sin broker)")
    return op


def main(argv=None):
    op = parsear_argumentos(argv)
    logging.basicConfig(
        level=logging.DEBUG if op.verboso else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    GatewaySimulado(op).ejecutar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
