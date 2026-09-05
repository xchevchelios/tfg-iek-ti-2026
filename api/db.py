"""Acceso a SQLite: conexiones, esquema y migraciones idempotentes."""
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

METRICAS = ("pm25", "pm10", "temperatura", "humedad")


def get_connection():
    """Conexion con filas tipo diccionario y claves foraneas activas."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Crea el esquema si falta. Es idempotente: se puede correr siempre."""
    with get_connection() as conn:
        # WAL: permite que la API lea mientras el servicio de ingesta escribe,
        # sin que se bloqueen entre si. Es persistente, basta con activarlo una
        # vez, pero no cuesta nada reafirmarlo.
        conn.execute("PRAGMA journal_mode = WAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mediciones (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT NOT NULL,
                station_id   TEXT NOT NULL,
                pm25         REAL,
                pm10         REAL,
                temperatura  REAL,
                humedad      REAL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS estaciones (
                id         TEXT PRIMARY KEY,
                nombre     TEXT NOT NULL,
                latitud    REAL,
                longitud   REAL,
                activa     INTEGER NOT NULL DEFAULT 1,
                creada_en  TEXT NOT NULL
            )
            """
        )

        # Indice compuesto: todas las consultas de la API filtran por estacion
        # y ordenan por tiempo, asi que este indice las cubre enteras. Los dos
        # indices sueltos que habia antes solo cubrian la mitad de cada una.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mediciones_station_ts
            ON mediciones (station_id, timestamp)
            """
        )
        conn.commit()


def sembrar_estaciones_existentes():
    """Da de alta en 'estaciones' los station_id que ya aparecen en mediciones.

    Sirve para migrar la base actual, donde las estaciones solo existian de
    forma implicita. Las coordenadas quedan en NULL: se cargan despues desde la
    pagina de gestion. Nunca pisa una estacion ya registrada.
    """
    ahora = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        existentes = {
            fila["station_id"]
            for fila in conn.execute("SELECT DISTINCT station_id FROM mediciones")
        }
        registradas = {fila["id"] for fila in conn.execute("SELECT id FROM estaciones")}

        nuevas = sorted(existentes - registradas)
        for station_id in nuevas:
            # 'estacion_01' -> 'Estacion 01', como nombre inicial editable
            nombre = station_id.replace("_", " ").strip().capitalize()
            conn.execute(
                """
                INSERT INTO estaciones (id, nombre, latitud, longitud, activa, creada_en)
                VALUES (?, ?, NULL, NULL, 1, ?)
                """,
                (station_id, nombre, ahora),
            )
        conn.commit()
    return nuevas


if __name__ == "__main__":
    init_db()
    nuevas = sembrar_estaciones_existentes()
    print(f"Esquema listo en {DB_PATH}")
    if nuevas:
        print("Estaciones dadas de alta desde las mediciones existentes:")
        for station_id in nuevas:
            print(f"  - {station_id}")
    else:
        print("Sin estaciones nuevas para sembrar.")
