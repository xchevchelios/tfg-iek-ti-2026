"""Calibracion de los sensores contra un equipo de referencia.

Idea general
------------
El SDS011 tiene un sesgo propio de cada unidad y sobreestima con humedad alta
(las particulas absorben agua y el laser las ve mas grandes). El DHT21 tambien
tiene un desvio propio en temperatura y en humedad. Con mediciones de un equipo
calibrado tomadas junto a un nodo se ajusta, por estacion y metrica:

    PM2.5 / PM10:          lineal:   ref = a + b * x
                           humedad:  ref = a + b * x + c * HR
    temperatura / humedad: lineal:   ref = a + b * x

En el modelo de PM con humedad, HR es la humedad CRUDA del DHT21 del mismo
nodo: con esa se ajusto, asi que con esa se aplica, aunque la humedad tenga su
propia calibracion. Para temperatura y humedad no se descartan ventanas por
humedad alta (justamente interesa calibrar todo el rango).

Reglas que ordenan el diseno:

* El dato crudo NUNCA se toca. `mediciones` guarda lo que midio el sensor; la
  calibracion se aplica al leer. Recalibrar corrige todo el historico y se puede
  deshacer anulando la calibracion.
* Versionado por fecha. Cada medicion usa la calibracion vigente en su
  timestamp (la ultima no anulada con `valida_desde <= timestamp`). Si el
  sensor deriva y se recalibra en marzo, enero sigue con la de enero.
* Los coeficientes los calcula el servidor a partir de los datos, nunca se
  cargan a mano: la fila guarda los pares usados y las metricas de validacion,
  asi que cada calibracion es reproducible.

Solo biblioteca estandar: corre en la e2-micro sin instalar numpy.
"""
import csv
import io
import json
import math
import re
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

METRICAS_PM = ("pm25", "pm10")

# Correccion por humedad alta del metodo publicado por la red FIUNA (Recalde,
# Bernal, Rincon y Stalder, Data in Brief 2026; notebook CorrectionCalculation):
#     PM_corr = PM / exp(ALFA * (HR - HR_REF))   solo si HR > UMBRAL
# No se ajusta con datos: es un modelo fijo. En la tabla se guarda con
# a = ALFA, b = HR_REF, c = UMBRAL. La HR que usa es la humedad ya calibrada
# de la estacion (la HR real), no la cruda del DHT21.
MODELO_HUMEDAD_EXP = "humedad_exp"
FIUNA_ALFA, FIUNA_HR_REF, FIUNA_UMBRAL = 0.014, 40.0, 80.0

# Con HR calibrada en o por encima de este valor la lectura se marca como de
# humedad alta (el SDS011 es menos confiable): el dashboard lo indica.
HR_ALTA = 80.0
METRICAS_CALIBRABLES = ("pm25", "pm10", "temperatura", "humedad")
MODELOS = ("lineal", "humedad")

# Rango fisico de cada magnitud: el valor calibrado se recorta a el. Una
# ordenada negativa no puede dar concentraciones negativas, ni una pendiente
# llevar la humedad relativa por encima del 100 %. La temperatura no se recorta.
LIMITES = {
    "pm25": (0.0, None),
    "pm10": (0.0, None),
    "temperatura": (None, None),
    "humedad": (0.0, 100.0),
}


def modelos_de(metrica):
    """El modelo con HR solo tiene sentido para PM (es la correccion por
    higroscopicidad de las particulas)."""
    return MODELOS if metrica in METRICAS_PM else ("lineal",)

# 'valida_desde' de una calibracion que se aplica a todo el historial.
DESDE_SIEMPRE = "1970-01-01T00:00:00Z"

FORMATO_TS = "%Y-%m-%dT%H:%M:%SZ"

# Por debajo de esto no se separa un tramo de validacion: con tan pocas
# ventanas la validacion seria ruido. Se informa como "en_muestra".
MIN_PARES_VALIDACION = 24
MIN_PARES = {"lineal": 3, "humedad": 4}

# Criterio de completitud del 75 %, el mismo que se usa para el periodo.
COMPLETITUD = 0.75


