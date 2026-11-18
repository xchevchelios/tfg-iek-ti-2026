"""Endpoints de calibracion contra un equipo de referencia (PM, temperatura
y humedad).

  GET    /api/calibraciones               lista (publica: transparencia)
  POST   /api/calibraciones/ajustar       evalua los modelos, NO guarda   (admin)
  POST   /api/calibraciones               evalua y guarda un modelo       (admin)
  DELETE /api/calibraciones/<id>          anula (vuelve a regir la anterior) (admin)

El navegador nunca manda coeficientes: manda el CSV de la referencia y el
servidor los calcula. Asi una calibracion guardada siempre corresponde a datos
reales y queda reproducible (se guardan los pares usados).
"""
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

import config
from calibracion import (
    METRICAS_CALIBRABLES,
    METRICAS_PM,
    ErrorCalibracion,
    calibrar_desde_csv,
    guardar,
    modelos_de,
)
from db import get_connection
from seguridad import registrar, requiere_rol

bp = Blueprint("calibracion", __name__, url_prefix="/api/calibraciones")

# Un CSV de una semana a 1 muestra/min son ~10 000 filas, unos 400 KB.
MAX_CSV_BYTES = 2 * 1024 * 1024

COLUMNAS_LISTADO = """
    id, station_id, metrica, modelo, a, b, c, valida_desde, ventana_min, hr_max,
    n_pares, validacion, n_validacion, r2_crudo, rmse_crudo, r2, rmse, mae,
    sesgo, notas, creada_en, email, anulada, anulada_en
"""


def _error(mensaje, codigo=400):
    return jsonify({"error": mensaje}), codigo


def _leer_parametros():
    """Valida el cuerpo comun a /ajustar y al alta. Devuelve (params, error)."""
    datos = request.get_json(silent=True) or {}

    estacion = (datos.get("estacion") or "").strip()
    metrica = datos.get("metrica")
    texto_csv = datos.get("csv") or ""

    if metrica not in METRICAS_CALIBRABLES:
        return None, f"La metrica tiene que ser una de: {', '.join(METRICAS_CALIBRABLES)}."
    if len(texto_csv.encode("utf-8")) > MAX_CSV_BYTES:
        return None, "El CSV supera los 2 MB."
    try:
        ventana_min = int(datos.get("ventana_min", 60))
        hr_max = datos.get("hr_max", 80)
        hr_max = None if hr_max in (None, "") else float(hr_max)
        offset = datos.get("utc_offset_horas")
        offset = config.TZ_OFFSET_HORAS if offset in (None, "") else float(offset)
    except (TypeError, ValueError):
        return None, "Parametros numericos invalidos."
    if not 5 <= ventana_min <= 1440:
        return None, "La ventana tiene que estar entre 5 y 1440 minutos."
    if metrica not in METRICAS_PM:
        hr_max = None  # el filtro por humedad es solo para PM
    if hr_max is not None and not 30 <= hr_max <= 100:
        return None, "El limite de humedad tiene que estar entre 30 y 100 %."

    with get_connection() as conn:
        existe = conn.execute(
            "SELECT 1 FROM estaciones WHERE id = ?", (estacion,)
        ).fetchone()
    if not existe:
        return None, "Estacion inexistente."

    return {
        "estacion": estacion,
        "metrica": metrica,
        "texto_csv": texto_csv,
        "ventana_min": ventana_min,
        "hr_max": hr_max,
        "offset": offset,
        "datos": datos,
    }, None


def _evaluar(conn, p):
    return calibrar_desde_csv(
        conn, p["estacion"], p["metrica"], p["texto_csv"],
        ventana_min=p["ventana_min"], hr_max=p["hr_max"], offset_horas=p["offset"],
    )


@bp.get("")
def listar():
    estacion = request.args.get("estacion")
    incluir_anuladas = request.args.get("incluir_anuladas", "").lower() in {"1", "true", "si"}

    sql = f"SELECT {COLUMNAS_LISTADO} FROM calibraciones WHERE 1 = 1"
    params = []
    if estacion:
        sql += " AND station_id = ?"
        params.append(estacion)
    if not incluir_anuladas:
        sql += " AND anulada = 0"
    sql += " ORDER BY station_id, metrica, valida_desde DESC, id DESC"

    with get_connection() as conn:
        filas = conn.execute(sql, params).fetchall()
    return jsonify([{**dict(f), "anulada": bool(f["anulada"])} for f in filas])


@bp.post("/ajustar")
@requiere_rol("admin")
def ajustar():
    """Vista previa: compara crudo vs. los dos modelos sin guardar nada."""
    p, error = _leer_parametros()
    if error:
        return _error(error)
    try:
        with get_connection() as conn:
            return jsonify(_evaluar(conn, p))
    except ErrorCalibracion as exc:
        return _error(str(exc))


@bp.post("")
@requiere_rol("admin")
def crear():
    p, error = _leer_parametros()
    if error:
        return _error(error)

    modelo = p["datos"].get("modelo")
    if modelo not in modelos_de(p["metrica"]):
        return _error(
            f"Para {p['metrica']} el modelo tiene que ser: {', '.join(modelos_de(p['metrica']))}."
        )
    notas = (p["datos"].get("notas") or "").strip()[:500] or None

    try:
        with get_connection() as conn:
            resultado = _evaluar(conn, p)
            cal_id = guardar(
                conn, resultado, modelo,
                valida_desde=p["datos"].get("valida_desde"),
                notas=notas, usuario=g.usuario,
            )
            conn.commit()
            fila = conn.execute(
                f"SELECT {COLUMNAS_LISTADO} FROM calibraciones WHERE id = ?", (cal_id,)
            ).fetchone()
    except ErrorCalibracion as exc:
        return _error(str(exc))

    coef = resultado["modelos"][modelo]["coef"]
    registrar(
        "calibracion_alta",
        p["estacion"],
        f"#{cal_id} {p['metrica']} {modelo}: a={coef['a']} b={coef['b']} c={coef['c']}"
        f" desde {fila['valida_desde']}",
    )
    return jsonify({**dict(fila), "anulada": False}), 201


@bp.delete("/<int:cal_id>")
@requiere_rol("admin")
def anular(cal_id):
    """Baja logica. Al anularla vuelve a regir la calibracion anterior (o el
    crudo), porque la busqueda por fecha ignora las anuladas."""
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT station_id, metrica, anulada FROM calibraciones WHERE id = ?",
            (cal_id,),
        ).fetchone()
        if not fila:
            return _error("Calibracion inexistente.", 404)
        if fila["anulada"]:
            return _error("La calibracion ya estaba anulada.", 409)
        conn.execute(
            "UPDATE calibraciones SET anulada = 1, anulada_en = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), cal_id),
        )
        conn.commit()

    registrar("calibracion_anulada", fila["station_id"], f"#{cal_id} {fila['metrica']}")
    return jsonify({"id": cal_id, "anulada": True})
