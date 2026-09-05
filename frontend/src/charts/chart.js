/**
 * Creacion y actualizacion de graficos.
 *
 * Las librerias entran por import, no por <script> desde un CDN: quedan
 * empaquetadas con version fija, el sitio no depende de que un CDN externo este
 * disponible, y el build falla en vez de romperse en produccion si algo falta.
 */
import Chart from 'chart.js/auto';
import 'chartjs-adapter-luxon';

import { MAX_PUNTOS_VIVO } from '../config.js';
import { colorDeMetrica } from './thresholds.js';

/** Grafico de la tira en vivo de una metrica. */
export function crearGraficoVivo(canvas, { metrica, unidad }) {
  return new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: unidad,
          data: [],
          fill: true,
          backgroundColor: 'rgba(0, 123, 255, 0.05)',
          pointBackgroundColor: [],
          pointBorderColor: [],
          pointRadius: 1,
          pointHoverRadius: 7,
          borderWidth: 2,
          tension: 0.1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      // Guardado aca para que agregarPunto sepa que escala de color aplicar.
      metrica,
      scales: {
        y: { beginAtZero: false, title: { display: true, text: unidad } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'index', intersect: false },
      },
    },
  });
}

/**
 * Agrega un punto al grafico en vivo, manteniendo una ventana deslizante.
 * El color de cada punto depende de su propio valor, y la linea se dibuja con
 * un gradiente que recorre esos colores.
 */
export function agregarPunto(grafico, etiqueta, valor) {
  if (!grafico || valor === undefined || valor === null) return;

  const { labels, datasets } = grafico.data;
  const serie = datasets[0];
  const color = colorDeMetrica(grafico.options.metrica, valor);

  labels.push(etiqueta);
  serie.data.push(valor);
  serie.pointBackgroundColor.push(color);
  serie.pointBorderColor.push(color);

  while (labels.length > MAX_PUNTOS_VIVO) {
    labels.shift();
    serie.data.shift();
    serie.pointBackgroundColor.shift();
    serie.pointBorderColor.shift();
  }

  aplicarGradiente(grafico);
  grafico.update('none');
}

function aplicarGradiente(grafico) {
  const serie = grafico.data.datasets[0];
  const metrica = grafico.options.metrica;

  if (serie.data.length === 1) {
    serie.borderColor = serie.pointBackgroundColor[0];
    return;
  }
  if (serie.data.length < 2) return;

  const gradiente = grafico.ctx.createLinearGradient(0, 0, grafico.width, 0);
  serie.data.forEach((valor, indice) => {
    gradiente.addColorStop(indice / (serie.data.length - 1), colorDeMetrica(metrica, valor));
  });
  serie.borderColor = gradiente;
}

/** Grafico del modal de historial: eje temporal real, no etiquetas de texto. */
export function crearGraficoHistorial(canvas, { metrica, etiqueta }) {
  return new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [
        {
          label: etiqueta,
          data: [],
          borderColor: colorDeMetrica(metrica, 0),
          backgroundColor: 'transparent',
          fill: false,
          pointRadius: 1,
          pointHoverRadius: 7,
          borderWidth: 2,
          tension: 0.1,
          // Cada tramo se pinta segun el valor al que llega, asi la linea
          // cambia de color al cruzar un umbral.
          segment: {
            borderColor: (ctx) => colorDeMetrica(metrica, ctx.p1.parsed.y),
          },
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      metrica,
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'dd/MM/yyyy HH:mm',
            displayFormats: { hour: 'HH:mm', day: 'dd/MM', month: 'MM/yyyy' },
          },
          title: { display: true, text: 'Fecha y hora' },
          ticks: { autoSkip: true, maxRotation: 0, maxTicksLimit: 12 },
        },
        y: { title: { display: true, text: etiqueta } },
      },
      plugins: {
        legend: { display: true, position: 'top' },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2) ?? '-'}`,
          },
        },
      },
    },
  });
}

/** Reemplaza por completo la serie de un grafico (usado por el historial). */
export function reemplazarDatos(grafico, puntos) {
  if (!grafico) return;
  grafico.data.datasets[0].data = puntos;
  grafico.update();
}
