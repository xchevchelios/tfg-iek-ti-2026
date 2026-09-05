"""Servicio de ingesta: se suscribe al broker MQTT y persiste en SQLite.

Antes se llamaba mqtt_a_sqlite.py y corria dentro de una sesion de tmux, lo que
significaba que no sobrevivia a un reinicio de la VM. Ahora corre bajo systemd
(unit tfg-ingesta, con Restart=always) y toma su configuracion del entorno.
"""
import json
import logging
import signal
import sys

import paho.mqtt.client as mqtt

import config
from db import get_connection, init_db, sembrar_estaciones_existentes

log = logging.getLogger("ingesta")

_cliente = None


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Conectado al broker %s:%s", config.MQTT_HOST, config.MQTT_PORT)
        client.subscribe(config.MQTT_TOPIC)
        log.info("Suscrito al topic %s", config.MQTT_TOPIC)
    else:
        log.error("Fallo la conexion al broker (codigo %s)", rc)


def on_disconnect(client, userdata, rc, properties=None, reason=None):
    if rc != 0:
        log.warning("Desconectado del broker (codigo %s). Paho va a reintentar.", rc)


def on_message(client, userdata, msg):
    try:
        partes = msg.topic.split("/")
        if len(partes) < 2:
            log.warning("Topic inesperado, descartado: %s", msg.topic)
            return
        station_id = partes[1]

        datos = json.loads(msg.payload)

        # El timestamp lo pone el gateway, que es quien sabe cuando se tomo la
        # medicion. Sin el no se puede ubicar el dato en la serie temporal.
        timestamp = datos.get("timestamp")
        if not timestamp:
            log.warning("Mensaje de %s sin timestamp, descartado.", station_id)
            return

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO mediciones (timestamp, station_id, pm25, pm10, temperatura, humedad)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    station_id,
                    datos.get("pm25"),
                    datos.get("pm10"),
                    datos.get("temperatura"),
                    datos.get("humedad"),
                ),
            )
            # Alta automatica de estaciones desconocidas: si aparece un nodo
            # nuevo en el broker, queda registrado con las coordenadas vacias en
            # vez de perderse. Se completa despues desde la pagina de gestion.
            conn.execute(
                """
                INSERT OR IGNORE INTO estaciones (id, nombre, latitud, longitud, activa, creada_en)
                VALUES (?, ?, NULL, NULL, 1, ?)
                """,
                (station_id, station_id.replace("_", " ").capitalize(), timestamp),
            )
            conn.commit()

        log.info("Medicion de %s guardada (%s)", station_id, timestamp)

    except json.JSONDecodeError:
        log.warning("Payload no es JSON valido: %r", msg.payload[:200])
    except Exception:
        log.exception("Error procesando el mensaje de %s", msg.topic)


def _apagar(signum, _frame):
    log.info("Senal %s recibida, cerrando.", signum)
    if _cliente is not None:
        _cliente.disconnect()
    sys.exit(0)


def main():
    global _cliente

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    init_db()
    nuevas = sembrar_estaciones_existentes()
    if nuevas:
        log.info("Estaciones sembradas desde el historico: %s", ", ".join(nuevas))

    _cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="tfg_ingesta"
    )
    if config.MQTT_USER:
        _cliente.username_pw_set(config.MQTT_USER, config.MQTT_PASS)

    _cliente.on_connect = on_connect
    _cliente.on_disconnect = on_disconnect
    _cliente.on_message = on_message

    signal.signal(signal.SIGTERM, _apagar)
    signal.signal(signal.SIGINT, _apagar)

    # connect_async + loop_forever hace que Paho reintente solo si el broker
    # todavia no esta arriba cuando systemd lanza este servicio.
    _cliente.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    log.info("Servicio de ingesta iniciado.")
    _cliente.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