class ErrorCalibracion(ValueError):
    """Error de datos de entrada: se muestra tal cual a quien calibra."""


# =================================================================
# Esquema
# =================================================================

def crear_tabla(conn):
    # Las versiones anteriores de la tabla tenian un CHECK mas chico (solo
    # pm25/pm10, o sin el modelo humedad_exp) y SQLite no permite modificar un
    # CHECK: se reconstruye copiando las filas.
    actual = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'calibraciones'"
    ).fetchone()
    if actual and MODELO_HUMEDAD_EXP not in actual[0]:
        conn.execute("ALTER TABLE calibraciones RENAME TO calibraciones_v1")
        _crear_tabla_actual(conn)
        conn.execute("INSERT INTO calibraciones SELECT * FROM calibraciones_v1")
        conn.execute("DROP TABLE calibraciones_v1")
    else:
        _crear_tabla_actual(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calibraciones_estacion
        ON calibraciones (station_id, metrica, valida_desde)
        """
    )


def _crear_tabla_actual(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calibraciones (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id    TEXT NOT NULL REFERENCES estaciones(id),
            metrica       TEXT NOT NULL CHECK (metrica IN
                              ('pm25', 'pm10', 'temperatura', 'humedad')),
            modelo        TEXT NOT NULL CHECK (modelo IN
                              ('lineal', 'humedad', 'humedad_exp')),
            a             REAL NOT NULL,
            b             REAL NOT NULL,
            c             REAL NOT NULL DEFAULT 0,
            valida_desde  TEXT NOT NULL,
            ventana_min   INTEGER NOT NULL,
            hr_max        REAL,
            n_pares       INTEGER NOT NULL,
            validacion    TEXT NOT NULL CHECK (validacion IN ('temporal', 'en_muestra')),
            n_validacion  INTEGER NOT NULL,
            r2_crudo      REAL,
            rmse_crudo    REAL,
            r2            REAL,
            rmse          REAL,
            mae           REAL,
            sesgo         REAL,
            pares_json    TEXT NOT NULL,
            notas         TEXT,
            creada_en     TEXT NOT NULL,
            usuario_id    INTEGER,
            email         TEXT,
            anulada       INTEGER NOT NULL DEFAULT 0,
            anulada_en    TEXT
        )
        """
    )


# =================================================================
# Fechas
# =================================================================

def parsear_ts(texto, offset_horas=0.0):
    """Texto -> datetime en UTC.

    Acepta ISO con o sin zona ('2026-11-12T00:27:00Z', '2026-11-12 09:00') y
    dd/mm/aaaa hh:mm[:ss]. Sin zona se interpreta como hora local con el
    desfase dado (Paraguay: -3). No se usa fromisoformat con 'Z' directo
    porque Python < 3.11 no lo entiende.
    """
    texto = (texto or "").strip()
    if not texto:
        raise ErrorCalibracion("Fecha vacia.")
    momento = None
    iso = texto.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        momento = datetime.fromisoformat(iso)
    except ValueError:
        for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                momento = datetime.strptime(texto, formato)
                break
            except ValueError:
                continue
    if momento is None:
        raise ErrorCalibracion(f"Fecha no reconocida: {texto!r}.")
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone(timedelta(hours=offset_horas)))
    return momento.astimezone(timezone.utc)


def texto_utc(momento):
    return momento.astimezone(timezone.utc).strftime(FORMATO_TS)


# =================================================================
# Regresion y metricas
# =================================================================

def _resolver(matriz, vector):
    """Eliminacion gaussiana con pivoteo parcial (sistemas de 2x2 o 3x3)."""
    n = len(vector)
    m = [fila[:] + [vector[i]] for i, fila in enumerate(matriz)]
    for col in range(n):
        pivote = max(range(col, n), key=lambda f: abs(m[f][col]))
        if abs(m[pivote][col]) < 1e-12:
            raise ErrorCalibracion(
                "Los datos no alcanzan para ajustar el modelo (variables sin "
                "variacion o colineales). Hace falta un rango mas amplio."
            )
        m[col], m[pivote] = m[pivote], m[col]
        for f in range(col + 1, n):
            factor = m[f][col] / m[col][col]
            for k in range(col, n + 1):
                m[f][k] -= factor * m[col][k]
    solucion = [0.0] * n
    for f in range(n - 1, -1, -1):
        suma = m[f][n] - sum(m[f][k] * solucion[k] for k in range(f + 1, n))
        solucion[f] = suma / m[f][f]
    return solucion


