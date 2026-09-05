/**
 * Cliente MQTT sobre WebSocket para los datos en vivo.
 *
 * Usa MQTT.js en lugar de Paho: Paho esta sin mantenimiento activo desde hace
 * anios, mientras que MQTT.js tiene reconexion automatica incorporada, soporte
 * WSS nativo y sigue el estandar MQTT 5.
 */
import mqtt from 'mqtt';

import { MQTT_PASS, MQTT_TOPIC, MQTT_URL, MQTT_USER } from '../config.js';

/**
 * Abre la conexion y devuelve un objeto con `desconectar()`.
 *
 * @param {object} manejadores
 * @param {(estacionId: string, medicion: object) => void} manejadores.alRecibir
 * @param {(estado: 'conectado'|'reconectando'|'desconectado'|'error', detalle?: string) => void} manejadores.alCambiarEstado
 */
export function conectarMqtt({ alRecibir, alCambiarEstado = () => {} }) {
  const cliente = mqtt.connect(MQTT_URL, {
    username: MQTT_USER,
    password: MQTT_PASS,
    // Id unico por pestania: dos dashboards abiertos con el mismo id se
    // desconectarian mutuamente.
    clientId: `dashboard_${Math.random().toString(16).slice(2, 10)}`,
    clean: true,
    reconnectPeriod: 5000,
    connectTimeout: 10000,
  });

  cliente.on('connect', () => {
    cliente.subscribe(MQTT_TOPIC, { qos: 0 }, (error) => {
      if (error) {
        console.error('No se pudo suscribir al topic:', error);
        alCambiarEstado('error', 'No se pudo suscribir al topic.');
        return;
      }
      alCambiarEstado('conectado');
    });
  });

  cliente.on('reconnect', () => alCambiarEstado('reconectando'));
  cliente.on('offline', () => alCambiarEstado('desconectado'));
  cliente.on('close', () => alCambiarEstado('desconectado'));

  cliente.on('error', (error) => {
    console.error('Error de MQTT:', error);
    alCambiarEstado('error', error?.message);
  });

  cliente.on('message', (topic, payload) => {
    try {
      // 'estaciones/<id>/data'
      const estacionId = topic.split('/')[1];
      if (!estacionId) return;
      alRecibir(estacionId, JSON.parse(payload.toString()));
    } catch (error) {
      console.error('Mensaje MQTT ilegible en', topic, error);
    }
  });

  return {
    desconectar() {
      cliente.end(true);
    },
  };
}
