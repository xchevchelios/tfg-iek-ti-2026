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

# --- Autenticacion con Google (OpenID Connect) ---
# La identidad la verifica Google; la autorizacion es nuestra: solo entran los
# correos dados de alta en la tabla `usuarios`. Sin esa lista, cualquier persona
# con una cuenta de Google podria acceder al panel.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

# Tiene que coincidir EXACTAMENTE con la URI autorizada en la consola de Google.
OAUTH_REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI", "https://api.aire-campus-una.org/api/auth/callback"
)

# A donde se devuelve al navegador una vez resuelto el login.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://aire-campus-una.org").rstrip("/")

# Duracion de la sesion, con renovacion mientras se use.
SESION_HORAS = int(os.environ.get("SESION_HORAS", "12"))

# El navegador no guarda una cookie Secure sobre http://localhost, asi que en
# desarrollo hay que poner COOKIE_SECURE=false. En produccion queda en true.
COOKIE_SECURE = _bool("COOKIE_SECURE", True)


def autenticacion_configurada():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

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