def ajustar(pares, modelo):
    """Minimos cuadrados. `pares`: dicts con sds, hr, ref. Devuelve (a, b, c)."""
    if len(pares) < MIN_PARES[modelo]:
        raise ErrorCalibracion(
            f"El modelo '{modelo}' necesita al menos {MIN_PARES[modelo]} ventanas "
            f"validas y hay {len(pares)}."
        )
    filas = [
        [1.0, p["sds"]] + ([p["hr"]] if modelo == "humedad" else [])
        for p in pares
    ]
    y = [p["ref"] for p in pares]
    k = len(filas[0])
    xtx = [[sum(f[i] * f[j] for f in filas) for j in range(k)] for i in range(k)]
    xty = [sum(f[i] * yi for f, yi in zip(filas, y)) for i in range(k)]
    coef = _resolver(xtx, xty)
    return coef[0], coef[1], coef[2] if modelo == "humedad" else 0.0


def evaluar_coef(a, b, c, x, hr, metrica="pm25"):
    """Valor calibrado, recortado al rango fisico de la magnitud."""
    valor = a + b * x + c * (hr or 0.0)
    minimo, maximo = LIMITES[metrica]
    if minimo is not None:
        valor = max(minimo, valor)
    if maximo is not None:
        valor = min(maximo, valor)
    return valor


def metricas(pred, ref):
    """r2: cuadrado del coeficiente de Pearson, la definicion que usan las
    metas de desempeno de la EPA para sensores de bajo costo. Una calibracion
    lineal simple NO cambia r2 (es una transformacion afin): corrige el sesgo y
    la escala, que se ven en RMSE, MAE y sesgo."""
    n = len(ref)
    if n == 0:
        return None
    errores = [p - r for p, r in zip(pred, ref)]
    rmse = math.sqrt(sum(e * e for e in errores) / n)
    mae = sum(abs(e) for e in errores) / n
    sesgo = sum(errores) / n
    r2 = None
    if n >= 3:
        mp, mr = sum(pred) / n, sum(ref) / n
        sp = math.sqrt(sum((p - mp) ** 2 for p in pred))
        sr = math.sqrt(sum((r - mr) ** 2 for r in ref))
        if sp > 0 and sr > 0:
            r = sum((p - mp) * (q - mr) for p, q in zip(pred, ref)) / (sp * sr)
            r2 = r * r
    return {
        "n": n,
        "r2": None if r2 is None else round(r2, 4),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "sesgo": round(sesgo, 3),
    }


# =================================================================
# Referencia y alineacion
# =================================================================

_ALIAS = {
    "timestamp": "timestamp", "fecha": "timestamp", "fecha_hora": "timestamp",
    "fechahora": "timestamp", "hora": "timestamp", "datetime": "timestamp",
    "time": "timestamp",
    "pm25": "pm25", "pm2.5": "pm25", "pm2_5": "pm25", "pm 2.5": "pm25",
    "pm10": "pm10", "pm 10": "pm10",
    "temperatura": "temperatura", "temp": "temperatura", "temperature": "temperatura",
    "t": "temperatura", "temp_c": "temperatura",
    "humedad": "humedad", "hr": "humedad", "rh": "humedad", "humidity": "humedad",
    "hum": "humedad", "humedad_relativa": "humedad",
}


def _canonica(columna):
    """'PM2.5 (µg/m³)' -> 'pm25', 'Temperatura [°C]' -> 'temperatura'."""
    limpio = re.sub(r"\(.*?\)|\[.*?\]", "", columna or "").strip().lower()
    return _ALIAS.get(limpio)


