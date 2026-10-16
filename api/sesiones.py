"""Gestion de sesiones de usuario.

La cookie que viaja al navegador contiene un token opaco y aleatorio: no lleva
datos ni un JWT. El estado vive en la tabla `sesiones`, y de ese token solo se
guarda su hash, de modo que alguien que lea la base no obtiene sesiones usables.

Se eligio sesion con estado en vez de JWT porque la ventaja del JWT es no
compartir estado entre varios servidores, y aca hay un solo proceso. A cambio,
el JWT cuesta la revocacion: cerrar una sesion comprometida obligaria a mantener
una lista negra, que es justamente el estado que el JWT venia a evitar. Borrar
una fila es mas simple y es inmediato.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import config
from db import get_connection

NOMBRE_COOKIE = "tfg_sesion"

# Cada cuanto se refresca `ultima_actividad`. No se escribe en cada peticion
# para no competir con el servicio de ingesta por el escritor de SQLite.
REFRESCO_MINIMO = timedelta(minutes=5)


def _ahora():
    return datetime.now(timezone.utc)


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def crear_sesion(usuario_id, ip=None, agente=None):
    """Crea una sesion y devuelve (token, csrf). El token solo existe aca y en
    la cookie: en la base queda unicamente su hash."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)

    ahora = _ahora()
    expira = ahora + timedelta(hours=config.SESION_HORAS)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sesiones
                (id, usuario_id, csrf, creada_en, expira_en, ultima_actividad, ip, agente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_hash(token), usuario_id, csrf, ahora.isoformat(), expira.isoformat(),
             ahora.isoformat(), ip, (agente or "")[:200]),
        )
        conn.commit()

    return token, csrf


def leer_sesion(token):
    """Devuelve los datos de la sesion y del usuario, o None si no vale.

    Aplica vencimiento deslizante: mientras la sesion se use, se corre la fecha
    de expiracion. Una sesion inactiva vence sola.
    """
    if not token:
        return None

    ahora = _ahora()

    with get_connection() as conn:
        fila = conn.execute(
            """
            SELECT s.id, s.csrf, s.expira_en, s.ultima_actividad,
                   u.id AS usuario_id, u.email, u.nombre, u.rol, u.activo
            FROM sesiones s
            JOIN usuarios u ON u.id = s.usuario_id
            WHERE s.id = ?
            """,
            (_hash(token),),
        ).fetchone()

        if fila is None:
            return None

        if datetime.fromisoformat(fila["expira_en"]) <= ahora:
            conn.execute("DELETE FROM sesiones WHERE id = ?", (fila["id"],))
            conn.commit()
            return None

        # Un usuario dado de baja pierde la sesion en el acto, sin esperar a que
        # venza: la baja tiene que ser efectiva ya.
        if not fila["activo"]:
            conn.execute("DELETE FROM sesiones WHERE id = ?", (fila["id"],))
            conn.commit()
            return None

        ultima = datetime.fromisoformat(fila["ultima_actividad"])
        if ahora - ultima > REFRESCO_MINIMO:
            conn.execute(
                "UPDATE sesiones SET ultima_actividad = ?, expira_en = ? WHERE id = ?",
                (ahora.isoformat(),
                 (ahora + timedelta(hours=config.SESION_HORAS)).isoformat(),
                 fila["id"]),
            )
            conn.commit()

    return {
        "csrf": fila["csrf"],
        "usuario": {
            "id": fila["usuario_id"],
            "email": fila["email"],
            "nombre": fila["nombre"],
            "rol": fila["rol"],
        },
    }


def cerrar_sesion(token):
    if not token:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM sesiones WHERE id = ?", (_hash(token),))
        conn.commit()


def cerrar_sesiones_de(usuario_id):
    """Corta todas las sesiones abiertas de un usuario. Se usa al darlo de baja
    o al cambiarle el rol."""
    with get_connection() as conn:
        conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))
        conn.commit()


def purgar_vencidas():
    with get_connection() as conn:
        conn.execute("DELETE FROM sesiones WHERE expira_en <= ?", (_ahora().isoformat(),))
        conn.commit()


def poner_cookie(respuesta, token):
    """La cookie es HttpOnly: el JavaScript de la pagina no puede leerla, asi que
    un XSS no puede robar la sesion.

    Sin atributo Domain, con lo cual queda acotada al host de la API y no se
    envia a ningun otro subdominio. SameSite=Lax alcanza porque el frontend y la
    API comparten dominio registrable: sus peticiones son same-site.
    """
    respuesta.set_cookie(
        NOMBRE_COOKIE,
        token,
        max_age=config.SESION_HORAS * 3600,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )
    return respuesta


def borrar_cookie(respuesta):
    respuesta.delete_cookie(
        NOMBRE_COOKIE,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )
    return respuesta
