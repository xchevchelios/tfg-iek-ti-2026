"""Endpoints de estaciones: el catalogo de nodos de la red."""
import re
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from db import get_connection
from routes.auth import requiere_token

bp = Blueprint("estaciones", __name__, url_prefix="/api/estaciones")

# Los identificadores viajan en topics MQTT ('estaciones/<id>/data'), asi que
# se restringen a minusculas, digitos y guion bajo.
PATRON_ID = re.compile(r"^[a-z0-9_]{3,32}$")


def _fila_a_dict(fila):
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "latitud": fila["latitud"],
        "longitud": fila["longitud"],
        "activa": bool(fila["activa"]),
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


@bp.get("")
def listar():
    """Lista las estaciones. Por defecto solo las activas."""
    incluir_inactivas = request.args.get("incluir_inactivas", "").lower() in {
        "1",
        "true",
        "si",
    }
    sql = "SELECT * FROM estaciones"
    if not incluir_inactivas:
        sql += " WHERE activa = 1"
    sql += " ORDER BY id ASC"

    with get_connection() as conn:
        filas = conn.execute(sql).fetchall()
    return jsonify([_fila_a_dict(f) for f in filas])


@bp.post("")
@requiere_token
def crear():
    """Da de alta una estacion nueva."""
    datos = request.get_json(silent=True) or {}

    station_id = str(datos.get("id", "")).strip().lower()
    if not PATRON_ID.match(station_id):
        return (
            jsonify(
                {
                    "error": "El identificador solo admite minusculas, digitos y "
                    "guion bajo, entre 3 y 32 caracteres."
                }
            ),
            400,
        )

    nombre = str(datos.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio."}), 400

    latitud, error = _validar_coordenada(datos.get("latitud"), "latitud", -90, 90)
    if error:
        return jsonify({"error": error}), 400
    longitud, error = _validar_coordenada(datos.get("longitud"), "longitud", -180, 180)
    if error:
        return jsonify({"error": error}), 400

    ahora = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO estaciones (id, nombre, latitud, longitud, activa, creada_en)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (station_id, nombre, latitud, longitud, ahora),
            )
            conn.commit()
            fila = conn.execute(
                "SELECT * FROM estaciones WHERE id = ?", (station_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Ya existe una estacion con id '{station_id}'."}), 409

    return jsonify(_fila_a_dict(fila)), 201


@bp.put("/<station_id>")
@requiere_token
def actualizar(station_id):
    """Edita nombre, coordenadas o estado de una estacion. El id no se cambia."""
    datos = request.get_json(silent=True) or {}

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
            longitud, error = _validar_coordenada(
                datos["longitud"], "longitud", -180, 180
            )
            if error:
                return jsonify({"error": error}), 400

        activa = fila["activa"]
        if "activa" in datos:
            activa = 1 if datos["activa"] else 0

        conn.execute(
            """
            UPDATE estaciones
            SET nombre = ?, latitud = ?, longitud = ?, activa = ?
            WHERE id = ?
            """,
            (nombre, latitud, longitud, activa, station_id),
        )
        conn.commit()
        fila = conn.execute(
            "SELECT * FROM estaciones WHERE id = ?", (station_id,)
        ).fetchone()

    return jsonify(_fila_a_dict(fila))


@bp.delete("/<station_id>")
@requiere_token
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

    return jsonify({"id": station_id, "activa": False})
