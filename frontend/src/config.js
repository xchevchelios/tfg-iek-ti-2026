/**
 * Configuracion de la aplicacion.
 *
 * Todo sale de variables de entorno de Vite (archivos .env.development y
 * .env.production). No hay URLs ni credenciales escritas en el codigo: cambiar
 * de entorno es cambiar un .env, no editar fuentes.
 */

/** Base de la API. Vacia en desarrollo: el proxy de Vite resuelve /api. */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

/** URL completa del broker MQTT sobre WebSocket. */
export const MQTT_URL = import.meta.env.VITE_MQTT_URL;

/**
 * Credenciales del usuario MQTT de solo lectura.
 * No son un secreto y no pueden serlo: un cliente MQTT de navegador siempre
 * las expone. La proteccion real es el ACL del broker.
 */
export const MQTT_USER = import.meta.env.VITE_MQTT_USER;
export const MQTT_PASS = import.meta.env.VITE_MQTT_PASS;

/** Topic al que se suscribe el dashboard. El '+' es el id de estacion. */
export const MQTT_TOPIC = 'estaciones/+/data';

/** Vista inicial del mapa. */
export const MAPA_CENTRO = (import.meta.env.VITE_MAPA_CENTRO ?? '-25.3345,-57.5150')
  .split(',')
  .map(Number);
export const MAPA_ZOOM = Number(import.meta.env.VITE_MAPA_ZOOM ?? 16);

/** Metricas que maneja el sistema, con su unidad y etiqueta para la interfaz. */
export const METRICAS = [
  { clave: 'pm25', etiqueta: 'PM2.5', unidad: 'µg/m³' },
  { clave: 'pm10', etiqueta: 'PM10', unidad: 'µg/m³' },
  { clave: 'temperatura', etiqueta: 'Temperatura', unidad: '°C' },
  { clave: 'humedad', etiqueta: 'Humedad', unidad: '% HR' },
];

/** Cantidad de puntos que se mantienen en los graficos en vivo. */
export const MAX_PUNTOS_VIVO = 20;
