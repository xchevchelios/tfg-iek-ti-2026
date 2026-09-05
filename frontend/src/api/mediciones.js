/** Consultas de mediciones: lecturas recientes e historial. */
import { api } from './client.js';

/** Ultimas mediciones de cada estacion activa, agrupadas por id de estacion. */
export function ultimasMediciones(limite = 50) {
  return api.get(`/api/mediciones/ultimas?limite=${limite}`);
}

/**
 * Serie historica de una metrica.
 * @param {object} p
 * @param {string} p.estacion  id de la estacion
 * @param {string} p.metrica   pm25 | pm10 | temperatura | humedad
 * @param {string} [p.rango]   24h | 7d | 30d
 * @param {string} [p.start]   fecha ISO (yyyy-mm-dd), junto con end
 * @param {string} [p.end]
 */
export function historial({ estacion, metrica, rango, start, end }) {
  const params = new URLSearchParams({ estacion, metrica });
  if (rango) {
    params.set('rango', rango);
  } else {
    params.set('start', start);
    params.set('end', end);
  }
  return api.get(`/api/historial?${params}`);
}
