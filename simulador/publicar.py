#!/usr/bin/env python3
"""
Simula los nodos de la red: publica mediciones al azar en el broker MQTT.

Una sola conexion que va rotando entre las estaciones, igual que el gateway
real, que interroga a un nodo por vez. Mismo topic y mismo JSON que el ESP32.

Correr con el boton Run del editor, o:  python publicar.py
Requiere:  pip install paho-mqtt
"""
import getpass
import json
import os
import random
import ssl
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# --- Configuracion ---
BROKER = "air-quality-campus-una.duckdns.org"
PUERTO = 8883
USUARIO = "tfg_nodo"
NODOS = [1, 2]        # ids de las estaciones ya registradas
INTERVALO = 5         # segundos entre publicaciones


def pedir_clave():
    """La clave no se guarda en el archivo: el repo esta versionado."""
    if os.environ.get("MQTT_PASS"):
        return os.environ["MQTT_PASS"]
    try:
        return getpass.getpass(f"Clave de {USUARIO}: ")
    except Exception:
        # Algunas consolas de editor no permiten ocultar lo que se escribe.
        return input(f"Clave de {USUARIO}: ")


def medicion():
    """Los cuatro valores que manda un nodo, al azar dentro de rangos creibles."""
    pm25 = random.uniform(5, 40)
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pm25": round(pm25, 2),
        "pm10": round(pm25 * random.uniform(1.8, 2.6), 2),   # siempre mayor al PM2.5
        "temperatura": round(random.uniform(18, 33), 2),
        "humedad": round(random.uniform(40, 90), 2),
    }


def main():
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cliente.username_pw_set(USUARIO, pedir_clave())
    cliente.tls_set(cert_reqs=ssl.CERT_NONE)   # el gateway usa setInsecure()
    cliente.tls_insecure_set(True)

    cliente.connect(BROKER, PUERTO, 60)
    cliente.loop_start()
    print(f"Conectado a {BROKER}:{PUERTO}. Ctrl+C para cortar.\n")

    try:
        while True:
            for id_nodo in NODOS:               # rota: 1, 2, 1, 2, ...
                topic = f"estaciones/estacion_{id_nodo:02d}/data"
                datos = medicion()
                cliente.publish(topic, json.dumps(datos))
                print(f"{topic}  pm25={datos['pm25']:<6} pm10={datos['pm10']:<6} "
                      f"temp={datos['temperatura']:<6} hum={datos['humedad']}")
                time.sleep(INTERVALO)
    except KeyboardInterrupt:
        print("\nCortado.")
    finally:
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()