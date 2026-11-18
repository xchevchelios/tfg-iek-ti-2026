"""Calibracion desde la consola (mismo calculo que la pestania del panel).

Ejemplos, desde api/ en la VM:

  # Solo evaluar y ver la tabla crudo / lineal / con humedad
  python3 calibrar.py --estacion estacion_01 --metrica pm25 --csv referencia.csv

  # Exportar los pares (para graficos del libro) y guardar el modelo con HR
  python3 calibrar.py --estacion estacion_01 --metrica pm25 --csv referencia.csv \\
      --pares pares_e1_pm25.csv --guardar humedad --notas "HAL-HPC601, Bloque H"

  # Listar y anular
  python3 calibrar.py --listar
  python3 calibrar.py --anular 3

Lo que se guarda desde aca queda en la bitacora como hecho por 'consola'.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone

import config
from calibracion import (
    METRICAS_CALIBRABLES,
    MODELOS,
    ErrorCalibracion,
    calibrar_desde_csv,
    evaluar_coef,
    guardar,
)
from db import get_connection, init_db


def _num(v, d=2):
    return "—" if v is None else f"{v:.{d}f}"


def _formula(c):
    def termo(v, sufijo):
        return f" {'-' if v < 0 else '+'} {abs(v):.3f}·{sufijo}"
    texto = f"{c['a']:.2f}" + termo(c["b"], "x")
    return texto + (termo(c["c"], "HR") if c["c"] else "")


def imprimir(resultado):
    r, ref = resultado["resumen"], resultado["referencia"]
    filtro = f" · HR max {resultado['hr_max']}" if resultado["hr_max"] else ""
    print(f"\nEstacion {resultado['estacion']} · {resultado['metrica']} · "
          f"ventana {resultado['ventana_min']} min{filtro}")
    print(f"Referencia: {ref['muestras']} muestras, {ref['desde']} a {ref['hasta']}")
    print(f"Nodo: {r['mediciones_sensor']} mediciones. Ventanas en comun: "
          f"{r['ventanas_comunes']}, descartadas por HR: {r['descartadas_humedad']}, "
          f"pares: {r['pares']}\n")

    filas = []
    tipo = None
    for modelo in resultado["modelos"]:
        m = resultado["modelos"][modelo]
        if "error" in m:
            filas.append((modelo, m["error"], None))
            continue
        tipo = m["validacion"]
        if not filas:
            filas.append(("sin calibrar", "x", m["validacion"]["crudo"]))
        filas.append((modelo, _formula(m["coef"]), m["validacion"]["calibrado"]))

    print(f"{'modelo':<14}{'formula':<34}{'R²':>7}{'RMSE':>8}{'MAE':>8}{'sesgo':>8}")
    for nombre, formula, m in filas:
        if m is None:
            print(f"{nombre:<14}{formula}")
            continue
        print(f"{nombre:<14}{formula:<34}{_num(m['r2'], 3):>7}{_num(m['rmse']):>8}"
              f"{_num(m['mae']):>8}{_num(m['sesgo']):>8}")
    if tipo:
        if tipo["tipo"] == "temporal":
            print(f"\nValidacion temporal: metricas sobre las ultimas {tipo['n']} ventanas "
                  "(no usadas para ajustar).")
        else:
            print("\nATENCION: pocas ventanas, metricas sobre los mismos datos del ajuste "
                  "(optimistas).")


def exportar_pares(resultado, ruta):
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        cabecera = ["t_utc", "sds", "hr", "ref"]
        modelos = [m for m, v in resultado["modelos"].items() if "error" not in v]
        escritor.writerow(cabecera + [f"cal_{m}" for m in modelos])  # sds = valor del nodo
        for p in resultado["pares"]:
            calibrados = []
            for m in modelos:
                c = resultado["modelos"][m]["coef"]
                calibrados.append(round(evaluar_coef(c["a"], c["b"], c["c"], p["sds"], p["hr"],
                                                     resultado["metrica"]), 3))
            escritor.writerow([p["t"], p["sds"], p["hr"], p["ref"], *calibrados])
    print(f"\nPares exportados a {ruta}")


def listar(conn):
    filas = conn.execute(
        """SELECT id, station_id, metrica, modelo, a, b, c, valida_desde, n_pares,
                  rmse_crudo, rmse, r2, anulada FROM calibraciones
           ORDER BY station_id, metrica, valida_desde, id"""
    ).fetchall()
    if not filas:
        print("Sin calibraciones.")
    for f in filas:
        estado = "ANULADA" if f["anulada"] else "vigente"
        print(f"#{f['id']:<4}{f['station_id']:<14}{f['metrica']:<6}{f['modelo']:<9}"
              f"{_formula(dict(f)):<34} desde {f['valida_desde']}  n={f['n_pares']}  "
              f"RMSE {_num(f['rmse_crudo'])}→{_num(f['rmse'])}  {estado}")


def bitacora(conn, accion, entidad, detalle):
    conn.execute(
        "INSERT INTO bitacora (momento, email, accion, entidad, detalle) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), "consola", accion, entidad, detalle),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--estacion")
    ap.add_argument("--metrica", choices=METRICAS_CALIBRABLES)
    ap.add_argument("--csv", help="CSV del equipo de referencia "
                    "(timestamp y alguna de: pm25, pm10, temperatura, humedad)")
    ap.add_argument("--ventana", type=int, default=60, help="minutos (por defecto 60)")
    ap.add_argument("--hr-max", type=float, default=80.0,
                    help="solo PM: descarta ventanas con HR mayor (por defecto 80; 0 = sin filtro)")
    ap.add_argument("--utc", action="store_true",
                    help="las fechas del CSV sin zona estan en UTC (por defecto, hora local)")
    ap.add_argument("--pares", help="exporta los pares a este CSV")
    ap.add_argument("--guardar", choices=MODELOS,
                    help="guarda este modelo ('humedad' solo existe para PM)")
    ap.add_argument("--desde", help="vigente desde, hora local (por defecto, todo el historial)")
    ap.add_argument("--notas")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--anular", type=int, metavar="ID")
    args = ap.parse_args()

    init_db()
    with get_connection() as conn:
        if args.listar:
            listar(conn)
            return
        if args.anular:
            cambio = conn.execute(
                "UPDATE calibraciones SET anulada = 1, anulada_en = ? WHERE id = ? AND anulada = 0",
                (datetime.now(timezone.utc).isoformat(), args.anular),
            )
            if not cambio.rowcount:
                sys.exit(f"No hay una calibracion vigente con id {args.anular}.")
            bitacora(conn, "calibracion_anulada", None, f"#{args.anular}")
            conn.commit()
            print(f"Calibracion #{args.anular} anulada.")
            return

        if not (args.estacion and args.metrica and args.csv):
            ap.error("hacen falta --estacion, --metrica y --csv")
        with open(args.csv, encoding="utf-8-sig") as archivo:
            texto = archivo.read()

        try:
            resultado = calibrar_desde_csv(
                conn, args.estacion, args.metrica, texto,
                ventana_min=args.ventana,
                hr_max=args.hr_max or None,
                offset_horas=0.0 if args.utc else config.TZ_OFFSET_HORAS,
            )
        except ErrorCalibracion as error:
            sys.exit(f"Error: {error}")

        imprimir(resultado)
        if args.pares:
            exportar_pares(resultado, args.pares)

        if args.guardar:
            try:
                cal_id = guardar(conn, resultado, args.guardar,
                                 valida_desde=args.desde, notas=args.notas,
                                 offset_horas=config.TZ_OFFSET_HORAS)
            except ErrorCalibracion as error:
                sys.exit(f"Error: {error}")
            c = resultado["modelos"][args.guardar]["coef"]
            bitacora(conn, "calibracion_alta", args.estacion,
                     f"#{cal_id} {args.metrica} {args.guardar}: a={c['a']} b={c['b']} c={c['c']}")
            conn.commit()
            print(f"\nGuardada como calibracion #{cal_id}.")


if __name__ == "__main__":
    main()
