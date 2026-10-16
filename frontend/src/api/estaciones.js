/** Operaciones sobre el catalogo de estaciones. */
import { api } from './client.js';

export function listarEstaciones({ incluirInactivas = false } = {}) {
  const query = incluirInactivas ? '?incluir_inactivas=1' : '';
  return api.get(`/api/estaciones${query}`);
}

export function crearEstacion(datos) {
  return api.post('/api/estaciones', datos);
}

export function actualizarEstacion(id, datos) {
  return api.put(`/api/estaciones/${encodeURIComponent(id)}`, datos);
}

export function darDeBajaEstacion(id) {
  return api.del(`/api/estaciones/${encodeURIComponent(id)}`);
}

/**
 * Reenvia al gateway la lista vigente de nodos.
 *
 * Hace falta cuando una escritura anterior quedo bien en la base pero el broker
 * no estaba disponible para recibir la configuracion. Es idempotente: publica
 * el estado actual completo, no un cambio, asi que repetirla no rompe nada.
 */
export function republicarConfiguracion() {
  return api.post('/api/estaciones/configuracion');
}
