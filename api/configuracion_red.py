"""Publicacion de la configuracion de la red hacia el gateway.

El gateway tenia la lista de nodos compilada adentro: la web podia dar de alta
una estacion que nunca iba a ser interrogada. Este modulo cierra ese hueco
publicando la lista vigente en un topic que el gateway escucha.

Dos decisiones que hacen que esto funcione sin coordinacion:

1. Se publica el ESTADO COMPLETO, no el cambio. Un mensaje de "se agrego el
   nodo 3" obliga al gateway a no perderse ninguno; la lista entera lo deja
   indiferente a los mensajes perdidos.

2. Se publica RETENIDO. El broker guarda el ultimo mensaje retenido de cada
   topic y se lo entrega a quien se suscriba, en el momento de suscribirse. El
   gateway que arranca, reconecta o estuvo una semana apagado recibe la lista
   vigente sin tener que pedir nada.

El topic vive en su propia rama y no bajo estaciones/, para no cruzarse con el
comodin estaciones/+/data al que esta suscrito el servicio de ingesta.
"""
import json
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import config
from db import get_connection

log = logging.getLogger("configuracion_red")

TOPIC_NODOS = "gateway/config/nodos"

# Tope de la red. No lo impone la memoria del ESP32 sino el tiempo en el aire:
# con un canal, un SF y hasta 3 s de espera por nodo, una ronda de 32 nodos ya
# supera los dos minutos. El limite esta donde deja de ser util, no donde deja
# de entrar.
MAX_NODOS = 32


def direcciones_activas():
    """Direcciones LoRa de las estaciones activas, ordenadas."""
    with get_connection() as conn:
        filas = conn.execute(
            """
            SELECT direccion_lora FROM estaciones
            WHERE activa = 1 AND direccion_lora IS NOT NULL
            ORDER BY direccion_lora ASC
            """
        ).fetchall()
    return [f["direccion_lora"] for f in filas][:MAX_NODOS]


def publicar_configuracion():
    """Publica la lista vigente de nodos. Devuelve True si el broker la acepto.

    Un fallo aca NO invalida la operacion que lo disparo: el alta o la baja ya
    quedaron en la base. Lo que queda pendiente es que el gateway se entere, y
    eso se resuelve con un reintento desde el panel. Por eso devuelve un
    booleano en vez de propagar la excepcion: quien llama decide como avisarlo.
    """
    nodos = direcciones_activas()
    carga = json.dumps({
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodos": nodos,
    })

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tfg_api_config")
    if config.MQTT_USER:
        cliente.username_pw_set(config.MQTT_USER, config.MQTT_PASS)

    try:
        cliente.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=10)
        cliente.loop_start()
        # QoS 1 y retained: el broker confirma la entrega y guarda el mensaje
        # para los suscriptores futuros.
        info = cliente.publish(TOPIC_NODOS, carga, qos=1, retain=True)
        info.wait_for_publish(timeout=5)
        entregado = info.is_published()
    except Exception:
        log.exception("No se pudo publicar la configuracion de la red")
        return False
    finally:
        try:
            cliente.loop_stop()
            cliente.disconnect()
        except Exception:
            pass

    if entregado:
        log.info("Configuracion publicada: %d nodos %s", len(nodos), nodos)
    else:
        log.warning("El broker no confirmo la publicacion de la configuracion")
    return entregado
