"""Fiabilidad del enlace: cuantas veces no responde cada nodo, por version de
firmware del gateway. Lo usa la pestania "Fiabilidad" del panel.

Se apoya en las rondas que guarda la ingesta (ver rondas.py) y en las funciones
de resumen de estadisticas_rondas.py, las mismas del script de consola.

Desenlaces de un nodo en una ronda:
  fresca         llego el dato de esta ronda
  vieja          respondio, pero con una medicion anterior (no oyo MEDIR)
  sin_respuesta  no respondio
  sin_desenlace  el gateway no informo nada de ese nodo
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

import config
from estadisticas_rondas import nombres_estaciones, resumen_gateway, resumen_nodos

AGRUPACIONES = ("hora", "dia")
_VERSION = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def version(texto):
    """'2.4.0' -> (2, 4, 0). None si no se puede interpretar."""
    m = _VERSION.match(texto or "")
    if not m:
        return None
    return tuple(int(p or 0) for p in m.groups())


def _pct(parte, total):
    return round(100 * parte / total, 1) if total else None


def _periodo(recibida, agrupar):
    local = datetime.strptime(recibida, "%Y-%m-%dT%H:%M:%SZ") + timedelta(
        hours=config.TZ_OFFSET_HORAS
    )
    return local.strftime("%Y-%m-%d %H:00" if agrupar == "hora" else "%Y-%m-%d")


def por_firmware(conn):
    """Comparacion entre versiones: rondas y % sin respuesta de cada una."""
    filas = conn.execute(
        """
        SELECT g.firmware,
               COUNT(DISTINCT g.id) AS rondas,
               MIN(g.recibida) AS desde, MAX(g.recibida) AS hasta,
               COUNT(n.direccion) AS intentos,
               SUM(n.desenlace = 'sin_respuesta') AS sin_respuesta,
               SUM(n.desenlace = 'fresca') AS frescas
        FROM rondas_gateway g LEFT JOIN rondas_nodo n ON n.ronda_id = g.id
        GROUP BY g.firmware
        """
    ).fetchall()
    salida = [
        {
            "firmware": f["firmware"],
            "rondas": f["rondas"],
            "desde": f["desde"],
            "hasta": f["hasta"],
            "intentos": f["intentos"],
            "pct_sin_respuesta": _pct(f["sin_respuesta"] or 0, f["intentos"]),
            "pct_fresca": _pct(f["frescas"] or 0, f["intentos"]),
        }
        for f in filas
    ]
    return sorted(salida, key=lambda v: version(v["firmware"]) or (-1,))


def calcular(conn, firmware_min=None, agrupar="dia"):
    if agrupar not in AGRUPACIONES:
        raise ValueError("agrupar tiene que ser 'hora' o 'dia'.")
    minima = version(firmware_min) if firmware_min else None
    if firmware_min and minima is None:
        raise ValueError(f"Version de firmware no valida: {firmware_min!r}.")

    versiones = por_firmware(conn)
    incluidas = [
        v["firmware"] for v in versiones
        if minima is None or (version(v["firmware"]) or (-1,)) >= minima
    ]

    vacio = {
        "firmware_min": firmware_min,
        "incluidas": incluidas,
        "versiones": versiones,
        "gateway": None,
        "nodos": [],
        "serie": {"agrupar": agrupar, "periodos": [], "nodos": {}},
    }
    if not incluidas:
        return vacio

    marcas = ",".join("?" * len(incluidas))
    rondas = conn.execute(
        f"SELECT * FROM rondas_gateway WHERE firmware IN ({marcas}) ORDER BY recibida, id",
        incluidas,
    ).fetchall()
    nodos = conn.execute(
        f"""
        SELECT g.id AS ronda_id, g.recibida, n.direccion, n.desenlace, n.sensor_ok, n.rssi
        FROM rondas_nodo n JOIN rondas_gateway g ON g.id = n.ronda_id
        WHERE g.firmware IN ({marcas})
        ORDER BY g.recibida, g.id
        """,
        incluidas,
    ).fetchall()

    nombres = nombres_estaciones(conn)
    rssi = defaultdict(list)
    for f in nodos:
        if f["rssi"] is not None:
            rssi[f["direccion"]].append(f["rssi"])

    resumen = []
    for direccion, d in sorted(resumen_nodos(nodos).items()):
        resumen.append({
            "direccion": direccion,
            "nombre": nombres.get(direccion),
            "total": d["total"],
            "fresca": d["fresca"],
            "vieja": d["vieja"],
            "sin_respuesta": d["sin_respuesta"],
            "sin_desenlace": d["sin_desenlace"],
            "racha_max": d["racha_max"],
            "pct_sin_respuesta": _pct(d["sin_respuesta"], d["total"]),
            "pct_fresca": _pct(d["fresca"], d["total"]),
            "rssi_medio": round(sum(rssi[direccion]) / len(rssi[direccion]), 1)
            if rssi[direccion] else None,
        })

    celdas = defaultdict(lambda: [0, 0])  # (periodo, direccion) -> [total, sin_respuesta]
    for f in nodos:
        celda = celdas[(_periodo(f["recibida"], agrupar), f["direccion"])]
        celda[0] += 1
        celda[1] += f["desenlace"] == "sin_respuesta"
    periodos = sorted({p for p, _ in celdas})
    serie = {
        str(d["direccion"]): [
            _pct(celdas[(p, d["direccion"])][1], celdas[(p, d["direccion"])][0])
            if (p, d["direccion"]) in celdas else None
            for p in periodos
        ]
        for d in resumen
    }

    gw = resumen_gateway(rondas)
    esperadas = gw["registradas"] + gw["sin_reporte"]
    gw["pct_sin_reporte"] = _pct(gw["sin_reporte"], esperadas)
    gw["desde"] = rondas[0]["recibida"] if rondas else None
    gw["hasta"] = rondas[-1]["recibida"] if rondas else None

    return {
        **vacio,
        "gateway": gw,
        "nodos": resumen,
        "serie": {"agrupar": agrupar, "periodos": periodos, "nodos": serie},
    }
