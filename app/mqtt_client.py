"""
Módulo para manejar la conexión a MQTT.
Nota: Este módulo se usa en mqtt_a_sqlite.py, no en la app Flask principal.
Se incluye aquí para centralizar la lógica.
"""
import paho.mqtt.client as mqtt
import json
from datetime import datetime
from app.db import get_db_connection


class MQTTClient:
    """Cliente MQTT para suscribirse a datos de sensores."""
    
    def __init__(self, broker, port, user, password, topic):
        """
        Inicializa el cliente MQTT.
        
        Args:
            broker: Dirección del broker
            port: Puerto del broker
            user: Usuario MQTT
            password: Contraseña MQTT
            topic: Topic MQTT a suscribirse
        """
        self.broker = broker
        self.port = port
        self.user = user
        self.password = password
        self.topic = topic
        self.client = None
        
    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback de conexión."""
        if rc == 0:
            print("Conectado al broker MQTT.")
            client.subscribe(self.topic)
            print(f"Suscrito a: {self.topic}")
        else:
            print(f"Error de conexión (código {rc}).")
    
    def on_message(self, client, userdata, msg):
        """Callback de mensajes recibidos."""
        try:
            station_id = msg.topic.split('/')[1]
            data = json.loads(msg.payload)
            timestamp = data.get("timestamp")
            
            if not timestamp:
                print(f"Error: {station_id} sin timestamp. Descartado.")
                return
            
            # Guardar en BD
            self.save_to_db(
                timestamp=timestamp,
                station_id=station_id,
                pm25=data.get("pm25"),
                pm10=data.get("pm10"),
                temperatura=data.get("temperatura"),
                humedad=data.get("humedad")
            )
            
            print(f"Datos de {station_id} guardados. Timestamp: {timestamp}")
            
        except json.JSONDecodeError:
            print(f"Error: JSON inválido en payload")
        except Exception as e:
            print(f"Error procesando mensaje: {e}")
    
    @staticmethod
    def save_to_db(timestamp, station_id, pm25, pm10, temperatura, humedad):
        """Guarda datos en la base de datos."""
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO mediciones 
                (timestamp, station_id, pm25, pm10, temperatura, humedad)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, station_id, pm25, pm10, temperatura, humedad)
            )
    
    def connect_and_run(self):
        """Conecta al broker y mantiene la conexión activa."""
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="mqtt_sqlite_connector_v5"
        )
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        try:
            self.client.connect(self.broker, self.port, 60)
            print("Servicio de persistencia iniciado. Esperando mensajes...")
            self.client.loop_forever()
        except KeyboardInterrupt:
            print("Finalizado por el usuario.")
        except Exception as e:
            print(f"Error al conectar: {e}")
