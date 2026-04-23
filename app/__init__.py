"""
Módulo principal de la aplicación Flask.
Inicializa la app con configuración y rutas.
"""
from flask import Flask
from app.config import Config


def create_app(config_class=Config):
    """
    Factory function para crear la instancia de Flask.
    
    Args:
        config_class: Clase de configuración a usar
        
    Returns:
        app: Instancia de Flask configurada
    """
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(config_class)
    
    # Registrar blueprints
    from app.routes import api_bp, sse_bp
    app.register_blueprint(api_bp)
    app.register_blueprint(sse_bp)
    
    return app
