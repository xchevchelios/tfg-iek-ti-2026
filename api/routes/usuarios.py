"""Gestion de usuarios. Solo para el rol admin.

No hay registro publico: un endpoint abierto de alta en un panel de
administracion es una puerta, no una funcionalidad. El primer usuario se crea
por consola (crear_usuario.py) y de ahi en mas los habilita un admin.
"""
import re
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from db import get_connection
from seguridad import registrar, requiere_rol
from sesiones import cerrar_sesiones_de

bp = Blueprint("usuarios", __name__, url_prefix="/api/usuarios")

ROLES = ("admin", "operador")
PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _a_dict(fila):
    return {
        "id": fila["id"],
        "email": fila["email"],
        "nombre": fila["nombre"],
        "rol": fila["rol"],
        "activo": bool(fila["activo"]),
        "vinculado": bool(fila["sub_google"]),
        "creado_en": fila["creado_en"],
        "ultimo_acceso": fila["ultimo_acceso"],
    }


@bp.get("")
@requiere_rol("admin")
def listar():
    with get_connection() as conn:
        filas = conn.execute("SELECT * FROM usuarios ORDER BY email").fetchall()
    return jsonify([_a_dict(f) for f in filas])


@bp.post("")
@requiere_rol("admin")
def crear():
    datos = request.get_json(silent=True) or {}

    email = str(datos.get("email", "")).strip().lower()
    if not PATRON_EMAIL.match(email):
        return jsonify({"error": "Correo invalido."}), 400

    nombre = str(datos.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio."}), 400

    rol = str(datos.get("rol", "operador")).strip()
    if rol not in ROLES:
        return jsonify({"error": f"Rol invalido. Opciones: {', '.join(ROLES)}."}), 400

    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO usuarios (email, nombre, rol, activo, creado_en)
                VALUES (?, ?, ?, 1, ?)
                """,
                (email, nombre, rol, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            fila = conn.execute(
                "SELECT * FROM usuarios WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Ya existe un usuario con el correo {email}."}), 409

    registrar("alta_usuario", "usuario", f"{email} ({rol})")
    return jsonify(_a_dict(fila)), 201


@bp.put("/<int:usuario_id>")
@requiere_rol("admin")
def actualizar(usuario_id):
    datos = request.get_json(silent=True) or {}

    with get_connection() as conn:
        fila = conn.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if fila is None:
            return jsonify({"error": "Usuario no encontrado."}), 404

        rol = fila["rol"]
        if "rol" in datos:
            rol = str(datos["rol"]).strip()
            if rol not in ROLES:
                return jsonify({"error": "Rol invalido."}), 400

        activo = fila["activo"]
        if "activo" in datos:
            activo = 1 if datos["activo"] else 0

        nombre = fila["nombre"]
        if "nombre" in datos:
            nombre = str(datos["nombre"]).strip()
            if not nombre:
                return jsonify({"error": "El nombre no puede quedar vacio."}), 400

        # Dos bloqueos contra quedarse afuera del propio panel: nadie se saca a
        # si mismo el rol de admin ni se desactiva solo. Para eso tiene que
        # hacerlo otro admin, que por definicion sigue teniendo acceso.
        if usuario_id == g.usuario["id"]:
            if rol != "admin":
                return jsonify({"error": "No podes quitarte tu propio rol de admin."}), 400
            if not activo:
                return jsonify({"error": "No podes desactivarte a vos mismo."}), 400

        if rol == "operador" and fila["rol"] == "admin":
            quedan = conn.execute(
                "SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1 AND id != ?",
                (usuario_id,),
            ).fetchone()[0]
            if quedan == 0:
                return jsonify({"error": "Tiene que quedar al menos un admin activo."}), 400

        conn.execute(
            "UPDATE usuarios SET nombre = ?, rol = ?, activo = ? WHERE id = ?",
            (nombre, rol, activo, usuario_id),
        )
        conn.commit()
        actualizado = conn.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()

    # Un cambio de rol o una baja tienen que ser efectivos ya, no cuando venza
    # la sesion que la persona tenga abierta.
    if rol != fila["rol"] or activo != fila["activo"]:
        cerrar_sesiones_de(usuario_id)

    registrar("edicion_usuario", "usuario", f"{actualizado['email']} -> {rol}, activo={bool(activo)}")
    return jsonify(_a_dict(actualizado))


@bp.delete("/<int:usuario_id>")
@requiere_rol("admin")
def dar_de_baja(usuario_id):
    """Baja logica. No se borra la fila porque la bitacora la referencia."""
    if usuario_id == g.usuario["id"]:
        return jsonify({"error": "No podes darte de baja a vos mismo."}), 400

    with get_connection() as conn:
        fila = conn.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if fila is None:
            return jsonify({"error": "Usuario no encontrado."}), 404

        if fila["rol"] == "admin":
            quedan = conn.execute(
                "SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1 AND id != ?",
                (usuario_id,),
            ).fetchone()[0]
            if quedan == 0:
                return jsonify({"error": "Tiene que quedar al menos un admin activo."}), 400

        conn.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario_id,))
        conn.commit()
        email = fila["email"]

    cerrar_sesiones_de(usuario_id)
    registrar("baja_usuario", "usuario", email)
    return jsonify({"id": usuario_id, "activo": False})


@bp.get("/bitacora")
@requiere_rol("admin")
def bitacora():
    """Ultimas acciones registradas. Responde quien hizo que y cuando."""
    try:
        limite = min(int(request.args.get("limite", 100)), 500)
    except ValueError:
        return jsonify({"error": "'limite' tiene que ser un numero entero."}), 400

    with get_connection() as conn:
        filas = conn.execute(
            "SELECT momento, email, accion, entidad, detalle FROM bitacora "
            "ORDER BY momento DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return jsonify([dict(f) for f in filas])
