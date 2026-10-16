/** Sesión del panel de administración. */
import { API_BASE_URL } from '../config.js';
import { api, fijarCsrf } from './client.js';

/**
 * Pregunta al servidor quién soy.
 *
 * Devuelve siempre un objeto: no tener sesión no es un error, es el estado que
 * la página necesita conocer para mostrar la pantalla de acceso.
 */
export async function leerSesion() {
  try {
    const estado = await api.get('/api/auth/sesion');
    fijarCsrf(estado.csrf);
    return estado;
  } catch {
    return { autenticado: false, configurado: false, sinConexion: true };
  }
}

/**
 * El login es una navegación de primer nivel, no un fetch: el navegador tiene
 * que ir a Google y volver. Por eso se cambia la ubicación de la página en vez
 * de pedir la URL por detrás.
 */
export function entrar(destino = '/admin') {
  window.location.assign(
    `${API_BASE_URL}/api/auth/login?destino=${encodeURIComponent(destino)}`
  );
}

export async function salir() {
  await api.post('/api/auth/logout', {});
  fijarCsrf('');
}

/** Mensajes de los errores que el servidor devuelve en la URL de vuelta. */
const MENSAJES = {
  no_autorizado:
    'Tu cuenta de Google es válida, pero no está habilitada para administrar la red. ' +
    'Pedile a un administrador que te dé de alta.',
  inactivo: 'Tu usuario está dado de baja.',
  cancelado: 'Cancelaste el acceso en la pantalla de Google.',
  expirado: 'La solicitud de acceso venció. Probá de nuevo.',
  estado_invalido: 'La solicitud no se pudo validar. Probá de nuevo.',
  token_invalido: 'Google devolvió una respuesta que no se pudo validar.',
  canje_fallido: 'Google rechazó la solicitud de acceso.',
  sin_codigo: 'Google no devolvió un código de autorización.',
  sin_conexion: 'El servidor no pudo contactar a Google.',
};

/**
 * Lee el error que el servidor dejó en la URL y lo limpia de la barra de
 * direcciones, para que no reaparezca al recargar.
 */
export function tomarErrorDeAcceso() {
  const codigo = new URLSearchParams(window.location.search).get('auth_error');
  if (!codigo) return null;

  window.history.replaceState({}, '', window.location.pathname);
  return MENSAJES[codigo] ?? `No se pudo completar el acceso (${codigo}).`;
}
