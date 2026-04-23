"""
Configuración centralizada de la aplicación.
Lee variables de entorno y define valores por defecto.
"""
import os
from dotenv import load_dotenv

# Cargar variables de .env
load_dotenv()


class Config:
    """Configuración base de la aplicación"""
    
    # Flask
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    DEBUG = FLASK_ENV == 'development'
    
    # MQTT
    MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_USER = os.getenv('MQTT_USER', '')
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
    MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'estaciones/+/data')
    
    # Base de datos
    DB_FILE = os.getenv('DB_FILE', 'sensores.db')
    
    # SSE (Server-Sent Events)
    SSE_AGGREGATION_INTERVAL = int(os.getenv('SSE_AGGREGATION_INTERVAL', 30))
    
    
class DevelopmentConfig(Config):
    """Configuración para desarrollo"""
    DEBUG = True
    FLASK_ENV = 'development'


class ProductionConfig(Config):
    """Configuración para producción"""
    DEBUG = False
    FLASK_ENV = 'production'
