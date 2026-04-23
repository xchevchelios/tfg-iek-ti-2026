"""
Punto de entrada de la aplicación.
Ejecutar con: python app.py (desarrollo) o gunicorn wsgi:app (producción)
"""
import os
from app import create_app
from app.db import setup_database
from app.config import DevelopmentConfig, ProductionConfig

# Seleccionar configuración según entorno
config_class = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
app = create_app(config_class)

# Inicializar base de datos
with app.app_context():
    setup_database()

if __name__ == '__main__':
    # SOLO para desarrollo
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=True
    )
