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
TOPIC_PARAMETROS = "gateway/config/parametros"

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


def _publicar_retenido(topic, carga):
    """Publica `carga` retenida y con QoS 1. True si el broker la confirmo."""
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id=f"tfg_api_{topic.rsplit('/', 1)[-1]}")
    if config.MQTT_USER:
        cliente.username_pw_set(config.MQTT_USER, config.MQTT_PASS)

    try:
        cliente.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=10)
        cliente.loop_start()
        # QoS 1 y retained: el broker confirma la entrega y guarda el mensaje
        # para los suscriptores futuros.
        info = cliente.publish(topic, carga, qos=1, retain=True)
        info.wait_for_publish(timeout=5)
        return info.is_published()
    except Exception:
        log.exception("No se pudo publicar en %s", topic)
        return False
    finally:
        try:
            cliente.loop_stop()
            cliente.disconnect()
        except Exception:
            pass


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
    entregado = _publicar_retenido(TOPIC_NODOS, carga)
    if entregado:
        log.info("Configuracion publicada: %d nodos %s", len(nodos), nodos)
    else:
        log.warning("El broker no confirmo la publicacion de la configuracion")
    return entregado


# ---------------------------------------------------------------------------
# Parametros del gateway
# ---------------------------------------------------------------------------
# Los mismos rangos y tiempos que firmware/gateway/planificador.h y gestor.py.
# Se validan en los tres lugares: el panel y el gestor para dar un error claro,
# el firmware porque es la ultima barrera.
LIMITES = {
    "periodo_s": (60, 3600),
    "estabilizacion_s": (15, 60),
    "timeout_ms": (500, 5000),
}
POR_DEFECTO = {"periodo_s": 300, "estabilizacion_s": 30, "timeout_ms": 3000, "publicar": True}
MARGEN_MEDICION_MS = 2000
PAUSA_ENTRE_NODOS_MS = 100
VIDA_LASER_H = 8000   # vida util del laser del SDS011 segun su hoja de datos


def duracion_maxima_ronda_s(p, n):
    """Lo que tarda una ronda en el peor caso: ningun nodo responde."""
    return (p["estabilizacion_s"] * 1000 + MARGEN_MEDICION_MS
            + n * (p["timeout_ms"] + PAUSA_ENTRE_NODOS_MS)) / 1000


def validar_parametros(p, n):
    """None si los parametros son validos para n nodos; si no, el motivo."""
    for clave, (minimo, maximo) in LIMITES.items():
        valor = p.get(clave)
        # bool es subclase de int en Python: True pasaria como 1.
        if isinstance(valor, bool) or not isinstance(valor, int):
            return f"{clave} tiene que ser un numero entero."
        if not minimo <= valor <= maximo:
            return f"{clave} tiene que estar entre {minimo} y {maximo}."
    if not isinstance(p.get("publicar"), bool):
        return "publicar tiene que ser true o false."
    peor = duracion_maxima_ronda_s(p, n)
    if peor > p["periodo_s"]:
        minimo = int(peor) + (0 if peor == int(peor) else 1)
        return (f"Con {n} nodos, una ronda puede tardar hasta {peor:.0f} s si ninguno "
                f"responde: el periodo tiene que ser de al menos {minimo} s.")
    return None


def publicar_parametros(p):
    """Publica el documento completo de parametros. True si el broker lo acepto.

    Se publica entero y no solo lo que cambio: el mensaje retenido es el estado
    deseado, el que recibe el gateway al reconectarse aunque haya perdido su
    NVS. El panel y gestor.py escriben el mismo topic y siempre el documento
    completo, asi que gana el ultimo que publico.
    """
    carga = json.dumps({clave: p[clave] for clave in POR_DEFECTO})
    entregado = _publicar_retenido(TOPIC_PARAMETROS, carga)
    if entregado:
        log.info("Parametros publicados: %s", carga)
    return entregado
