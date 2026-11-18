/**
 * Calibración de PM contra un equipo de referencia.
 *
 * El navegador nunca manda coeficientes: manda el CSV de la referencia y el
 * servidor los calcula, así una calibración guardada siempre sale de datos.
 */
import { api } from './client.js';

export function listarCalibraciones({ incluirAnuladas = false } = {}) {
  return api.get(`/api/calibraciones${incluirAnuladas ? '?incluir_anuladas=1' : ''}`);
}

/** Vista previa: compara crudo con los dos modelos, sin guardar. */
export function ajustarCalibracion(parametros) {
  return api.post('/api/calibraciones/ajustar', parametros);
}

/** Recalcula en el servidor y guarda el modelo elegido. */
export function guardarCalibracion(parametros) {
  return api.post('/api/calibraciones', parametros);
}

export function anularCalibracion(id) {
  return api.del(`/api/calibraciones/${id}`);
}
