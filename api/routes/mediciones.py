"""Endpoints de mediciones: lecturas recientes e historial."""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from db import METRICAS, get_connection

bp = Blueprint("mediciones", __name__, url_prefix="/api")

RANGOS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

LIMITE_MAXIMO = 500


@bp.get("/mediciones/ultimas")
def ultimas():
    """Ultimas N mediciones de cada estacion activa, agrupadas por estacion.

    Reemplaza al viejo /api/datos_recientes, que devolvia una lista plana y
    asumia que las estaciones eran exactamente dos.
    """
    try:
        limite = int(request.args.get("limite", 50))
    except ValueError:
        return jsonify({"error": "'limite' tiene que ser un numero entero."}), 400
    limite = max(1, min(limite, LIMITE_MAXIMO))

    with get_connection() as conn:
        estaciones = [
            fila["id"]
            for fila in conn.execute(
                "SELECT id FROM estaciones WHERE activa = 1 ORDER BY id ASC"
            )
        ]

        resultado = {}
        for station_id in estaciones:
            filas = conn.execute(
                """
                SELECT timestamp, pm25, pm10, temperatura, humedad
                FROM mediciones
                WHERE station_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (station_id, limite),
            ).fetchall()
            # Se invierte para entregar de mas antigua a mas reciente, que es
            # el orden en que los graficos las dibujan.
            resultado[station_id] = [dict(f) for f in reversed(filas)]

    return jsonify(resultado)


@bp.get("/historial")
def historial():
    """Serie historica de una metrica de una estacion.

    /api/historial?estacion=estacion_01&metrica=pm25&rango=7d
    /api/historial?estacion=estacion_01&metrica=pm25&start=2026-08-01&end=2026-08-31
    """
    station_id = request.args.get("estacion")
    metrica = request.args.get("metrica")

    if not station_id or not metrica:
        return jsonify({"error": "Faltan los parametros 'estacion' o 'metrica'."}), 400

    # Lista blanca: 'metrica' se interpola en el SQL, asi que no puede venir
    # libre desde el cliente.
    if metrica not in METRICAS:
        return (
            jsonify({"error": f"Metrica no valida. Opciones: {', '.join(METRICAS)}."}),
            400,
        )

    rango = request.args.get("rango")
    start_date = request.args.get("start")
    end_date = request.args.get("end")

    if rango:
        if rango not in RANGOS:
            return (
                jsonify({"error": f"Rango no valido. Opciones: {', '.join(RANGOS)}."}),
                400,
            )
        desde = (datetime.now() - RANGOS[rango]).isoformat()
        hasta = datetime.now().isoformat()
    elif start_date and end_date:
        desde = f"{start_date}T00:00:00"
        hasta = f"{end_date}T23:59:59"
    else:
        return (
            jsonify({"error": "Faltan parametros de tiempo ('rango' o 'start'/'end')."}),
            400,
        )

    sql = f"""
        SELECT timestamp, {metrica}
        FROM mediciones
        WHERE station_id = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
    """

    with get_connection() as conn:
        filas = conn.execute(sql, (station_id, desde, hasta)).fetchall()

    # Formato {x, y} que consume Chart.js directamente.
    return jsonify([{"x": f["timestamp"], "y": f[metrica]} for f in filas])


@bp.get("/datos_recientes")
def datos_recientes():
    """OBSOLETO: lo consume el frontend viejo servido desde nginx.

    Se mantiene solo mientras dure la transicion a Cloudflare. Una vez retirado
    ese frontend, este endpoint se puede eliminar.
    """
    with get_connection() as conn:
        estaciones = [
            fila["id"]
            for fila in conn.execute(
                "SELECT id FROM estaciones WHERE activa = 1 ORDER BY id ASC"
            )
        ]
        salida = []
        for station_id in estaciones:
            filas = conn.execute(
                """
                SELECT * FROM mediciones
                WHERE station_id = ?
                ORDER BY timestamp DESC
                LIMIT 50
                """,
                (station_id,),
            ).fetchall()
            salida.extend(dict(f) for f in reversed(filas))

    return jsonify(salida)
