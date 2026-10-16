"""Endpoints de estaciones: el catalogo de nodos de la red."""
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from configuracion_red import MAX_NODOS, direcciones_activas, publicar_configuracion
from db import get_connection
from seguridad import registrar, requiere_rol

bp = Blueprint("estaciones", __name__, url_prefix="/api/estaciones")

# El identificador es el segmento del topic MQTT y lo arma el gateway a partir
# de la direccion LoRa. Por eso no lo escribe una persona: se deriva, y asi no
# puede quedar desalineado con lo que el gateway publica.
def _id_desde_direccion(direccion):
    return f"estacion_{direccion:02d}"


def _fila_a_dict(fila):
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "latitud": fila["latitud"],
        "longitud": fila["longitud"],
        "activa": bool(fila["activa"]),
        "direccion_lora": fila["direccion_lora"],
        "creada_en": fila["creada_en"],
    }


def _validar_coordenada(valor, nombre, minimo, maximo):
    """Devuelve (valor_normalizado, error). None es valido: coordenada sin cargar."""
    if valor is None or valor == "":
        return None, None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None, f"'{nombre}' tiene que ser un numero."
    if not minimo <= numero <= maximo:
        return None, f"'{nombre}' tiene que estar entre {minimo} y {maximo}."
    return numero, None


def _validar_direccion(valor):
    try:
        direccion = int(valor)
    except (TypeError, ValueError):
        return None, "La direccion LoRa tiene que ser un numero entero."
    # El campo de la trama binaria es un uint8, y el 0 queda reservado.
    if not 1 <= direccion <= 255:
        return None, "La direccion LoRa tiene que estar entre 1 y 255."
    return direccion, None


@bp.get("")
def listar():
    """Lista las estaciones. Por defecto solo las activas."""
    incluir_inactivas = request.args.get("incluir_inactivas", "").lower() in {
        "1", "true", "si",
    }
    sql = "SELECT * FROM estaciones"
    if not incluir_inactivas:
        sql += " WHERE activa = 1"
    sql += " ORDER BY direccion_lora IS NULL, direccion_lora ASC, id ASC"

    with get_connection() as conn:
        filas = conn.execute(sql).fetchall()
    return jsonify([_fila_a_dict(f) for f in filas])


