/**
 * Pestaña "Fiabilidad": cuánto falla el enlace LoRa de cada nodo, a partir de
 * las rondas que registra la ingesta, filtrando por versión de firmware del
 * gateway (por defecto 2.4.0 en adelante).
 */
import Chart from 'chart.js/auto';

import { leerFiabilidad } from '../api/gateway.js';
import { mostrarAviso } from './aviso.js';

const FIRMWARE_POR_DEFECTO = '2.4.0';
const COLORES = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf'];

let manejarError = (error) => mostrarAviso(error.message, 'error');
let grafico = null;
let iniciada = false;

const $ = (id) => document.getElementById(id);
const pct = (v) => (v === null || v === undefined ? '—' : `${String(v).replace('.', ',')} %`);
const fecha = (iso) => (iso ? new Date(iso).toLocaleString('es-PY') : '—');
const nombreNodo = (n) => n.nombre ?? `Nodo ${n.direccion}`;

function nivel(p) {
  if (p === null || p === undefined) return '';
  if (p < 5) return 'bien';
  if (p < 15) return 'regular';
  return 'mal';
}

function celda(fila, texto) {
  const td = document.createElement('td');
  td.textContent = texto;
  fila.appendChild(td);
}

function conteo(parte, total) {
  return `${parte} (${pct(total ? Math.round((1000 * parte) / total) / 10 : null)})`;
}

function llenarVersiones(versiones) {
  const select = $('fia-firmware');
  const elegida = select.value;
  select.textContent = '';
  const todas = document.createElement('option');
  todas.value = '';
  todas.textContent = 'Todas las versiones';
  select.appendChild(todas);
  for (const v of versiones) {
    if (!v.firmware) continue;
    const op = document.createElement('option');
    op.value = v.firmware;
    op.textContent = v.firmware;
    select.appendChild(op);
  }
  const hay = (x) => [...select.options].some((o) => o.value === x);
  if (elegida !== undefined && hay(elegida) && iniciada) select.value = elegida;
  else if (hay(FIRMWARE_POR_DEFECTO)) select.value = FIRMWARE_POR_DEFECTO;
}

function dibujar(datos) {
  const { gateway, nodos, serie, versiones } = datos;

  $('fia-periodo').textContent = gateway
    ? `Desde ${fecha(gateway.desde)} hasta ${fecha(gateway.hasta)}: ${gateway.registradas} rondas ` +
      `registradas, ${gateway.sin_reporte} que el gateway no llegó a reportar ` +
      `(${pct(gateway.pct_sin_reporte)}), ${gateway.arranques} arranques del gateway.`
    : '';

  const tarjetas = $('fia-tarjetas');
  tarjetas.textContent = '';
  for (const n of nodos) {
    const t = document.createElement('div');
    t.className = 'fia-tarjeta';
    t.dataset.nivel = nivel(n.pct_sin_respuesta);
    t.innerHTML = `
      <div class="fia-tarjeta__nombre"></div>
      <div class="fia-tarjeta__valor"></div>
      <div class="fia-tarjeta__detalle"></div>`;
    t.querySelector('.fia-tarjeta__nombre').textContent = `${nombreNodo(n)} — sin respuesta`;
    t.querySelector('.fia-tarjeta__valor').textContent = pct(n.pct_sin_respuesta);
    t.querySelector('.fia-tarjeta__detalle').textContent =
      `${n.sin_respuesta} de ${n.total} rondas · con dato ${pct(n.pct_fresca)}`;
    tarjetas.appendChild(t);
  }

  const cuerpo = $('fia-nodos');
  cuerpo.textContent = '';
  for (const n of nodos) {
    const fila = document.createElement('tr');
    celda(fila, `${nombreNodo(n)} (${n.direccion})`);
    celda(fila, String(n.total));
    celda(fila, conteo(n.fresca, n.total));
    celda(fila, conteo(n.vieja, n.total));
    celda(fila, conteo(n.sin_respuesta, n.total));
    celda(fila, conteo(n.sin_desenlace, n.total));
    celda(fila, String(n.racha_max));
    celda(fila, n.rssi_medio === null ? '—' : `${n.rssi_medio} dBm`);
    cuerpo.appendChild(fila);
  }

  const cuerpoV = $('fia-versiones');
  cuerpoV.textContent = '';
  for (const v of versiones) {
    const fila = document.createElement('tr');
    if (!datos.incluidas.includes(v.firmware)) fila.className = 'fila--inactiva';
    celda(fila, v.firmware ?? 'sin dato');
    celda(fila, fecha(v.desde));
    celda(fila, fecha(v.hasta));
    celda(fila, String(v.rondas));
    celda(fila, pct(v.pct_sin_respuesta));
    celda(fila, pct(v.pct_fresca));
    cuerpoV.appendChild(fila);
  }

  if (grafico) grafico.destroy();
  grafico = new Chart($('fia-grafico').getContext('2d'), {
    type: 'line',
    data: {
      labels: serie.periodos,
      datasets: nodos.map((n, i) => ({
        label: nombreNodo(n),
        data: serie.nodos[String(n.direccion)],
        borderColor: COLORES[i % COLORES.length],
        backgroundColor: COLORES[i % COLORES.length],
        spanGaps: false,
        tension: 0.2,
        pointRadius: serie.periodos.length > 60 ? 0 : 3,
      })),
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: '% sin respuesta' } },
        x: { ticks: { maxTicksLimit: 12 } },
      },
      plugins: {
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${pct(c.parsed.y)}` } },
      },
    },
  });
}

async function cargar() {
  const estado = $('fia-estado');
  try {
    const datos = await leerFiabilidad({
      firmwareMin: iniciada ? $('fia-firmware').value : FIRMWARE_POR_DEFECTO,
      agrupar: $('fia-agrupar').value,
    });
    llenarVersiones(datos.versiones);
    iniciada = true;
    if (!datos.nodos.length) {
      $('fia-contenido').hidden = true;
      estado.hidden = false;
      estado.textContent = datos.firmware_min
        ? `Todavía no hay rondas registradas con firmware ${datos.firmware_min} o posterior.`
        : 'Todavía no hay rondas registradas.';
      return;
    }
    estado.hidden = true;
    $('fia-contenido').hidden = false;
    dibujar(datos);
  } catch (error) {
    manejarError(error);
  }
}

export function iniciarVistaFiabilidad(opciones = {}) {
  if (opciones.manejarError) manejarError = opciones.manejarError;
  $('form-fiabilidad').addEventListener('submit', (e) => {
    e.preventDefault();
    cargar();
  });
  $('fia-firmware').addEventListener('change', cargar);
  $('fia-agrupar').addEventListener('change', cargar);
}

export function mostrarVistaFiabilidad() {
  cargar();
}
