"""Endpoints de mediciones: lecturas recientes e historial."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import config
from calibracion import METRICAS_CALIBRABLES, cargar_calibrador
from db import METRICAS, get_connection

bp = Blueprint("mediciones", __name__, url_prefix="/api")

RANGOS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

LIMITE_MAXIMO = 500

# El gateway publica los timestamps en UTC con sufijo Z y el servicio de ingesta
# los guarda tal cual, asi que ese es el formato canonico en la base. Los
# rangos de las consultas se construyen en el mismo formato: las columnas se
# comparan como texto, de modo que mezclar hora local con hora UTC desplazaria
# las consultas tantas horas como valga el huso.
FORMATO_TS = "%Y-%m-%dT%H:%M:%SZ"


def _texto_utc(momento):
    return momento.strftime(FORMATO_TS)


def _quiere_crudo():
    """?crudo=1 devuelve lo que midio el sensor, sin calibracion."""
    return request.args.get("crudo", "").lower() in {"1", "true", "si"}


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
    crudo = _quiere_crudo()

    with get_connection() as conn:
        estaciones = [
            fila["id"]
            for fila in conn.execute(
                "SELECT id FROM estaciones WHERE activa = 1 ORDER BY id ASC"
            )
        ]
        calibrador = None if crudo else cargar_calibrador(conn, estaciones)

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
            filas = [dict(f) for f in reversed(filas)]
            if calibrador:
                # pm25/pm10 pasan a ser los calibrados; el crudo viaja en
                # pm25_crudo/pm10_crudo para que siempre se pueda comparar.
                filas = [calibrador.calibrar_fila(station_id, f) for f in filas]
            resultado[station_id] = filas

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
        ahora = datetime.now(timezone.utc)
        desde = _texto_utc(ahora - RANGOS[rango])
        hasta = _texto_utc(ahora)
    elif start_date and end_date:
        # Las fechas llegan del <input type="date"> del navegador: son dias del
        # calendario local. Hay que correrlas al UTC en que estan guardadas,
        # o el primer y el ultimo dia del rango quedan recortados.
        desplazamiento = timedelta(hours=config.TZ_OFFSET_HORAS)
        try:
            inicio_local = datetime.fromisoformat(f"{start_date}T00:00:00")
            fin_local = datetime.fromisoformat(f"{end_date}T23:59:59")
        except ValueError:
            return jsonify({"error": "Fechas invalidas. Formato esperado: aaaa-mm-dd."}), 400
        if inicio_local > fin_local:
            return jsonify({"error": "La fecha de inicio es posterior a la de fin."}), 400
        desde = _texto_utc(inicio_local - desplazamiento)
        hasta = _texto_utc(fin_local - desplazamiento)
    else:
        return (
            jsonify({"error": "Faltan parametros de tiempo ('rango' o 'start'/'end')."}),
            400,
        )

    # La humedad se trae siempre: el modelo de calibracion con HR la necesita.
    sql = f"""
        SELECT timestamp, {metrica} AS valor, humedad
        FROM mediciones
        WHERE station_id = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
    """

    with get_connection() as conn:
        filas = conn.execute(sql, (station_id, desde, hasta)).fetchall()
        calibrar = metrica in METRICAS_CALIBRABLES and not _quiere_crudo()
        calibrador = cargar_calibrador(conn, [station_id]) if calibrar else None

    # Formato {x, y} que consume Chart.js directamente. Si la metrica se
    # calibra, 'y' es el valor calibrado y 'crudo' el del sensor.
    if not calibrador:
        return jsonify([{"x": f["timestamp"], "y": f["valor"]} for f in filas])

    puntos = []
    for f in filas:
        valor, _ = calibrador.valor(
            station_id, metrica, f["timestamp"], f["valor"], f["humedad"]
        )
        puntos.append({"x": f["timestamp"], "y": valor, "crudo": f["valor"]})
    return jsonify(puntos)


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
