/**
 * Conexion MQTT del panel de administracion.
 *
 * Una sola conexion para las dos pestanias que la usan (Gateway y Trafico),
 * con un usuario de solo lectura cuyas credenciales entrega la API despues
 * del login. A diferencia de las del dashboard publico, no viajan en el
 * codigo del sitio.
 *
 * Se abre recien cuando se visita una de esas pestanias, y MQTT.js se carga
 * en ese momento (import dinamico): quien solo administra estaciones no
 * descarga el cliente MQTT.
 *
 * Guarda el ultimo mensaje de cada topic. Al suscribirse, el broker entrega
 * los retenidos (estado del gateway, parametros, lista de nodos), asi que ese
 * mapa es en todo momento la foto del estado actual del sistema.
 */
import { MQTT_URL } from '../config.js';
import { credencialesMqtt } from './gateway.js';

export const TOPICS_PANEL = ['estaciones/#', 'gateway/#'];

let cliente = null;
let conectando = null;
let estado = 'desconectado';
let detalle = '';
const ultimos = new Map();
const oyentesMensaje = new Set();
const oyentesEstado = new Set();

function cambiarEstado(nuevo, texto = '') {
  estado = nuevo;
  detalle = texto;
  oyentesEstado.forEach((f) => f(estado, detalle));
}

/** Abre la conexion si no esta abierta. Rechaza si la API no entrega credenciales. */
export function conectarPanel() {
  if (cliente) return Promise.resolve();
  if (conectando) return conectando;

  conectando = (async () => {
    cambiarEstado('conectando');
    try {
      const [{ default: mqtt }, credenciales] = await Promise.all([
        import('mqtt'),
        credencialesMqtt(),
      ]);
      cliente = mqtt.connect(MQTT_URL, {
        username: credenciales.usuario,
        password: credenciales.clave,
        clientId: `panel_${Math.random().toString(16).slice(2, 10)}`,
        clean: true,
        reconnectPeriod: 5000,
        connectTimeout: 10000,
      });

      cliente.on('connect', () => {
        // Topics explicitos y no '#': coinciden exactamente con lo que el ACL
        // le permite leer a este usuario.
        cliente.subscribe(TOPICS_PANEL, { qos: 0 }, (error) => {
          if (error) cambiarEstado('error', 'El broker rechazó la suscripción.');
          else cambiarEstado('conectado');
        });
      });
      cliente.on('reconnect', () => cambiarEstado('reconectando'));
      cliente.on('offline', () => cambiarEstado('desconectado'));
      cliente.on('error', (error) => cambiarEstado('error', error?.message ?? 'Error de conexión.'));

      cliente.on('message', (topic, carga, paquete) => {
        const mensaje = {
          topic,
          texto: carga.toString(),
          retenido: Boolean(paquete?.retain),
          hora: new Date(),
        };
        // Un retenido vacio es el broker borrando el estado de ese topic.
        if (mensaje.texto === '') ultimos.delete(topic);
        else ultimos.set(topic, mensaje);
        oyentesMensaje.forEach((f) => f(mensaje));
      });
    } catch (error) {
      cliente = null;
      cambiarEstado('error', error?.message ?? 'No se pudo conectar al broker.');
      throw error;
    } finally {
      conectando = null;
    }
  })();
  return conectando;
}

export function desconectarPanel() {
  if (cliente) cliente.end(true);
  cliente = null;
  ultimos.clear();
  cambiarEstado('desconectado');
}

/** Suscribe una funcion a cada mensaje. Devuelve la funcion para desuscribirse. */
export function alRecibirMensaje(f) {
  oyentesMensaje.add(f);
  return () => oyentesMensaje.delete(f);
}

/** Suscribe una funcion a los cambios de estado; la llama ya con el actual. */
export function alCambiarEstadoPanel(f) {
  oyentesEstado.add(f);
  f(estado, detalle);
  return () => oyentesEstado.delete(f);
}

/** Ultimo mensaje recibido en un topic, o undefined. */
export const ultimoMensaje = (topic) => ultimos.get(topic);
