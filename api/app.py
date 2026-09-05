import os
import re
import sqlite3
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CORS ---
# El frontend se sirve desde Cloudflare Workers, es decir desde OTRO origen que
# esta API, asi que el navegador exige cabeceras CORS explicitas.
# Origenes permitidos: los de la variable de entorno CORS_ORIGINS (separados por
# coma) o, si no esta definida, la lista por defecto de abajo.
_origenes_env = os.environ.get("CORS_ORIGINS", "").strip()
if _origenes_env:
    ORIGENES_PERMITIDOS = [o.strip() for o in _origenes_env.split(",") if o.strip()]
else:
    ORIGENES_PERMITIDOS = [
        # Frontend en Cloudflare Workers (produccion y URLs de preview,
        # que tienen la forma <version>-<worker>.<subdominio>.workers.dev)
        re.compile(
            r"^https://([a-z0-9-]+-)?tfg-iek-ti-2026"
            r"\.alvaro-aguinagalde13\.workers\.dev$"
        ),
        # Dominio de la VM (sigue sirviendo /api, /mqtt y el MQTTS del gateway)
        "https://air-quality-campus-una.duckdns.org",
        # Desarrollo local
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

CORS(app, resources={r"/api/*": {"origins": ORIGENES_PERMITIDOS}})

DB_NAME = "sensores.db"

def get_db_connection():
    """Crea una conexión a la BD que devuelve filas como diccionarios."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# --- API ANTIGUA (PARA EL DASHBOARD EN VIVO) ---
# Esta función sigue siendo necesaria para la carga inicial de la página.

@app.route('/api/datos_recientes')
def get_datos_recientes():
    """Devuelve los últimos 50 registros de cada estación."""
    datos_finales = []
    try:
        conn = get_db_connection()
        
        # Obtenemos los últimos 50 de la estación 1
        cursor1 = conn.execute(
            "SELECT * FROM mediciones WHERE station_id = 'estacion_01' ORDER BY timestamp DESC LIMIT 50"
        )
        datos_estacion_1 = cursor1.fetchall()
        
        # Obtenemos los últimos 50 de la estación 2
        cursor2 = conn.execute(
            "SELECT * FROM mediciones WHERE station_id = 'estacion_02' ORDER BY timestamp DESC LIMIT 50"
        )
        datos_estacion_2 = cursor2.fetchall()
        
        conn.close()
        
        # Invertimos el orden para que Chart.js los muestre de más antiguo a más nuevo
        datos_finales.extend(reversed(datos_estacion_1))
        datos_finales.extend(reversed(datos_estacion_2))
        
        # Convertimos las filas de SQLite (tipo 'Row') a diccionarios estándar
        datos_dict = [dict(fila) for fila in datos_finales]
        return jsonify(datos_dict)

    except Exception as e:
        print(f"Error en /api/datos_recientes: {e}")
        return jsonify({"error": str(e)}), 500


# --- NUEVA API (PARA EL MODAL DE HISTORIAL) ---

@app.route('/api/historial')
def get_historial():
    """
    Devuelve datos históricos filtrados por estación, métrica y rango de tiempo.
    Ej: /api/historial?estacion=estacion_01&metrica=pm25&rango=7d
    Ej: /api/historial?estacion=estacion_01&metrica=pm25&start=2025-10-20&end=2025-10-28
    """
    try:
        # 1. Leer parámetros obligatorios
        station_id = request.args.get('estacion')
        metrica = request.args.get('metrica')

        if not station_id or not metrica:
            return jsonify({"error": "Faltan parámetros 'estacion' o 'metrica'"}), 400

        # 2. Lista blanca de métricas para evitar Inyección SQL
        metricas_seguras = ['pm25', 'pm10', 'temperatura', 'humedad']
        if metrica not in metricas_seguras:
            return jsonify({"error": "Métrica no válida"}), 400

        # 3. Leer parámetros de tiempo
        rango = request.args.get('rango')
        start_date = request.args.get('start')
        end_date = request.args.get('end')

        sql = ""
        params = ()
        
        # 4. Construir la consulta SQL basada en los parámetros
        if rango:
            # --- Lógica para rangos rápidos (24h, 7d, 30d) ---
            if rango == '24h':
                fecha_inicio = datetime.now() - timedelta(hours=24)
            elif rango == '7d':
                fecha_inicio = datetime.now() - timedelta(days=7)
            elif rango == '30d':
                fecha_inicio = datetime.now() - timedelta(days=30)
            else:
                return jsonify({"error": "Rango no válido"}), 400
            
            # El f-string es seguro aquí porque 'metrica' fue validado por la lista blanca
            sql = f"""
                SELECT timestamp, {metrica} 
                FROM mediciones 
                WHERE station_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """
            params = (station_id, fecha_inicio.isoformat())

        elif start_date and end_date:
            # --- Lógica para rangos personalizados ---
            # Añadimos la hora para cubrir el día completo
            start_datetime = f"{start_date}T00:00:00"
            end_datetime = f"{end_date}T23:59:59"
            
            sql = f"""
                SELECT timestamp, {metrica} 
                FROM mediciones 
                WHERE station_id = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
            """
            params = (station_id, start_datetime, end_datetime)
        
        else:
            return jsonify({"error": "Faltan parámetros de tiempo ('rango' o 'start'/'end')"}), 400

        # 5. Ejecutar la consulta
        conn = get_db_connection()
        cursor = conn.cursor()
        datos_crudos = cursor.execute(sql, params).fetchall()
        conn.close()

        # 6. Formatear datos para Chart.js (formato {x, y})
        datos_formateados = []
        for fila in datos_crudos:
            datos_formateados.append({
                'x': fila['timestamp'], # Eje X
                'y': fila[metrica]      # Eje Y
            })
        
        # Devolverá una lista vacía [] si la consulta no encontró nada
        return jsonify(datos_formateados)

    except Exception as e:
        print(f"Error en /api/historial: {e}")
        return jsonify({"error": str(e)}), 500

# --- Ejecución (solo para pruebas locales, Gunicorn no usa esto) ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
