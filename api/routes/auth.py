"""Proteccion de las operaciones de escritura de la API."""
from functools import wraps

from flask import jsonify, request

import config


def requiere_token(vista):
    """Exige la cabecera X-API-Token en las operaciones de escritura.

    Si API_TOKEN no esta configurado, la escritura se rechaza en vez de quedar
    abierta: la pagina de gestion no puede estar expuesta a internet sin
    control, y un despliegue mal configurado tiene que fallar de forma
    evidente, no silenciosamente permisiva.
    """

    @wraps(vista)
    def envoltorio(*args, **kwargs):
        if not config.API_TOKEN:
            return (
                jsonify(
                    {
                        "error": "Las operaciones de escritura estan deshabilitadas: "
                        "falta configurar API_TOKEN en el servidor."
                    }
                ),
                503,
            )

        enviado = request.headers.get("X-API-Token", "")
        if enviado != config.API_TOKEN:
            return jsonify({"error": "Token invalido o ausente."}), 401

        return vista(*args, **kwargs)

    return envoltorio