def _numero(texto, admite_negativos=False):
    texto = (texto or "").strip()
    if not texto:
        return None
    # Planillas en espanol: coma decimal.
    if "," in texto and "." not in texto:
        texto = texto.replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        return None
    if not math.isfinite(valor) or (valor < 0 and not admite_negativos):
        return None
    return valor


def leer_referencia(texto_csv, offset_horas):
    """CSV del equipo de referencia -> lista de (datetime UTC, {pm25, pm10}).

    Columnas reconocidas: timestamp/fecha, pm25 (o pm2.5) y pm10 en µg/m³,
    temperatura (o temp) en °C y humedad (o hr, rh) en %. Las unidades entre
    parentesis o corchetes en la cabecera se ignoran. Separador coma o punto y
    coma; coma decimal admitida.
    """
    texto_csv = (texto_csv or "").lstrip("﻿")
    if not texto_csv.strip():
        raise ErrorCalibracion("El archivo de referencia esta vacio.")
    try:
        dialecto = csv.Sniffer().sniff(texto_csv[:2048], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    lector = csv.DictReader(io.StringIO(texto_csv), dialect=dialecto)
    columnas = {c: _canonica(c) for c in (lector.fieldnames or [])}
    if "timestamp" not in columnas.values():
        raise ErrorCalibracion(
            "El CSV necesita una columna 'timestamp' (o 'fecha'). Columnas "
            f"encontradas: {', '.join(lector.fieldnames or [])}."
        )
    if not set(METRICAS_CALIBRABLES) & set(columnas.values()):
        raise ErrorCalibracion(
            "El CSV necesita al menos una columna de datos: pm25, pm10, "
            "temperatura o humedad."
        )

    muestras = []
    for numero_fila, fila in enumerate(lector, start=2):
        valores = {}
        momento = None
        for original, canonica in columnas.items():
            if canonica == "timestamp":
                try:
                    momento = parsear_ts(fila.get(original), offset_horas)
                except ErrorCalibracion as error:
                    raise ErrorCalibracion(f"Fila {numero_fila}: {error}") from None
            elif canonica in METRICAS_CALIBRABLES:
                valores[canonica] = _numero(
                    fila.get(original), admite_negativos=canonica == "temperatura"
                )
        muestras.append((momento, valores))
    if not muestras:
        raise ErrorCalibracion("El CSV no tiene filas de datos.")
    muestras.sort(key=lambda m: m[0])
    return muestras


def _periodo_mediano(instantes):
    """Intervalo tipico entre muestras, en segundos."""
    difs = sorted(
        (b - a).total_seconds() for a, b in zip(instantes, instantes[1:])
        if (b - a).total_seconds() > 0
    )
    return difs[len(difs) // 2] if difs else None


def _promediar(muestras, ventana_s):
    """[(datetime, valor)] -> {inicio_ventana: promedio} con completitud del 75 %.

    La cantidad esperada por ventana sale del periodo tipico de la propia
    serie, asi que sirve igual para el SDS011 (60 o 300 s) que para un equipo
    de referencia que registre cada minuto o cada hora.
    """
    muestras = [(t, v) for t, v in muestras if v is not None]
    if not muestras:
        return {}
    periodo = _periodo_mediano([t for t, _ in muestras]) or ventana_s
    esperadas = max(1, round(ventana_s / periodo))
    minimo = max(1, math.ceil(COMPLETITUD * esperadas))

    grupos = {}
    for t, v in muestras:
        inicio = int(t.timestamp() // ventana_s * ventana_s)
        grupos.setdefault(inicio, []).append(v)
    return {
        inicio: sum(vs) / len(vs)
        for inicio, vs in grupos.items()
        if len(vs) >= minimo
    }


def armar_pares(conn, station_id, metrica, referencia, ventana_min=60, hr_max=80.0):
    """Cruza la referencia con las mediciones de la estacion, por ventana.

    Devuelve (pares, resumen). Cada par: {t, sds, hr, ref}; 'sds' es el valor
    del nodo para la metrica elegida (el nombre viene del SDS011) y 'hr' la
    humedad cruda del nodo.
    """
    if metrica not in METRICAS_CALIBRABLES:
        raise ErrorCalibracion(
            f"Metrica no calibrable. Opciones: {', '.join(METRICAS_CALIBRABLES)}."
        )
    if metrica not in METRICAS_PM:
        hr_max = None  # el filtro por humedad es solo para PM
    ventana_s = int(ventana_min) * 60
    if ventana_s <= 0:
        raise ErrorCalibracion("La ventana tiene que ser positiva.")

    ref_serie = [(t, v.get(metrica)) for t, v in referencia]
    if all(v is None for _, v in ref_serie):
        raise ErrorCalibracion(f"La referencia no tiene valores de {metrica}.")

    desde = referencia[0][0] - timedelta(seconds=ventana_s)
    hasta = referencia[-1][0] + timedelta(seconds=ventana_s)
    filas = conn.execute(
        f"""
        SELECT timestamp, {metrica} AS valor, humedad
        FROM mediciones
        WHERE station_id = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (station_id, texto_utc(desde), texto_utc(hasta)),
    ).fetchall()
    sensor = []
    for f in filas:
        try:
            sensor.append((parsear_ts(f["timestamp"]), f["valor"], f["humedad"]))
        except ErrorCalibracion:
            continue

    ref_v = _promediar(ref_serie, ventana_s)
    sds_v = _promediar([(t, v) for t, v, _ in sensor], ventana_s)
    hr_v = _promediar([(t, h) for t, _, h in sensor], ventana_s)

    comunes = sorted(set(ref_v) & set(sds_v) & set(hr_v))
    pares, por_humedad = [], 0
    for inicio in comunes:
        if hr_max is not None and hr_v[inicio] > hr_max:
            por_humedad += 1
            continue
        pares.append({
            "t": texto_utc(datetime.fromtimestamp(inicio, tz=timezone.utc)),
            "sds": round(sds_v[inicio], 3),
            "hr": round(hr_v[inicio], 2),
            "ref": round(ref_v[inicio], 3),
        })

    resumen = {
        "mediciones_sensor": len(sensor),
        "ventanas_referencia": len(ref_v),
        "ventanas_sensor": len(sds_v),
        "ventanas_comunes": len(comunes),
        "descartadas_humedad": por_humedad,
        "pares": len(pares),
    }
    return pares, resumen


# =================================================================
# Evaluacion de los modelos
# =================================================================

def evaluar_modelo(pares, modelo, metrica="pm25"):
    """Coeficientes finales (ajustados con todos los pares) y metricas de
    validacion.

    La validacion separa por tiempo, no al azar: las primeras 2/3 ajustan y el
    ultimo 1/3 valida. Al azar, ventanas vecinas (casi iguales) quedarian a
    ambos lados y la validacion seria optimista.
    """
    a, b, c = ajustar(pares, modelo)

    if len(pares) >= MIN_PARES_VALIDACION:
        corte = len(pares) * 2 // 3
        tramo_ajuste, tramo_val = pares[:corte], pares[corte:]
        av, bv, cv = ajustar(tramo_ajuste, modelo)
        tipo = "temporal"
    else:
        tramo_val = pares
        av, bv, cv = a, b, c
        tipo = "en_muestra"

    ref = [p["ref"] for p in tramo_val]
    crudo = [p["sds"] for p in tramo_val]
    calibrado = [evaluar_coef(av, bv, cv, p["sds"], p["hr"], metrica) for p in tramo_val]

    return {
        "modelo": modelo,
        "coef": {"a": round(a, 5), "b": round(b, 5), "c": round(c, 5)},
        "validacion": {
            "tipo": tipo,
            "n": len(tramo_val),
            "crudo": metricas(crudo, ref),
            "calibrado": metricas(calibrado, ref),
        },
    }


def evaluar(pares, metrica="pm25"):
    """Evalua los dos modelos; uno que no se puede ajustar informa el motivo."""
    if not pares:
        raise ErrorCalibracion(
            "No hay ventanas en comun entre la referencia y la estacion. "
            "Revisar la estacion elegida, el rango de fechas y la zona horaria."
        )
    resultado = {}
    for modelo in modelos_de(metrica):
        try:
            resultado[modelo] = evaluar_modelo(pares, modelo, metrica)
        except ErrorCalibracion as error:
            resultado[modelo] = {"modelo": modelo, "error": str(error)}
    return resultado


def calibrar_desde_csv(conn, station_id, metrica, texto_csv, *, ventana_min=60,
                       hr_max=80.0, offset_horas=-3.0):
    referencia = leer_referencia(texto_csv, offset_horas)
    if metrica not in METRICAS_PM:
        hr_max = None
    pares, resumen = armar_pares(conn, station_id, metrica, referencia,
                                 ventana_min, hr_max)
    return {
        "estacion": station_id,
        "metrica": metrica,
        "ventana_min": int(ventana_min),
        "hr_max": hr_max,
        "referencia": {
            "muestras": len(referencia),
            "desde": texto_utc(referencia[0][0]),
            "hasta": texto_utc(referencia[-1][0]),
        },
        "resumen": resumen,
        "modelos": evaluar(pares, metrica),
        "pares": pares,
    }


def guardar(conn, resultado, modelo, *, valida_desde=None, notas=None, usuario=None,
            offset_horas=0.0):
    """Persiste una calibracion evaluada. Devuelve el id.

    `valida_desde` sin zona se interpreta con `offset_horas`.
    """
    evaluado = resultado["modelos"].get(modelo)
    if not evaluado or "error" in evaluado:
        raise ErrorCalibracion(
            (evaluado or {}).get("error") or f"Modelo '{modelo}' no valido."
        )
    desde = DESDE_SIEMPRE if not valida_desde else texto_utc(parsear_ts(valida_desde, offset_horas))
    val = evaluado["validacion"]
    cursor = conn.execute(
        """
        INSERT INTO calibraciones (
            station_id, metrica, modelo, a, b, c, valida_desde, ventana_min,
            hr_max, n_pares, validacion, n_validacion, r2_crudo, rmse_crudo,
            r2, rmse, mae, sesgo, pares_json, notas, creada_en, usuario_id, email
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resultado["estacion"], resultado["metrica"], modelo,
            evaluado["coef"]["a"], evaluado["coef"]["b"], evaluado["coef"]["c"],
            desde, resultado["ventana_min"], resultado["hr_max"],
            len(resultado["pares"]), val["tipo"], val["n"],
            val["crudo"]["r2"], val["crudo"]["rmse"],
            val["calibrado"]["r2"], val["calibrado"]["rmse"],
            val["calibrado"]["mae"], val["calibrado"]["sesgo"],
            json.dumps(resultado["pares"]), notas,
            datetime.now(timezone.utc).isoformat(),
            usuario["id"] if usuario else None,
            usuario["email"] if usuario else None,
        ),
    )
    return cursor.lastrowid


def guardar_humedad_exp(conn, station_id, metrica, *, valida_desde=None, usuario=None,
                        offset_horas=0.0):
    """Da de alta la correccion FIUNA por humedad alta para una metrica de PM.
    Devuelve el id."""
    if metrica not in METRICAS_PM:
        raise ErrorCalibracion("La correccion por humedad alta es solo para PM2.5 y PM10.")
    desde = DESDE_SIEMPRE if not valida_desde else texto_utc(parsear_ts(valida_desde, offset_horas))
    cursor = conn.execute(
        """
        INSERT INTO calibraciones (
            station_id, metrica, modelo, a, b, c, valida_desde, ventana_min, hr_max,
            n_pares, validacion, n_validacion, pares_json, notas, creada_en,
            usuario_id, email
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, 'en_muestra', 0, '[]', ?, ?, ?, ?)
        """,
        (
            station_id, metrica, MODELO_HUMEDAD_EXP, FIUNA_ALFA, FIUNA_HR_REF, FIUNA_UMBRAL,
            desde,
            "Correccion por humedad alta, metodo publicado de la red FIUNA: "
            f"PM / exp({FIUNA_ALFA}·(HR − {FIUNA_HR_REF:g})) si HR > {FIUNA_UMBRAL:g} %. "
            "Con HR menor el valor no se modifica (validado contra datos 2020 del campus).",
            datetime.now(timezone.utc).isoformat(),
            usuario["id"] if usuario else None,
            usuario["email"] if usuario else None,
        ),
    )
    return cursor.lastrowid


# =================================================================
# Aplicacion al leer
# =================================================================

class Calibrador:
    """Calibraciones vigentes ya cargadas, para aplicarlas fila por fila sin
    volver a consultar la base. La busqueda por fecha es binaria."""

    def __init__(self, filas):
        self._tablas = {}
        for f in filas:  # vienen ordenadas por valida_desde, id
            clave = (f["station_id"], f["metrica"])
            lista = self._tablas.setdefault(clave, ([], []))
            lista[0].append(f["valida_desde"])
            modelo = f["modelo"] if "modelo" in f.keys() else "lineal"
            lista[1].append((f["id"], f["a"], f["b"], f["c"], modelo))

    def vigente(self, station_id, metrica, timestamp):
        tabla = self._tablas.get((station_id, metrica))
        if not tabla or not timestamp:
            return None
        # bisect_right: con dos calibraciones del mismo instante gana la mas
        # nueva (mayor id), que queda ultima en el orden.
        i = bisect_right(tabla[0], timestamp)
        return tabla[1][i - 1] if i else None

    def valor(self, station_id, metrica, timestamp, crudo, humedad):
        """(valor, id_calibracion). Sin calibracion vigente devuelve el crudo."""
        if crudo is None:
            return None, None
        cal = self.vigente(station_id, metrica, timestamp)
        if cal is None:
            return crudo, None
        cal_id, a, b, c, modelo = cal
        if modelo == MODELO_HUMEDAD_EXP:
            # Usa la HR real: la humedad de la estacion con su calibracion.
            hr, _ = self.valor(station_id, "humedad", timestamp, humedad, None)
            if hr is None:
                return crudo, None
            if hr <= c:
                return crudo, cal_id
            return round(crudo / math.exp(a * (hr - b)), 1), cal_id
        if c and humedad is None:
            # El modelo con humedad no se puede evaluar sin HR: se informa el
            # crudo sin marcarlo como calibrado, en vez de inventar un valor.
            return crudo, None
        return round(evaluar_coef(a, b, c, crudo, humedad, metrica), 1), cal_id

    def calibrar_fila(self, station_id, fila):
        """Agrega <metrica>_crudo y reemplaza cada metrica por su valor
        calibrado. `calibracion` dice que version se aplico a cada una.

        La HR que entra al modelo de PM es siempre la cruda de `fila`, nunca
        la humedad ya calibrada: es con la que se ajusto ese modelo."""
        salida = dict(fila)
        usadas = {}
        for metrica in METRICAS_CALIBRABLES:
            if metrica not in fila:
                continue
            crudo = fila[metrica]
            valor, cal_id = self.valor(
                station_id, metrica, fila.get("timestamp"), crudo, fila.get("humedad")
            )
            salida[f"{metrica}_crudo"] = crudo
            salida[metrica] = valor
            usadas[metrica] = cal_id
        salida["calibracion"] = usadas
        hr = salida.get("humedad")  # ya calibrada si hay calibracion de humedad
        salida["humedad_alta"] = hr is not None and hr >= HR_ALTA
        return salida


def cargar_calibrador(conn, station_ids=None):
    sql = """
        SELECT id, station_id, metrica, modelo, a, b, c, valida_desde
        FROM calibraciones WHERE anulada = 0
    """
    params = []
    if station_ids:
        sql += f" AND station_id IN ({','.join('?' * len(station_ids))})"
        params = list(station_ids)
    sql += " ORDER BY valida_desde ASC, id ASC"
    return Calibrador(conn.execute(sql, params).fetchall())
