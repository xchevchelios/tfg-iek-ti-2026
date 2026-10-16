"""Autenticacion con Google mediante OpenID Connect (flujo de codigo con PKCE).

El canje del codigo ocurre en el servidor, donde vive el client_secret: el
navegador nunca lo ve. Google solo responde quien es la persona; que puede
hacer lo decide la tabla `usuarios` de este sistema.
"""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, g, jsonify, redirect, request

import config
from db import get_connection
from seguridad import registrar, requiere_sesion
from sesiones import (
    NOMBRE_COOKIE,
    borrar_cookie,
    cerrar_sesion,
    crear_sesion,
    poner_cookie,
)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

AUTORIZACION = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
EMISORES = {"accounts.google.com", "https://accounts.google.com"}

COOKIE_TRANSITO = "tfg_oauth"
VIDA_TRANSITO = 600  # 10 minutos para completar el login


def _b64url(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _destino_seguro(valor):
    """Evita convertir el login en un redirector abierto.

    Sin esta validacion, un enlace a /api/auth/login?destino=https://sitio.malo
    terminaria enviando al usuario ahi despues de autenticarse, con la
    credibilidad de haber pasado por Google.
    """
    if not valor or not valor.startswith("/") or valor.startswith("//"):
        return "/admin"
    return valor


def _volver(destino, **parametros):
    url = f"{config.FRONTEND_URL}{destino}"
    if parametros:
        url += "?" + urlencode(parametros)
    return redirect(url, code=302)


@bp.get("/login")
def login():
    """Arranca el flujo: guarda el estado en transito y manda a Google."""
    if not config.autenticacion_configurada():
        return jsonify({"error": "La autenticacion no esta configurada."}), 503

    destino = _destino_seguro(request.args.get("destino"))

    estado = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verificador = secrets.token_urlsafe(48)
    desafio = _b64url(hashlib.sha256(verificador.encode("ascii")).digest())

    parametros = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        "nonce": nonce,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        # Obliga a elegir cuenta en vez de reusar la ultima en silencio.
        "prompt": "select_account",
    }

    respuesta = redirect(f"{AUTORIZACION}?{urlencode(parametros)}", code=302)

    # El estado en transito viaja en una cookie efimera. Al volver de Google se
    # compara con el `state` de la URL: un atacante no puede fijar esta cookie,
    # asi que no puede inyectar su propio codigo de autorizacion.
    respuesta.set_cookie(
        COOKIE_TRANSITO,
        _b64url(json.dumps({
            "estado": estado,
            "nonce": nonce,
            "verificador": verificador,
            "destino": destino,
        }).encode("utf-8")),
        max_age=VIDA_TRANSITO,
        httponly=True,
        secure=config.COOKIE_SECURE,
        # Lax permite que la cookie viaje en la navegacion de vuelta desde
        # Google, que es un GET de primer nivel.
        samesite="Lax",
        path="/api/auth",
    )
    return respuesta


def _leer_transito():
    crudo = request.cookies.get(COOKIE_TRANSITO)
    if not crudo:
        return None
    try:
        relleno = "=" * (-len(crudo) % 4)
        return json.loads(base64.urlsafe_b64decode(crudo + relleno))
    except Exception:
        return None


def _datos_del_id_token(id_token, nonce):
    """Valida el id_token y devuelve sus afirmaciones.

    No se verifica la firma RS256, y es deliberado: el token llego por el canal
    directo contra el endpoint de Google sobre TLS, no a traves del navegador.
    OpenID Connect Core 3.1.3.7 admite expresamente apoyarse en la validacion
    TLS del servidor en ese caso. Lo que si se comprueba es emisor, audiencia,
    vencimiento y nonce.
    """
    try:
        cuerpo = id_token.split(".")[1]
        relleno = "=" * (-len(cuerpo) % 4)
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + relleno))
    except Exception:
        return None, "El id_token no se pudo interpretar."

    if datos.get("iss") not in EMISORES:
        return None, "Emisor inesperado."
    if datos.get("aud") != config.GOOGLE_CLIENT_ID:
        return None, "El token no fue emitido para esta aplicacion."
    if datos.get("exp", 0) <= datetime.now(timezone.utc).timestamp():
        return None, "El token esta vencido."
    # Ata esta respuesta a esta peticion concreta: sin el nonce, un token
    # obtenido en otro contexto podria reutilizarse aca.
    if datos.get("nonce") != nonce:
        return None, "El nonce no coincide."
    if not datos.get("email"):
        return None, "Google no devolvio un correo."
    if not datos.get("email_verified"):
        return None, "El correo de la cuenta no esta verificado."

    return datos, None


