/** Parametros del gateway y acceso de solo lectura al broker para el panel. */
import { api } from './client.js';

/** Rangos, valores por defecto y constantes de tiempo, los mismos del servidor. */
export const leerLimites = () => api.get('/api/gateway/limites');

/** Publica el documento completo de parametros (el servidor lo valida). */
export const guardarParametros = (parametros) => api.put('/api/gateway/parametros', parametros);

/** Usuario MQTT de solo lectura; la API lo entrega solo con sesion iniciada. */
export const credencialesMqtt = () => api.get('/api/gateway/credenciales-mqtt');
