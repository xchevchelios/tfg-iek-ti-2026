"""
Rutas de API REST.
Endpoints para obtener datos recientes e históricos.
"""
from flask import jsonify, request, current_app
from app.routes import api_bp
from app.db import get_recent_data, get_history


@api_bp.route('/datos_recientes', methods=['GET'])
def get_datos_recientes():
    """
    Obtiene los últimos 50 registros de cada estación.
    Usado para cargar datos iniciales en el dashboard.
    
    Returns:
        JSON: Lista de registros recientes
    """
    try:
        datos = get_recent_data(limit=50)
        return jsonify(datos)
    except Exception as e:
        current_app.logger.error(f"Error en /api/datos_recientes: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/historial', methods=['GET'])
def get_historial():
    """
    Obtiene datos históricos filtrados por estación, métrica y rango de tiempo.
    
    Query Parameters:
        - estacion: ID de estación (estacion_01, estacion_02) - REQUERIDO
        - metrica: Métrica (pm25, pm10, temperatura, humedad) - REQUERIDO
        - rango: Rango rápido (24h, 7d, 30d) - OPCIONAL
        - start: Fecha inicio (YYYY-MM-DD) - OPCIONAL
        - end: Fecha fin (YYYY-MM-DD) - OPCIONAL
    
    Ejemplos:
        /api/historial?estacion=estacion_01&metrica=pm25&rango=24h
        /api/historial?estacion=estacion_01&metrica=pm25&start=2026-04-20&end=2026-04-23
    
    Returns:
        JSON: Lista de datos formateados para Chart.js
    """
    try:
        # Validar parámetros obligatorios
        station_id = request.args.get('estacion')
        metrica = request.args.get('metrica')
        
        if not station_id or not metrica:
            return jsonify({
                "error": "Faltan parámetros 'estacion' o 'metrica'"
            }), 400
        
        # Leer parámetros opcionales
        rango = request.args.get('rango')
        start_date = request.args.get('start')
        end_date = request.args.get('end')
        
        # Obtener datos
        datos = get_history(station_id, metrica, rango, start_date, end_date)
        return jsonify(datos)
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error en /api/historial: {e}")
        return jsonify({"error": str(e)}), 500
