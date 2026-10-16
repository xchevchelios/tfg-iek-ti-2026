#!/usr/bin/env python3
"""Alta del primer usuario, por consola.

No existe un endpoint publico de registro: el primer admin se crea aca y de ahi
en mas los usuarios los habilita un admin desde el panel.

    python3 crear_usuario.py alguien@gmail.com "Nombre Apellido" admin

El correo tiene que ser el de la cuenta de Google con la que la persona va a
entrar. No se guarda ninguna contrasena: la identidad la verifica Google.
"""
import sqlite3
import sys
from datetime import datetime, timezone

from db import get_connection, init_db

ROLES = ("admin", "operador")


def main(argv):
    if len(argv) != 4 or argv[3] not in ROLES:
        print(__doc__)
        print(f"Roles validos: {', '.join(ROLES)}")
        return 1

    email = argv[1].strip().lower()
    nombre = argv[2].strip()
    rol = argv[3]

    if "@" not in email:
        print(f"'{email}' no parece un correo.")
        return 1

    init_db()

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO usuarios (email, nombre, rol, activo, creado_en)
                VALUES (?, ?, ?, 1, ?)
                """,
                (email, nombre, rol, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        print(f"Ya existe un usuario con el correo {email}.")
        return 1

    print(f"Usuario creado: {email} ({rol})")
    print("Puede entrar en /admin con 'Entrar con Google' usando esa cuenta.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
