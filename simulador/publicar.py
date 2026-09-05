#!/usr/bin/env python3
"""
Publica mediciones falsas en el broker MQTT, como si fueran del gateway.

Mismo topic y mismo JSON que publica el ESP32, para poder ver el dashboard con
datos sin tener el hardware prendido.

    python3 publicar.py --clave gisd2026
    python3 publicar.py --clave gisd2026 --historico 7
"""
import argparse
import json
import random
import ssl
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

BROKER = "air-quality-campus-una.duckdns.org"
PUERTO = 8883
USUARIO = "tfg_nodo"
NODOS = [1, 2]


def medicion(momento):
    """Valores al azar dentro de rangos plausibles."""
    pm25 = random.uniform(5, 40)
    return {
        "timestamp": momento.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pm25": round(pm25, 2),
        "pm10": round(pm25 * random.uniform(1.8, 2.6), 2),
        "temperatura": round(random.uniform(18, 33), 2),
        "humedad": round(random.uniform(40, 90), 2),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clave", required=True, help="clave del usuario tfg_nodo")
    p.add_argument("--intervalo", type=float, default=5, help="segundos entre publicaciones")
    p.add_argument("--historico", type=int, default=0, help="dias de datos pasados a generar y salir")
    p.add_argument("--cadencia", type=int, default=300, help="segundos entre muestras del historico")
    op = p.parse_args()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cliente.username_pw_set(USUARIO, op.clave)
    cliente.tls_set(cert_reqs=ssl.CERT_NONE)   # el gateway usa setInsecure()
    cliente.tls_insecure_set(True)
    cliente.connect(BROKER, PUERTO, 60)
    cliente.loop_start()
    print(f"Conectado a {BROKER}:{PUERTO}")

    try:
        if op.historico:
            momento = datetime.now(timezone.utc) - timedelta(days=op.historico)
            fin = datetime.now(timezone.utc)
            n = 0
            while momento < fin:
                for id_nodo in NODOS:
                    publicar(cliente, id_nodo, momento, silencioso=True)
                    n += 1
                momento += timedelta(seconds=op.cadencia)
            print(f"{n} mediciones históricas publicadas.")
            time.sleep(2)   # dar tiempo a que salgan todas antes de cerrar
            return

        while True:
            for id_nodo in NODOS:
                publicar(cliente, id_nodo, datetime.now(timezone.utc))
            time.sleep(op.intervalo)
    except KeyboardInterrupt:
        print("\nCortado.")
    finally:
        cliente.loop_stop()
        cliente.disconnect()


def publicar(cliente, id_nodo, momento, silencioso=False):
    topic = f"estaciones/estacion_{id_nodo:02d}/data"
    datos = medicion(momento)
    cliente.publish(topic, json.dumps(datos))
    if not silencioso:
        print(f"{topic}  pm25={datos['pm25']}  temp={datos['temperatura']}")


if __name__ == "__main__":
    main()
