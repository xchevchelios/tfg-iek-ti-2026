"""
Módulo de base de datos.
Funciones para conexión y operaciones de base de datos.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from flask import current_app


@contextmanager
def get_db_connection():
    """
    Context manager para conexiones a la BD.
    Garantiza cierre automático y manejo de transacciones.
    
    Yields:
        sqlite3.Connection: Conexión con row_factory configurado
    """
    conn = sqlite3.connect(current_app.config['DB_FILE'])
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def setup_database():
    """
    Inicializa la base de datos.
    Crea tabla e índices si no existen.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Crear tabla
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
        
        # Agregar columna humedad si no existe (migración)
        try:
            cursor.execute("ALTER TABLE mediciones ADD COLUMN humedad REAL")
        except sqlite3.OperationalError:
            pass
        
        # Crear índices para acelerar consultas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_station ON mediciones(station_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_time ON mediciones(timestamp)")
        
        conn.commit()
        print("Base de datos inicializada correctamente.")


def get_recent_data(limit=50):
    """
    Obtiene los últimos N registros de cada estación.
    
    Args:
        limit: Número máximo de registros por estación
        
    Returns:
        list: Lista de diccionarios con datos recientes
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        datos = []
        for station_id in ['estacion_01', 'estacion_02']:
            cursor.execute(
                """
                SELECT timestamp, station_id, pm25, pm10, temperatura, humedad
                FROM mediciones
                WHERE station_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (station_id, limit)
            )
            # Invertir para que estén de más antiguo a más nuevo
            datos.extend(reversed([dict(row) for row in cursor.fetchall()]))
        
        return datos


def get_history(station_id, metrica, rango=None, start_date=None, end_date=None):
    """
    Obtiene datos históricos filtrados.
    
    Args:
        station_id: ID de la estación (ej: 'estacion_01')
        metrica: Nombre de la métrica (pm25, pm10, temperatura, humedad)
        rango: String de rango rápido ('24h', '7d', '30d')
        start_date: Fecha inicio para rango personalizado (YYYY-MM-DD)
        end_date: Fecha fin para rango personalizado (YYYY-MM-DD)
        
    Returns:
        list: Lista de datos formateados para Chart.js
    """
    # Validar métrica
    metricas_validas = ['pm25', 'pm10', 'temperatura', 'humedad']
    if metrica not in metricas_validas:
        raise ValueError(f"Métrica no válida: {metrica}")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if rango:
            # Rango rápido
            if rango == '24h':
                fecha_inicio = datetime.now() - timedelta(hours=24)
            elif rango == '7d':
                fecha_inicio = datetime.now() - timedelta(days=7)
            elif rango == '30d':
                fecha_inicio = datetime.now() - timedelta(days=30)
            else:
                raise ValueError(f"Rango no válido: {rango}")
            
            sql = f"""
                SELECT timestamp, {metrica}
                FROM mediciones
                WHERE station_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """
            cursor.execute(sql, (station_id, fecha_inicio.isoformat()))
            
        elif start_date and end_date:
            # Rango personalizado
            start_datetime = f"{start_date}T00:00:00"
            end_datetime = f"{end_date}T23:59:59"
            
            sql = f"""
                SELECT timestamp, {metrica}
                FROM mediciones
                WHERE station_id = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
            """
            cursor.execute(sql, (station_id, start_datetime, end_datetime))
        else:
            raise ValueError("Se requiere 'rango' o 'start_date'/'end_date'")
        
        datos = cursor.fetchall()
        
        # Formatear para Chart.js
        return [
            {'x': row['timestamp'], 'y': row[metrica]}
            for row in datos
        ]


def get_latest_aggregated_data(interval_seconds=30):
    """
    Obtiene el último dato agregado de cada estación.
    Útil para SSE (actualización cada N segundos).
    
    Args:
        interval_seconds: Intervalo de agregación en segundos
        
    Returns:
        dict: Datos agregados por estación
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Obtener el último timestamp agregado
        cursor.execute("""
            SELECT MAX(timestamp) as max_timestamp
            FROM mediciones
            WHERE timestamp >= datetime('now', '-' || ? || ' seconds')
        """, (interval_seconds,))
        
        result = cursor.fetchone()
        if not result or not result['max_timestamp']:
            return {}
        
        max_timestamp = result['max_timestamp']
        
        # Obtener datos de ambas estaciones en ese intervalo
        cursor.execute("""
            SELECT 
                station_id,
                AVG(pm25) as pm25,
                AVG(pm10) as pm10,
                AVG(temperatura) as temperatura,
                AVG(humedad) as humedad,
                MAX(timestamp) as timestamp
            FROM mediciones
            WHERE timestamp >= datetime('now', '-' || ? || ' seconds')
            GROUP BY station_id
            ORDER BY station_id
        """, (interval_seconds,))
        
        return {row['station_id']: dict(row) for row in cursor.fetchall()}
