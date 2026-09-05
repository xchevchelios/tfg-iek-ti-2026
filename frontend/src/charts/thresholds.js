/**
 * Umbrales de calidad del aire y el color que le corresponde a cada valor.
 *
 * Los cortes de PM2.5 y PM10 siguen las Guias de Calidad del Aire de la OMS
 * (2021) para media de 24 horas: PM2.5 15 µg/m³, PM10 45 µg/m³. El segundo
 * corte marca el nivel a partir del cual la exposicion se considera claramente
 * elevada.
 */
export const COLOR_VERDE = '#28a745';
export const COLOR_AMARILLO = '#ffc107';
export const COLOR_ROJO = '#dc3545';
export const COLOR_CELESTE = '#17a2b8';
export const COLOR_NARANJA = '#fd7e14';
export const COLOR_NEUTRO = '#6c757d';

const escalas = {
  pm25: (valor) => (valor <= 15 ? COLOR_VERDE : valor <= 35 ? COLOR_AMARILLO : COLOR_ROJO),
  pm10: (valor) => (valor <= 45 ? COLOR_VERDE : valor <= 75 ? COLOR_AMARILLO : COLOR_ROJO),
  temperatura: (valor) =>
    valor < 15 ? COLOR_CELESTE : valor <= 25 ? COLOR_VERDE : COLOR_NARANJA,
  humedad: (valor) => (valor < 30 ? COLOR_AMARILLO : valor <= 60 ? COLOR_VERDE : COLOR_CELESTE),
};

/** Etiqueta legible del nivel, para el mapa y los tooltips. */
const niveles = {
  pm25: (valor) => (valor <= 15 ? 'Bueno' : valor <= 35 ? 'Moderado' : 'Elevado'),
  pm10: (valor) => (valor <= 45 ? 'Bueno' : valor <= 75 ? 'Moderado' : 'Elevado'),
};

export function colorDeMetrica(metrica, valor = 0) {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return COLOR_NEUTRO;
  const escala = escalas[metrica];
  return escala ? escala(valor) : '#007bff';
}

export function nivelDeMetrica(metrica, valor) {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return 'Sin datos';
  const nivel = niveles[metrica];
  return nivel ? nivel(valor) : '';
}
