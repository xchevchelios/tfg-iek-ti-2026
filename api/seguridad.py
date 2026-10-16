"""Decoradores de autorizacion y proteccion CSRF.

Separa deliberadamente las dos preguntas:
  - autenticacion: quien sos  -> la resuelve Google y la sostiene la cookie
  - autorizacion:  que podes  -> la resuelve la tabla `usuarios` de este sistema

Google dice quien es la persona, no que puede hacer. Sin la lista de correos
habilitados, cualquiera con una cuenta de Google entraria al panel.
"""
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import g, jsonify, request

import config
from db import get_connection
from sesiones import NOMBRE_COOKIE, leer_sesion

# Metodos que no modifican estado: no necesitan comprobacion CSRF.
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}


def cargar_sesion():
    """Deja la sesion en `g` para que la vista y la bitacora la tengan a mano."""
    g.sesion = leer_sesion(request.cookies.get(NOMBRE_COOKIE))
    g.usuario = g.sesion["usuario"] if g.sesion else None


def _falta_sesion():
    return jsonify({"error": "Sesion requerida."}), 401


def _csrf_invalido():
    return jsonify({"error": "Token CSRF ausente o invalido."}), 403


def _verificar_csrf():
    """Comprobacion CSRF por token de sesion.

    La cookie viaja sola en cada peticion, asi que un sitio ajeno podria
    disparar una escritura con la sesion de la victima. El token se entrega en
    el cuerpo de /api/auth/sesion y el frontend lo reenvia en una cabecera: un
    atacante de otro origen no puede leerlo, porque CORS le impide leer esa
    respuesta, ni fijar cabeceras propias sin un preflight que la allowlist
    rechaza.

    SameSite=Lax ya bloquea el caso clasico; esto es la segunda capa.
    """
    enviado = request.headers.get("X-CSRF-Token", "")
    esperado = g.sesion["csrf"] if g.sesion else ""
    # Comparacion en tiempo constante: evita filtrar el token por el tiempo
    # que tarda en fallar la comparacion.
    return bool(enviado) and secrets.compare_digest(enviado, esperado)


def requiere_sesion(vista):
    @wraps(vista)
    def envoltorio(*args, **kwargs):
        if not config.autenticacion_configurada():
            return (
                jsonify({"error": "La autenticacion no esta configurada en el servidor."}),
                503,
            )
        if not g.get("sesion"):
            return _falta_sesion()
        if request.method not in METODOS_SEGUROS and not _verificar_csrf():
            return _csrf_invalido()
        return vista(*args, **kwargs)

    return envoltorio


def requiere_rol(*roles):
    """Exige sesion y ademas uno de los roles indicados."""

    def decorador(vista):
        @wraps(vista)
        @requiere_sesion
        def envoltorio(*args, **kwargs):
            if g.usuario["rol"] not in roles:
                return (
                    jsonify({"error": "No tenes permisos para esta operacion."}),
                    403,
                )
            return vista(*args, **kwargs)

        return envoltorio

    return decorador


def registrar(accion, entidad=None, detalle=None):
    """Anota una accion en la bitacora. Es lo que vuelve util tener usuarios:
    responde quien hizo que y cuando, que con un secreto compartido no se podia."""
    usuario = g.get("usuario")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO bitacora (momento, usuario_id, email, accion, entidad, detalle)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                usuario["id"] if usuario else None,
                usuario["email"] if usuario else None,
                accion,
                entidad,
                detalle,
            ),
        )
        conn.commit()
