"""
Módulo de rutas.
Registra blueprints de API y SSE.
"""
from flask import Blueprint

# Blueprint para API REST
api_bp = Blueprint('api', __name__, url_prefix='/api')

# Blueprint para Server-Sent Events
sse_bp = Blueprint('sse', __name__)

# Importar rutas (debe ser después de crear los blueprints)
from app.routes import api, sse
