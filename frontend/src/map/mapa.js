/**
 * Mapa de las estaciones.
 *
 * Muestra un marcador por estacion con coordenadas cargadas, coloreado segun el
 * nivel de PM2.5 de su ultima medicion.
 *
 * Deliberadamente NO interpola una superficie de calor entre los nodos. Con la
 * densidad actual de la red (dos estaciones), cualquier degradado entre puntos
 * seria una invencion del algoritmo y no una medicion: se estaria afirmando
 * visualmente algo que los datos no sostienen. La interpolacion queda como
 * trabajo futuro, condicionada a una red mas densa.
 */
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { MAPA_CENTRO, MAPA_ZOOM } from '../config.js';
import { colorDeMetrica, nivelDeMetrica } from '../charts/thresholds.js';

const RADIO_BASE = 12;

export function crearMapa(contenedor) {
  const mapa = L.map(contenedor).setView(MAPA_CENTRO, MAPA_ZOOM);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; colaboradores de OpenStreetMap',
  }).addTo(mapa);

  const capaMarcadores = L.layerGroup().addTo(mapa);

  return {
    /**
     * @param {Array<{id, nombre, latitud, longitud}>} estaciones
     * @param {Record<string, object>} ultimaPorEstacion  ultima medicion de cada estacion
     */
    actualizar(estaciones, ultimaPorEstacion = {}) {
      capaMarcadores.clearLayers();

      const ubicadas = estaciones.filter(
        (e) => typeof e.latitud === 'number' && typeof e.longitud === 'number'
      );

      if (ubicadas.length === 0) return { ubicadas: 0, total: estaciones.length };

      const puntos = [];
      for (const estacion of ubicadas) {
        const medicion = ultimaPorEstacion[estacion.id];
        const pm25 = medicion?.pm25 ?? null;
        const color = colorDeMetrica('pm25', pm25);

        const marcador = L.circleMarker([estacion.latitud, estacion.longitud], {
          radius: RADIO_BASE,
          color: '#ffffff',
          weight: 2,
          fillColor: color,
          fillOpacity: 0.85,
        });

        marcador.bindPopup(construirPopup(estacion, medicion));
        marcador.bindTooltip(estacion.nombre, { direction: 'top' });
        marcador.addTo(capaMarcadores);
        puntos.push([estacion.latitud, estacion.longitud]);
      }

      if (puntos.length === 1) {
        mapa.setView(puntos[0], MAPA_ZOOM);
      } else {
        mapa.fitBounds(L.latLngBounds(puntos).pad(0.35));
      }

      return { ubicadas: ubicadas.length, total: estaciones.length };
    },

    /** Leaflet necesita esto si el contenedor cambia de tamanio o se muestra tarde. */
    reajustar() {
      mapa.invalidateSize();
    },
  };
}

function construirPopup(estacion, medicion) {
  if (!medicion) {
    return `<strong>${escapar(estacion.nombre)}</strong><br><em>Sin mediciones registradas.</em>`;
  }

  const fecha = medicion.timestamp
    ? new Date(medicion.timestamp).toLocaleString('es-PY')
    : 'sin fecha';

  const fila = (etiqueta, valor, unidad) =>
    valor === null || valor === undefined
      ? `<tr><th>${etiqueta}</th><td>—</td></tr>`
      : `<tr><th>${etiqueta}</th><td>${Number(valor).toFixed(1)} ${unidad}</td></tr>`;

  return `
    <strong>${escapar(estacion.nombre)}</strong>
    <div class="popup-nivel">PM2.5: ${nivelDeMetrica('pm25', medicion.pm25)}</div>
    <table class="popup-tabla">
      ${fila('PM2.5', medicion.pm25, 'µg/m³')}
      ${fila('PM10', medicion.pm10, 'µg/m³')}
      ${fila('Temp.', medicion.temperatura, '°C')}
      ${fila('Humedad', medicion.humedad, '%')}
    </table>
    <small>Última lectura: ${escapar(fecha)}</small>
  `;
}

function escapar(texto) {
  const nodo = document.createElement('span');
  nodo.textContent = texto ?? '';
  return nodo.innerHTML;
}
