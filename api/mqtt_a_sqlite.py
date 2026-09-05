import paho.mqtt.client as mqtt
import sqlite3
import json
from datetime import datetime
from zoneinfo import ZoneInfo  # Nativo desde Python 3.9

# --- CONFIGURACIÓN ---
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "estaciones/+/data"
DB_FILE = "sensores.db"
MQTT_USER = "tfg_nodo"
MQTT_PASS = "gisd2026"

# --- BASE DE DATOS ---
def setup_database():
    """Crea la tabla e índices si no existen."""
    # Usamos 'with' para asegurar que la conexión se cierre
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mediciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                station_id TEXT NOT NULL,
                pm25 REAL,
                pm10 REAL,
                temperatura REAL,
                humedad REAL
            )
        """)
        # Asegurarnos de que la columna humedad existe (para migraciones)
        try:
            cursor.execute("ALTER TABLE mediciones ADD COLUMN humedad REAL")
        except sqlite3.OperationalError:
            pass # La columna ya existía

        # Crear índices para acelerar las consultas de la API
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_station ON mediciones(station_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_time ON mediciones(timestamp)")
        conn.commit()
    print(f"Base de datos '{DB_FILE}' lista y actualizada.")

# --- MQTT ---
def on_connect(client, userdata, flags, rc, properties=None):
    """Callback para cuando el cliente se conecta al broker."""
    if rc == 0:
        print("Conectado al broker MQTT.")
        client.subscribe(MQTT_TOPIC)
        print(f"Suscrito al topic: {MQTT_TOPIC}")
    else:
        print(f"Fallo en la conexión (código {rc}).")

def on_message(client, userdata, msg):
    """Callback para cuando se recibe un mensaje."""
    try:
        station_id = msg.topic.split('/')[1]
        data = json.loads(msg.payload)

        # Usamos el timestamp que viene del gateway
        timestamp = data.get("timestamp")

        # Si el payload no tiene un timestamp, no podemos guardarlo
        if not timestamp:
            print(f"Error: Mensaje de {station_id} no tiene timestamp. Descartado.")
            return

        pm25 = data.get("pm25")
        pm10 = data.get("pm10")
        temperatura = data.get("temperatura")
        humedad = data.get("humedad")

        # Usamos 'with' para manejar la transacción de forma segura
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                """
                INSERT INTO mediciones (timestamp, station_id, pm25, pm10, temperatura, humedad)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, station_id, pm25, pm10, temperatura, humedad)
            )

        print(f"Datos de {station_id} guardados. Timestamp: {timestamp}")

    except json.JSONDecodeError:
        print(f"Error: Mensaje no es un JSON válido: {msg.payload}")
    except Exception as e:
        print(f"Error procesando mensaje: {e}")

# --- EJECUCIÓN PRINCIPAL ---
if __name__ == "__main__":
    setup_database()

    # Usamos la API v2 de Paho-MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mqtt_sqlite_connector_v4")
    
    # Añadimos las credenciales para la conexión local
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print("Servicio de persistencia iniciado. Esperando mensajes...")
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nFinalizado por el usuario.")
    except Exception as e:
        print(f"Error al conectar con el broker: {e}")
