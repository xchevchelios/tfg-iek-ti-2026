"""Fabrica de la aplicacion Flask.

La API sirve unicamente datos: el frontend es estatico y se sirve desde un CDN
(Cloudflare), en otro origen. Por eso CORS se configura de forma explicita, con
una lista blanca de origenes y nunca con '*'.
"""
import logging

from flask import Flask, jsonify
from flask_cors import CORS

import config
from db import init_db
from routes.estaciones import bp as bp_estaciones
from routes.mediciones import bp as bp_mediciones


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
        allow_headers=["Content-Type", "X-API-Token"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )

    init_db()

    app.register_blueprint(bp_estaciones)
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
        return jsonify({"estado": "ok", "escrituras": bool(config.API_TOKEN)})

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
