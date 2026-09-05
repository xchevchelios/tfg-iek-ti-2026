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
