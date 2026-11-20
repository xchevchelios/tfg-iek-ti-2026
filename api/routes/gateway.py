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
    DEBUG_POR_DEFECTO,
    LIMITES,
    LIMITES_DEBUG,
    MARGEN_MEDICION_MS,
    PAUSA_ENTRE_NODOS_MS,
    ULTIMA_COPIA_MEDIR_MS,
    POR_DEFECTO,
    VIDA_LASER_H,
    direcciones_activas,
    publicar_debug,
    publicar_parametros,
    validar_debug,
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
        "ultima_copia_medir_ms": ULTIMA_COPIA_MEDIR_MS,
        "vida_laser_h": VIDA_LASER_H,
        "debug": {
            "limites": {clave: {"min": a, "max": b} for clave, (a, b) in LIMITES_DEBUG.items()},
            "por_defecto": DEBUG_POR_DEFECTO,
        },
        "nodos_activos": len(direcciones_activas()),
    })


@bp.put("/parametros")
@requiere_rol("admin", "operador")
def guardar_parametros():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({"error": "Se esperaba un objeto JSON."}), 400

    # reintentos y pausa_reintento_ms son posteriores al resto: si un panel
    # viejo no los manda, va el valor por defecto en vez de rechazar el cambio.
    datos.setdefault("reintentos", POR_DEFECTO["reintentos"])
    datos.setdefault("pausa_reintento_ms", POR_DEFECTO["pausa_reintento_ms"])
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
        f"timeout {parametros['timeout_ms']} ms, reintentos {parametros['reintentos']} "
        f"(pausa {parametros['pausa_reintento_ms']} ms), "
        f"publicar {'si' if parametros['publicar'] else 'no'}",
    )
    return jsonify({"publicado": True, "parametros": parametros})


@bp.post("/debug")
@requiere_rol("admin", "operador")
def debug_lora():
    """Inicia o detiene el debug LoRa del gateway (firmware 2.7.0 en adelante).

    {"activo": true, "nodo": 2, "periodo_ms": 5000, "timeout_ms": 4000, "duracion_s": 600}
    {"activo": false}

    El resultado no pasa por la API: el panel lo lee de gateway/debug/log y
    gateway/debug/estado con el usuario MQTT de solo lectura.
    """
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({"error": "Se esperaba un objeto JSON."}), 400
    pedido = {"activo": datos.get("activo")}
    if pedido["activo"] is True:
        pedido.update({clave: datos.get(clave, valor) for clave, valor in DEBUG_POR_DEFECTO.items()})

    motivo = validar_debug(pedido)
    if motivo:
        return jsonify({"error": motivo}), 400
    if not publicar_debug(pedido):
        return jsonify({"error": "El broker no acepto el pedido de debug."}), 503

    if pedido["activo"]:
        detalle = (f"inicio: estacion {pedido['nodo']}, un pedido cada {pedido['periodo_ms']} ms, "
                   f"timeout {pedido['timeout_ms']} ms, durante {pedido['duracion_s']} s")
    else:
        detalle = "detenido"
    registrar("debug_lora", "gateway", detalle)
    return jsonify({"publicado": True, "pedido": pedido})


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


@bp.get("/fiabilidad")
@requiere_rol("admin", "operador")
def fiabilidad():
    """% de rondas sin respuesta por nodo, desde una version de firmware.

    ?firmware_min=2.4.0  (vacio = todas)   ?agrupar=dia|hora
    """
    from db import get_connection
    from fiabilidad import calcular

    try:
        with get_connection() as conn:
            return jsonify(calcular(
                conn,
                firmware_min=(request.args.get("firmware_min") or "").strip() or None,
                agrupar=request.args.get("agrupar", "dia"),
            ))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
