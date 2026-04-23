"""
Rutas para Server-Sent Events (SSE).
Proporciona un stream de datos en tiempo real al navegador.
"""
from flask import Response, current_app
from app.routes import sse_bp
from app.db import get_latest_aggregated_data
import json
import time
from datetime import datetime


@sse_bp.route('/stream', methods=['GET'])
def stream():
    """
    Endpoint SSE que envía datos agregados cada N segundos.
    
    El navegador se conecta y recibe actualizaciones automáticamente.
    Reemplaza la conexión directa a MQTT.
    
    Returns:
        Response: Stream de eventos Server-Sent Events
    """
    def generate():
        """
        Generador de eventos SSE.
        Envía datos agregados cada SSE_AGGREGATION_INTERVAL segundos.
        """
        interval = current_app.config['SSE_AGGREGATION_INTERVAL']
        
        while True:
            try:
                # Obtener datos agregados del último intervalo
                datos = get_latest_aggregated_data(interval_seconds=interval)
                
                if datos:
                    # Formatear datos para enviar
                    payload = {
                        'timestamp': datetime.now().isoformat(),
                        'data': datos
                    }
                    
                    # Enviar como evento SSE
                    yield f"data: {json.dumps(payload)}\n\n"
                
                # Esperar antes de siguiente envío
                time.sleep(interval)
                
            except Exception as e:
                current_app.logger.error(f"Error en SSE stream: {e}")
                # Enviar evento de error
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(interval)
    
    # Configurar respuesta como stream SSE
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # Importante para Nginx
            'Connection': 'keep-alive'
        }
    )
