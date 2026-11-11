"""Registro de las rondas del gateway, para estadisticas de entrega por nodo.

El gateway (firmware v2) publica al cerrar cada ronda un resumen en
gateway/estado/aplicado (retenido) con el desenlace de cada nodo:

    "ultima_ronda": {"frescas": [1], "viejas": [], "sin_respuesta": [2],
                     "sensor_fallando": [], "duracion_ms": 9400, "excedida": false}

Como el topic es retenido, en el broker solo queda la ultima ronda. Este modulo
guarda cada resumen para poder contar despues cuantas veces no respondio cada
nodo, sin tocar el firmware.

Tres detalles del firmware que este modulo resuelve:

- El numero de ronda arranca al azar en cada encendido del gateway (para que un
  nodo no confunda una medicion vieja con una ronda nueva). Por eso la ronda
  sola no identifica nada: se agrupa por arranque, que se deduce de
  `encendido_s`.
- El mismo resumen se republica al reconectarse al broker y al recibir un
  cambio de configuracion. Se descarta si ya se guardo esa ronda.
- El contador es de 16 bits y salta el 0 al dar la vuelta (65535 -> 1).
"""
from datetime import datetime, timedelta, timezone

# Cuanto pueden diferir dos estimaciones del mismo encendido. La hora de
# llegada y la deriva del cristal del ESP32 frente al reloj de la VM las
# separan unos segundos; un reinicio real las separa mucho mas.
TOLERANCIA_ARRANQUE_S = 120

# Ventana en la que un resumen repetido se considera duplicado. Con un contador
# de 16 bits, la misma ronda no vuelve a aparecer en menos de 45 dias a 60 s.
VENTANA_DUPLICADO_H = 6

DESENLACES = (
    ("frescas", "fresca"),
    ("viejas", "vieja"),
    ("sin_respuesta", "sin_respuesta"),
)

RONDA_MAX = 65535


class ResumenInvalido(ValueError):
    pass


def _iso(momento):
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _desde_iso(texto):
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _es_entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _arranque_id(conn, inicio):
    """Devuelve el id del arranque del gateway que corresponde a `inicio`.

    Compara contra la ultima estimacion de cada arranque reciente y la
    actualiza, asi la deriva del reloj del gateway no lo parte en dos con el
    paso de los dias.
    """
    for fila in conn.execute(
        "SELECT id, inicio_ultimo FROM arranques_gateway ORDER BY id DESC LIMIT 20"
    ):
        diferencia = abs((inicio - _desde_iso(fila["inicio_ultimo"])).total_seconds())
        if diferencia <= TOLERANCIA_ARRANQUE_S:
            conn.execute(
                "UPDATE arranques_gateway SET inicio_ultimo = ? WHERE id = ?",
                (_iso(inicio), fila["id"]),
            )
            return fila["id"]

    cursor = conn.execute(
        "INSERT INTO arranques_gateway (inicio, inicio_ultimo) VALUES (?, ?)",
        (_iso(inicio), _iso(inicio)),
    )
    return cursor.lastrowid


def guardar_resumen(conn, carga, ahora=None):
    """Guarda el resumen de una ronda. Devuelve el id de la ronda, o None si
    era un duplicado. No hace commit: eso queda a cargo de quien llama.

    `ahora` es la hora de llegada; el resumen no trae marca de tiempo, pero el
    gateway lo publica apenas cierra la ronda.
    """
    if not isinstance(carga, dict):
        raise ResumenInvalido("el resumen no es un objeto JSON")
    ultima = carga.get("ultima_ronda")
    ronda = carga.get("ronda")
    encendido_s = carga.get("encendido_s")
    if not isinstance(ultima, dict):
        raise ResumenInvalido("falta ultima_ronda")
    if not _es_entero(ronda) or not 1 <= ronda <= RONDA_MAX:
        raise ResumenInvalido(f"numero de ronda invalido: {ronda!r}")
    if not _es_entero(encendido_s) or encendido_s < 0:
        raise ResumenInvalido(f"encendido_s invalido: {encendido_s!r}")

    ahora = ahora or datetime.now(timezone.utc)
    arranque_id = _arranque_id(conn, ahora - timedelta(seconds=encendido_s))

    limite = _iso(ahora - timedelta(hours=VENTANA_DUPLICADO_H))
    repetida = conn.execute(
        """
        SELECT 1 FROM rondas_gateway
        WHERE arranque_id = ? AND ronda = ? AND recibida >= ?
        """,
        (arranque_id, ronda, limite),
    ).fetchone()
    if repetida:
        return None

    nodos = [d for d in carga.get("nodos") or [] if _es_entero(d)]
    cursor = conn.execute(
        """
        INSERT INTO rondas_gateway
            (arranque_id, ronda, recibida, nodos_configurados,
             duracion_ms, excedida, descartadas, firmware)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            arranque_id,
            ronda,
            _iso(ahora),
            len(nodos),
            ultima.get("duracion_ms") if _es_entero(ultima.get("duracion_ms")) else None,
            int(bool(ultima.get("excedida"))),
            carga.get("descartadas") if _es_entero(carga.get("descartadas")) else None,
            str(carga.get("firmware")) if carga.get("firmware") is not None else None,
        ),
    )
    ronda_id = cursor.lastrowid

    fallando = {d for d in ultima.get("sensor_fallando") or [] if _es_entero(d)}
    filas = []
    vistos = set()
    for clave, desenlace in DESENLACES:
        for direccion in ultima.get(clave) or []:
            if not _es_entero(direccion) or direccion in vistos:
                continue
            vistos.add(direccion)
            sensor_ok = int(direccion not in fallando) if desenlace == "fresca" else None
            filas.append((ronda_id, direccion, desenlace, sensor_ok))

    # Un nodo configurado que no figura en ninguna lista no deberia existir
    # (el gateway cierra la ronda con todos resueltos), pero si aparece se
    # registra en vez de perderlo en silencio.
    for direccion in nodos:
        if direccion not in vistos:
            filas.append((ronda_id, direccion, "sin_desenlace", None))

    conn.executemany(
        """
        INSERT INTO rondas_nodo (ronda_id, direccion, desenlace, sensor_ok)
        VALUES (?, ?, ?, ?)
        """,
        filas,
    )
    return ronda_id


def rondas_perdidas(anterior, siguiente):
    """Cuantas rondas faltan entre dos numeros consecutivos registrados.

    El contador salta el 0 al dar la vuelta, asi que despues de 65535 viene 1.
    """
    if siguiente > anterior:
        return siguiente - anterior - 1
    return (RONDA_MAX - anterior) + (siguiente - 1)