@bp.post("")
@requiere_rol("admin", "operador")
def crear():
    """Da de alta una estacion. El identificador se deriva de la direccion."""
    datos = request.get_json(silent=True) or {}

    direccion, error = _validar_direccion(datos.get("direccion_lora"))
    if error:
        return jsonify({"error": error}), 400

    nombre = str(datos.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio."}), 400

    latitud, error = _validar_coordenada(datos.get("latitud"), "latitud", -90, 90)
    if error:
        return jsonify({"error": error}), 400
    longitud, error = _validar_coordenada(datos.get("longitud"), "longitud", -180, 180)
    if error:
        return jsonify({"error": error}), 400

    station_id = _id_desde_direccion(direccion)
    ahora = datetime.now(timezone.utc).isoformat()

    try:
        with get_connection() as conn:
            activas = conn.execute(
                "SELECT COUNT(*) FROM estaciones "
                "WHERE activa = 1 AND direccion_lora IS NOT NULL"
            ).fetchone()[0]
            if activas >= MAX_NODOS:
                return (
                    jsonify({
                        "error": f"La red admite hasta {MAX_NODOS} nodos activos. "
                                 "El limite lo impone el tiempo del ciclo de "
                                 "interrogacion, no la memoria del gateway."
                    }),
                    409,
                )

            conn.execute(
                """
                INSERT INTO estaciones
                    (id, nombre, latitud, longitud, activa, direccion_lora, creada_en)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (station_id, nombre, latitud, longitud, direccion, ahora),
            )
            conn.commit()
            fila = conn.execute(
                "SELECT * FROM estaciones WHERE id = ?", (station_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return (
            jsonify({"error": f"Ya existe una estacion con la direccion LoRa {direccion}."}),
            409,
        )

    registrar("alta_estacion", station_id, f"{nombre} (LoRa {direccion})")
    respuesta = _fila_a_dict(fila)
    respuesta["configuracion_publicada"] = publicar_configuracion()
    return jsonify(respuesta), 201


@bp.put("/<station_id>")
@requiere_rol("admin", "operador")
def actualizar(station_id):
    """Edita nombre, coordenadas o estado. El identificador y la direccion no."""
    datos = request.get_json(silent=True) or {}

    # La direccion LoRa es la identidad del nodo: el gateway la usa para
    # interrogarlo y para armar el topic donde publica. Cambiarla dejaria las
    # mediciones historicas colgadas de un identificador que ya nadie alimenta.
    if "direccion_lora" in datos:
        return (
            jsonify({
                "error": "La direccion LoRa no se puede cambiar. Dá de baja la "
                         "estacion y creá una nueva con la direccion correcta."
            }),
            400,
        )

    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM estaciones WHERE id = ?", (station_id,)
        ).fetchone()
        if fila is None:
            return jsonify({"error": "Estacion no encontrada."}), 404

        nombre = fila["nombre"]
        if "nombre" in datos:
            nombre = str(datos["nombre"]).strip()
            if not nombre:
                return jsonify({"error": "El nombre no puede quedar vacio."}), 400

        latitud = fila["latitud"]
        if "latitud" in datos:
            latitud, error = _validar_coordenada(datos["latitud"], "latitud", -90, 90)
            if error:
                return jsonify({"error": error}), 400

        longitud = fila["longitud"]
        if "longitud" in datos:
            longitud, error = _validar_coordenada(datos["longitud"], "longitud", -180, 180)
            if error:
                return jsonify({"error": error}), 400

        activa = fila["activa"]
        if "activa" in datos:
            activa = 1 if datos["activa"] else 0

        conn.execute(
            "UPDATE estaciones SET nombre = ?, latitud = ?, longitud = ?, activa = ? WHERE id = ?",
            (nombre, latitud, longitud, activa, station_id),
        )
        conn.commit()
        fila = conn.execute(
            "SELECT * FROM estaciones WHERE id = ?", (station_id,)
        ).fetchone()

    registrar("edicion_estacion", station_id, nombre)
    respuesta = _fila_a_dict(fila)
    # Solo el estado de actividad cambia la lista que escucha el gateway.
    respuesta["configuracion_publicada"] = (
        publicar_configuracion() if "activa" in datos else None
    )
    return jsonify(respuesta)


@bp.delete("/<station_id>")
@requiere_rol("admin", "operador")
def dar_de_baja(station_id):
    """Baja logica: marca la estacion como inactiva.

    Nunca se borra el registro, porque las mediciones historicas siguen
    referenciando ese station_id y perderlo dejaria datos huerfanos sin nombre.
    """
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM estaciones WHERE id = ?", (station_id,)
        ).fetchone()
        if fila is None:
            return jsonify({"error": "Estacion no encontrada."}), 404

        conn.execute("UPDATE estaciones SET activa = 0 WHERE id = ?", (station_id,))
        conn.commit()

    registrar("baja_estacion", station_id)
    return jsonify({
        "id": station_id,
        "activa": False,
        "configuracion_publicada": publicar_configuracion(),
    })


@bp.post("/configuracion")
@requiere_rol("admin", "operador")
def republicar():
    """Reenvia la configuracion al gateway.

    Sirve cuando una publicacion anterior fallo porque el broker no estaba
    disponible: la base ya quedo bien, lo que falta es que el gateway se entere.
    """
    publicada = publicar_configuracion()
    registrar("republicar_configuracion", "gateway", "ok" if publicada else "fallo")
    cuerpo = {
        "configuracion_publicada": publicada,
        "nodos": direcciones_activas(),
    }
    if not publicada:
        cuerpo["error"] = ("El broker no acepto la configuracion. "
                           "La lista de nodos en la base esta bien; lo que falta "
                           "es que el gateway la reciba.")
        return jsonify(cuerpo), 503
    return jsonify(cuerpo)
