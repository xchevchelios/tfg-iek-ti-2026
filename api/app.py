"""Fabrica de la aplicacion Flask.

La API sirve unicamente datos: el frontend es estatico y se sirve desde un CDN,
en otro origen pero bajo el mismo dominio registrable. Esa condicion es la que
permite que la cookie de sesion no sea tratada como cookie de terceros.
"""
import logging

from flask import Flask, jsonify
from flask_cors import CORS

import config
from db import init_db
from routes.auth import bp as bp_auth
from routes.estaciones import bp as bp_estaciones
from routes.gateway import bp as bp_gateway
from routes.mediciones import bp as bp_mediciones
from routes.usuarios import bp as bp_usuarios
from seguridad import cargar_sesion


def create_app():
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    CORS(
        app,
        resources={r"/api/*": {"origins": config.origenes_cors()}},
        allow_headers=["Content-Type", "X-CSRF-Token"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        # Sin esto el navegador no adjunta la cookie de sesion en peticiones
        # cross-origin, aunque sean del mismo sitio.
        supports_credentials=True,
    )

    init_db()

    # La sesion se resuelve una vez por peticion y queda en `g`, para que los
    # decoradores y la bitacora no tengan que volver a consultarla.
    app.before_request(cargar_sesion)

    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_usuarios)
    app.register_blueprint(bp_estaciones)
    app.register_blueprint(bp_gateway)
    app.register_blueprint(bp_mediciones)

    @app.get("/api/salud")
    def salud():
        """Chequeo de vida: verifica que la base responda."""
        from db import get_connection

        try:
            with get_connection() as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001 - se reporta tal cual
            return jsonify({"estado": "error", "detalle": str(exc)}), 503

        return jsonify({
            "estado": "ok",
            "autenticacion": config.autenticacion_configurada(),
        })

    @app.errorhandler(404)
    def no_encontrado(_error):
        return jsonify({"error": "Recurso no encontrado."}), 404

    @app.errorhandler(500)
    def error_interno(_error):
        app.logger.exception("Error interno no controlado")
        return jsonify({"error": "Error interno del servidor."}), 500

    return app


app = create_app()


if __name__ == "__main__":
    # Solo para pruebas locales. En produccion la sirve gunicorn (ver wsgi.py).
    app.run(debug=True, host="127.0.0.1", port=5000)
