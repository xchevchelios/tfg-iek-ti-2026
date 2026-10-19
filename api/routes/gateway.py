"""Endpoints del gateway: parametros de operacion y acceso de solo lectura al
broker para el panel.

El estado del gateway (lo deseado y lo que reporta haber aplicado) no pasa por
la API: el panel lo lee directo de los mensajes retenidos del broker, con un
usuario MQTT de solo lectura. La API interviene solo donde hace falta
autoridad: validar y publicar un cambio, y dejarlo en la bitacora.
"""
from flask import Blueprint, jsonify, request

import config
from configuracion_red import (
    LIMITES,
    MARGEN_MEDICION_MS,
    PAUSA_ENTRE_NODOS_MS,
    POR_DEFECTO,
    VIDA_LASER_H,
    direcciones_activas,
    publicar_parametros,
    validar_parametros,
)
from seguridad import registrar, requiere_rol

bp = Blueprint("gateway", __name__, url_prefix="/api/gateway")


@bp.get("/limites")
@requiere_rol("admin", "operador")
def limites():
    """Rangos y constantes para que el panel valide y calcule la vista previa
    con los mismos numeros que el servidor, sin repetirlos en el JavaScript."""
    return jsonify({
        "limites": {clave: {"min": a, "max": b} for clave, (a, b) in LIMITES.items()},
        "por_defecto": POR_DEFECTO,
        "margen_medicion_ms": MARGEN_MEDICION_MS,
        "pausa_entre_nodos_ms": PAUSA_ENTRE_NODOS_MS,
        "vida_laser_h": VIDA_LASER_H,
        "nodos_activos": len(direcciones_activas()),
    })


@bp.put("/parametros")
@requiere_rol("admin", "operador")
def guardar_parametros():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({"error": "Se esperaba un objeto JSON."}), 400

    faltan = [clave for clave in POR_DEFECTO if clave not in datos]
    if faltan:
        return jsonify({"error": f"Faltan campos: {', '.join(faltan)}."}), 400
    parametros = {clave: datos[clave] for clave in POR_DEFECTO}

    motivo = validar_parametros(parametros, len(direcciones_activas()))
    if motivo:
        return jsonify({"error": motivo}), 400

    # A diferencia de una estacion, los parametros no se guardan en la base:
    # el mensaje retenido ES el estado deseado. Si el broker no lo acepta, no
    # quedo nada guardado en ningun lado, y la operacion fallo.
    if not publicar_parametros(parametros):
        return jsonify({"error": "El broker no acepto los parametros. No se aplico ningun cambio."}), 503

    registrar(
        "parametros_gateway", "gateway",
        f"periodo {parametros['periodo_s']} s, ventana {parametros['estabilizacion_s']} s, "
        f"timeout {parametros['timeout_ms']} ms, publicar {'si' if parametros['publicar'] else 'no'}",
    )
    return jsonify({"publicado": True, "parametros": parametros})


@bp.get("/credenciales-mqtt")
@requiere_rol("admin", "operador")
def credenciales_mqtt():
    """Credenciales de solo lectura del broker para el panel.

    Solo se entregan con sesion iniciada, y nunca se cachean. El usuario MQTT
    puede leer estaciones/# y gateway/# y no puede publicar nada (ACL).
    """
    if not (config.MQTT_PANEL_USER and config.MQTT_PANEL_PASS):
        return jsonify({"error": "El acceso al broker desde el panel no esta configurado."}), 503
    respuesta = jsonify({"usuario": config.MQTT_PANEL_USER, "clave": config.MQTT_PANEL_PASS})
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta
