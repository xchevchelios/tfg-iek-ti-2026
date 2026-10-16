"""Acceso a SQLite: conexiones, esquema y migraciones idempotentes."""
import re
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
                id             TEXT PRIMARY KEY,
                nombre         TEXT NOT NULL,
                latitud        REAL,
                longitud       REAL,
                activa         INTEGER NOT NULL DEFAULT 1,
                direccion_lora INTEGER,
                creada_en      TEXT NOT NULL
            )
            """
        )

        # La direccion LoRa es el numero que viaja en la trama binaria y lo
        # unico que el gateway entiende. Antes estaba implicita en el nombre
        # ('estacion_01' -> 1), un contrato que nada validaba y que se rompia
        # con cualquier identificador fuera de la convencion. Ahora es un campo
        # propio, y el identificador se deriva de el.
        try:
            conn.execute("ALTER TABLE estaciones ADD COLUMN direccion_lora INTEGER")
        except sqlite3.OperationalError:
            pass  # la columna ya existia

        # UNIQUE no se puede agregar con ALTER TABLE, pero un indice unico da
        # la misma garantia. Es parcial para que varias filas sin direccion
        # asignada no choquen entre si.
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_estaciones_lora
            ON estaciones (direccion_lora) WHERE direccion_lora IS NOT NULL
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

        # --- Autenticacion y autorizacion ---

        # El correo se compara sin distinguir mayusculas: Google puede devolver
        # el mismo buzon escrito distinto y serian dos usuarios para la base.
        # `sub_google` es el identificador estable que asigna Google; se completa
        # en el primer login y a partir de ahi es la clave de busqueda, porque
        # una persona puede cambiar de direccion de correo.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nombre        TEXT NOT NULL,
                rol           TEXT NOT NULL CHECK (rol IN ('admin', 'operador')),
                activo        INTEGER NOT NULL DEFAULT 1,
                sub_google    TEXT UNIQUE,
                creado_en     TEXT NOT NULL,
                ultimo_acceso TEXT
            )
            """
        )

        # De la sesion se guarda el HASH del token, nunca el token.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sesiones (
                id               TEXT PRIMARY KEY,
                usuario_id       INTEGER NOT NULL REFERENCES usuarios(id),
                csrf             TEXT NOT NULL,
                creada_en        TEXT NOT NULL,
                expira_en        TEXT NOT NULL,
                ultima_actividad TEXT NOT NULL,
                ip               TEXT,
                agente           TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones (usuario_id)"
        )

        # El correo se duplica a proposito: la bitacora tiene que seguir siendo
        # legible aunque el usuario se elimine de la tabla.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bitacora (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                momento    TEXT NOT NULL,
                usuario_id INTEGER,
                email      TEXT,
                accion     TEXT NOT NULL,
                entidad    TEXT,
                detalle    TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bitacora_momento ON bitacora (momento)"
        )

        conn.commit()


def migrar_direcciones_lora():
    """Completa la direccion LoRa de las estaciones que ya existian.

    Parsea el numero del identificador ('estacion_07' -> 7). Parsear esta bien
    como paso de migracion, una sola vez; mal como comportamiento permanente,
    que es lo que hacia el sistema antes.
    """
    asignadas = []
    with get_connection() as conn:
        filas = conn.execute(
            "SELECT id FROM estaciones WHERE direccion_lora IS NULL"
        ).fetchall()

        for fila in filas:
            coincidencia = re.fullmatch(r"estacion_(\d{1,3})", fila["id"])
            if not coincidencia:
                continue
            direccion = int(coincidencia.group(1))
            if not 1 <= direccion <= 254:
                continue
            try:
                conn.execute(
                    "UPDATE estaciones SET direccion_lora = ? WHERE id = ?",
                    (direccion, fila["id"]),
                )
                asignadas.append((fila["id"], direccion))
            except sqlite3.IntegrityError:
                # Otra estacion ya tiene esa direccion: queda sin asignar para
                # que una persona lo resuelva desde el panel.
                pass
        conn.commit()
    return asignadas


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
    migradas = migrar_direcciones_lora()
    nuevas = sembrar_estaciones_existentes()
    print(f"Esquema listo en {DB_PATH}")
    for id_estacion, direccion in migradas:
        print(f"  direccion LoRa asignada: {id_estacion} -> {direccion}")
    if nuevas:
        print("Estaciones dadas de alta desde las mediciones existentes:")
        for station_id in nuevas:
            print(f"  - {station_id}")
    else:
        print("Sin estaciones nuevas para sembrar.")
