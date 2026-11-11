#!/usr/bin/env python3
"""Estadisticas de entrega por nodo a partir de las rondas registradas.

Uso (en la VM, con el venv activo, desde api/):

    python3 estadisticas_rondas.py                          # todo el historico
    python3 estadisticas_rondas.py --desde 2026-11-12 --hasta 2026-11-19
    python3 estadisticas_rondas.py --por-hora               # suma el desglose por hora
    python3 estadisticas_rondas.py --csv rondas.csv         # una fila por nodo y ronda

Las fechas son del calendario local (TZ_OFFSET_HORAS); --hasta es inclusive.
Solo lee la base: se puede correr con los servicios en marcha.
"""
import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import config
from rondas import rondas_perdidas


def _limite_utc(dia, fin=False):
    """Fecha local -> instante UTC en el formato de `recibida`."""
    if dia is None:
        return None
    d = date.fromisoformat(dia) + (timedelta(days=1) if fin else timedelta())
    local = datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=config.TZ_OFFSET_HORAS)))
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conexion_lectura():
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def cargar(conn, desde, hasta):
    filtro, params = [], []
    if desde:
        filtro.append("g.recibida >= ?")
        params.append(desde)
    if hasta:
        filtro.append("g.recibida < ?")
        params.append(hasta)
    donde = ("WHERE " + " AND ".join(filtro)) if filtro else ""

    rondas = conn.execute(
        f"SELECT g.* FROM rondas_gateway g {donde} ORDER BY g.recibida, g.id", params
    ).fetchall()
    nodos = conn.execute(
        f"""
        SELECT g.id AS ronda_id, g.arranque_id, g.ronda, g.recibida,
               n.direccion, n.desenlace, n.sensor_ok
        FROM rondas_nodo n JOIN rondas_gateway g ON g.id = n.ronda_id
        {donde}
        ORDER BY g.recibida, g.id
        """,
        params,
    ).fetchall()
    return rondas, nodos


def nombres_estaciones(conn):
    try:
        return {
            f["direccion_lora"]: f["nombre"]
            for f in conn.execute(
                "SELECT direccion_lora, nombre FROM estaciones WHERE direccion_lora IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        return {}


def resumen_gateway(rondas):
    por_arranque = defaultdict(list)
    for r in rondas:
        por_arranque[r["arranque_id"]].append(r["ronda"])
    sin_reporte = 0
    for numeros in por_arranque.values():
        for anterior, siguiente in zip(numeros, numeros[1:]):
            if anterior != siguiente:
                sin_reporte += rondas_perdidas(anterior, siguiente)
    return {
        "registradas": len(rondas),
        "sin_reporte": sin_reporte,
        "arranques": len(por_arranque),
        "excedidas": sum(r["excedida"] for r in rondas),
    }


def resumen_nodos(nodos):
    datos = defaultdict(lambda: {"total": 0, "fresca": 0, "vieja": 0, "sin_respuesta": 0,
                                 "sin_desenlace": 0, "sensor_fallando": 0,
                                 "racha": 0, "racha_max": 0})
    for f in nodos:
        d = datos[f["direccion"]]
        d["total"] += 1
        d[f["desenlace"]] += 1
        if f["desenlace"] == "fresca":
            d["racha"] = 0
            if f["sensor_ok"] == 0:
                d["sensor_fallando"] += 1
        else:
            d["racha"] += 1
            d["racha_max"] = max(d["racha_max"], d["racha"])
    return datos


def por_hora(nodos):
    tabla = defaultdict(lambda: [0, 0])  # (direccion, hora) -> [total, frescas]
    desfase = timedelta(hours=config.TZ_OFFSET_HORAS)
    for f in nodos:
        utc = datetime.strptime(f["recibida"], "%Y-%m-%dT%H:%M:%SZ")
        hora = (utc + desfase).hour
        celda = tabla[(f["direccion"], hora)]
        celda[0] += 1
        celda[1] += f["desenlace"] == "fresca"
    return tabla


def _pct(parte, total):
    return f"{100 * parte / total:5.1f} %" if total else "    -  "


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", help="fecha local AAAA-MM-DD (inclusive)")
    p.add_argument("--hasta", help="fecha local AAAA-MM-DD (inclusive)")
    p.add_argument("--por-hora", action="store_true", help="porcentaje de entrega por hora del dia")
    p.add_argument("--csv", help="exportar una fila por nodo y ronda a este archivo")
    args = p.parse_args(argv)

    conn = _conexion_lectura()
    try:
        rondas, nodos = cargar(conn, _limite_utc(args.desde), _limite_utc(args.hasta, fin=True))
    except sqlite3.OperationalError as error:
        print(f"No se pudo leer la base ({error}). ¿Ya corrio la ingesta con el registro de rondas?")
        return 1
    nombres = nombres_estaciones(conn)

    if not rondas:
        print("No hay rondas registradas en ese periodo.")
        return 0

    g = resumen_gateway(rondas)
    print(f"Periodo: {rondas[0]['recibida']} a {rondas[-1]['recibida']} (UTC)")
    print(f"Gateway: {g['registradas']} rondas registradas, {g['arranques']} arranque(s)")
    print(f"  Rondas sin reporte (gateway o ingesta sin conexion): {g['sin_reporte']}")
    print(f"  Rondas que no entraron en el periodo: {g['excedidas']}")
    print()

    cabecera = f"{'Nodo':<26}{'Rondas':>8}{'Con dato':>11}{'Sin resp.':>11}{'No oyo MEDIR':>14}{'Sensor':>9}{'Racha max':>11}"
    print(cabecera)
    print("-" * len(cabecera))
    for direccion, d in sorted(resumen_nodos(nodos).items()):
        nombre = f"{direccion:>3} {nombres.get(direccion, f'estacion_{direccion:02d}')}"[:25]
        print(f"{nombre:<26}{d['total']:>8}{_pct(d['fresca'], d['total']):>11}"
              f"{d['sin_respuesta']:>11}{d['vieja']:>14}{d['sensor_fallando']:>9}{d['racha_max']:>11}")
    print()
    print("Con dato: llego la medicion de esa ronda. Sin resp.: no contesto al ENVIAR.")
    print("No oyo MEDIR: contesto con una medicion de una ronda anterior.")
    print("Sensor: el dato llego pero algun sensor no tenia lectura valida.")
    print("Racha max: mayor cantidad de rondas seguidas sin dato.")

    if args.por_hora:
        tabla = por_hora(nodos)
        direcciones = sorted({d for d, _ in tabla})
        print()
        print("Entrega por hora del dia (hora local):")
        print("Hora " + "".join(f"{'Nodo ' + str(d):>12}" for d in direcciones))
        for hora in range(24):
            celdas = []
            for d in direcciones:
                total, frescas = tabla.get((d, hora), (0, 0))
                celdas.append(f"{_pct(frescas, total):>12}")
            print(f"{hora:02d}h " + "".join(celdas))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as archivo:
            w = csv.writer(archivo)
            w.writerow(["recibida_utc", "arranque_id", "ronda", "direccion", "desenlace", "sensor_ok"])
            for f in nodos:
                w.writerow([f["recibida"], f["arranque_id"], f["ronda"], f["direccion"],
                            f["desenlace"], "" if f["sensor_ok"] is None else f["sensor_ok"]])
        print(f"\nExportado: {args.csv} ({len(nodos)} filas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