def _buscar_usuario(conn, sub, email):
    """Busca primero por el identificador estable de Google y despues por correo.

    El `sub` no cambia nunca; el correo si. Buscar primero por sub evita que un
    cambio de direccion deje a la persona afuera, y el correo solo se usa para
    el primer encuentro, cuando todavia no hay sub registrado.
    """
    fila = conn.execute("SELECT * FROM usuarios WHERE sub_google = ?", (sub,)).fetchone()
    if fila is not None:
        return fila
    return conn.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()


@bp.get("/callback")
def callback():
    transito = _leer_transito()
    destino = _destino_seguro((transito or {}).get("destino"))

    if request.args.get("error"):
        return _volver(destino, auth_error="cancelado")

    if transito is None:
        return _volver(destino, auth_error="expirado")

    if not secrets.compare_digest(request.args.get("state", ""), transito["estado"]):
        return _volver(destino, auth_error="estado_invalido")

    codigo = request.args.get("code", "")
    if not codigo:
        return _volver(destino, auth_error="sin_codigo")

    try:
        respuesta_token = requests.post(
            TOKEN,
            data={
                "code": codigo,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": config.OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": transito["verificador"],
            },
            timeout=10,
        )
    except requests.RequestException:
        return _volver(destino, auth_error="sin_conexion")

    if respuesta_token.status_code != 200:
        return _volver(destino, auth_error="canje_fallido")

    id_token = respuesta_token.json().get("id_token", "")
    datos, error = _datos_del_id_token(id_token, transito["nonce"])
    if error:
        return _volver(destino, auth_error="token_invalido")

    sub = datos["sub"]
    email = datos["email"]
    ahora = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        usuario = _buscar_usuario(conn, sub, email)

        if usuario is None:
            # Identidad valida pero no autorizada. Se distingue a proposito de
            # un fallo de autenticacion: la persona es quien dice ser, lo que
            # falta es que alguien la habilite.
            return _volver(destino, auth_error="no_autorizado")

        if not usuario["activo"]:
            return _volver(destino, auth_error="inactivo")

        conn.execute(
            "UPDATE usuarios SET sub_google = ?, ultimo_acceso = ?, nombre = ? WHERE id = ?",
            (sub, ahora, datos.get("name") or usuario["nombre"], usuario["id"]),
        )
        conn.commit()
        usuario_id = usuario["id"]
        usuario_email = usuario["email"]

    token, _csrf = crear_sesion(
        usuario_id,
        ip=request.headers.get("X-Real-IP") or request.remote_addr,
        agente=request.headers.get("User-Agent"),
    )

    g.usuario = {"id": usuario_id, "email": usuario_email}
    registrar("login", "usuario", usuario_email)

    return poner_cookie(_volver(destino), token)


@bp.get("/sesion")
def sesion():
    """Quien soy. Devuelve 200 siempre: no tener sesion no es un error, es un
    estado que el frontend necesita conocer para mostrar la pantalla de acceso.

    Aca se entrega el token CSRF que el frontend reenvia en las escrituras.
    """
    if not g.get("sesion"):
        return jsonify({
            "autenticado": False,
            "configurado": config.autenticacion_configurada(),
        })

    return jsonify({
        "autenticado": True,
        "configurado": True,
        "usuario": g.usuario,
        "csrf": g.sesion["csrf"],
    })


@bp.post("/logout")
@requiere_sesion
def logout():
    registrar("logout", "usuario", g.usuario["email"])
    cerrar_sesion(request.cookies.get(NOMBRE_COOKIE))
    return borrar_cookie(jsonify({"autenticado": False}))
