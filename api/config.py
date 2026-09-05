"""Configuracion de la aplicacion, leida del entorno.

Nada de credenciales ni rutas hardcodeadas: todo entra por variables de
entorno, que en desarrollo se cargan desde un archivo .env (ver .env.example)
y en produccion las inyecta systemd via EnvironmentFile.
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


def _bool(nombre: str, por_defecto: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().lower() in {"1", "true", "yes", "si", "on"}


# --- Base de datos ---
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "sensores.db"))

# --- Broker MQTT (lo usa el servicio de ingesta) ---
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "estaciones/+/data")
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

# --- API ---
# Token para las operaciones de escritura (alta/edicion/baja de estaciones).
# Si no esta definido, las escrituras quedan DESHABILITADAS. Es deliberado:
# el modo inseguro tiene que ser una decision explicita, no el default.
API_TOKEN = os.environ.get("API_TOKEN", "").strip()

DEBUG = _bool("FLASK_DEBUG", False)

# Desfase del huso local respecto de UTC, en horas (Paraguay: -3, sin horario
# de verano desde 2024). Se usa para traducir las fechas del calendario que
# elige el usuario en el navegador a los timestamps UTC que guarda el gateway.
# Es explicito y no se toma del reloj del servidor a proposito: asi la consulta
# da el mismo resultado corra la VM en UTC o en cualquier otra zona.
TZ_OFFSET_HORAS = float(os.environ.get("TZ_OFFSET_HORAS", "-3"))


def origenes_cors():
    """Lista de origenes permitidos para CORS.

    Se toma de CORS_ORIGINS (separada por comas). Cada entrada puede ser un
    origen literal o, si empieza con 're:', una expresion regular — util para
    las URLs de preview de Cloudflare, que cambian en cada deploy.
    """
    crudo = os.environ.get("CORS_ORIGINS", "").strip()
    if not crudo:
        # Solo desarrollo local. En produccion CORS_ORIGINS es obligatorio.
        return ["http://localhost:5173", "http://127.0.0.1:5173"]

    origenes = []
    for entrada in crudo.split(","):
        entrada = entrada.strip()
        if not entrada:
            continue
        if entrada.startswith("re:"):
            origenes.append(re.compile(entrada[3:]))
        else:
            origenes.append(entrada)
    return origenes
