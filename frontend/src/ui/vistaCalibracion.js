/**
 * Pestaña "Calibración" del panel (solo admin): PM2.5, PM10, temperatura y humedad.
 *
 * Flujo: se elige la estación co-ubicada con el equipo de referencia, se sube
 * el CSV y el servidor devuelve la comparación crudo / lineal / con humedad
 * sobre un tramo de validación. Recién ahí se guarda el modelo elegido, que el
 * servidor vuelve a calcular a partir de los mismos datos.
 */
import Chart from 'chart.js/auto';

import {
  ajustarCalibracion,
  anularCalibracion,
  aplicarHumedadAlta,
  guardarCalibracion,
  listarCalibraciones,
} from '../api/calibracion.js';
import { listarEstaciones } from '../api/estaciones.js';
import { mostrarAviso } from './aviso.js';

const NOMBRES_MODELO = {
  lineal: 'Lineal',
  humedad: 'Lineal + humedad',
  humedad_exp: 'Humedad alta (FIUNA)',
};
const NOMBRES_METRICA = {
  pm25: 'PM2.5',
  pm10: 'PM10',
  temperatura: 'Temperatura',
  humedad: 'Humedad',
};
const UNIDADES = { pm25: 'µg/m³', pm10: 'µg/m³', temperatura: '°C', humedad: '%' };
const ES_PM = (metrica) => metrica === 'pm25' || metrica === 'pm10';

/** Mismo recorte al rango físico que hace el servidor. */
function recortar(metrica, valor) {
  if (metrica === 'temperatura') return valor;
  if (metrica === 'humedad') return Math.min(100, Math.max(0, valor));
  return Math.max(0, valor);
}

let manejarError = (error) => mostrarAviso(error.message, 'error');
let ultimoAjuste = null; // { parametros, resultado }
let grafico = null;

const $ = (id) => document.getElementById(id);

function num(valor, decimales = 2) {
  return valor === null || valor === undefined ? '—' : Number(valor).toFixed(decimales);
}

function formula({ a, b, c, modelo }) {
  if (modelo === 'humedad_exp') return `x ÷ e^(${a}·(HR−${b})) si HR > ${c} %`;
  const signo = (v) => (v < 0 ? '−' : '+');
  let texto = `${num(a, 2)} ${signo(b)} ${num(Math.abs(b), 3)}·x`;
  if (c) texto += ` ${signo(c)} ${num(Math.abs(c), 3)}·HR`;
  return texto;
}

function fechaCorta(iso) {
  if (!iso || iso.startsWith('1970')) return 'todo el historial';
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? iso : fecha.toLocaleString('es-PY');
}

function celda(fila, texto, clase) {
  const td = document.createElement('td');
  td.textContent = texto;
  if (clase) td.className = clase;
  fila.appendChild(td);
  return td;
}

// =================================================================
// Ajuste (vista previa)
// =================================================================

function filaModelo(cuerpo, nombre, coefTexto, m, { mejor = false, modelo = null } = {}) {
  const fila = document.createElement('tr');
  celda(fila, nombre);
  celda(fila, coefTexto, 'celda-formula');
  celda(fila, num(m?.r2, 3));
  celda(fila, num(m?.rmse), mejor ? 'cal-mejor' : '');
  celda(fila, num(m?.mae));
  celda(fila, num(m?.sesgo));
  const acciones = celda(fila, '');
  if (modelo) {
    const boton = document.createElement('button');
    boton.type = 'button';
    boton.textContent = 'Guardar';
    boton.addEventListener('click', () => guardar(modelo));
    acciones.appendChild(boton);
  }
  cuerpo.appendChild(fila);
}

function dibujarDispersion(resultado, mejor) {
  if (grafico) grafico.destroy();
  const { pares, modelos } = resultado;
  const coef = mejor ? modelos[mejor].coef : null;
  const valores = pares.flatMap((p) => [p.sds, p.ref]);
  const maximo = Math.max(1, ...valores) * 1.05;
  // La temperatura no arranca en 0; las concentraciones y la HR si.
  const minimo = resultado.metrica === 'temperatura' ? Math.floor(Math.min(...valores) - 1) : 0;

  const conjuntos = [
    {
      label: 'Sensor sin calibrar',
      data: pares.map((p) => ({ x: p.ref, y: p.sds })),
      backgroundColor: 'rgba(220, 53, 69, 0.55)',
      pointRadius: 3,
    },
  ];
  if (coef) {
    conjuntos.push({
      label: `Calibrado (${NOMBRES_MODELO[mejor].toLowerCase()})`,
      data: pares.map((p) => ({
        x: p.ref,
        y: recortar(resultado.metrica, coef.a + coef.b * p.sds + coef.c * p.hr),
      })),
      backgroundColor: 'rgba(0, 123, 255, 0.6)',
      pointRadius: 3,
    });
  }
  conjuntos.push({
    label: 'Ideal (1:1)',
    type: 'line',
    data: [{ x: minimo, y: minimo }, { x: maximo, y: maximo }],
    borderColor: '#6c757d',
    borderDash: [6, 4],
    borderWidth: 1,
    pointRadius: 0,
  });

  const unidad = `${NOMBRES_METRICA[resultado.metrica]} (${UNIDADES[resultado.metrica]})`;
  grafico = new Chart($('cal-dispersion').getContext('2d'), {
    type: 'scatter',
    data: { datasets: conjuntos },
    options: {
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { min: minimo, max: maximo, title: { display: true, text: `Referencia ${unidad}` } },
        y: { min: minimo, max: maximo, title: { display: true, text: `Nodo ${unidad}` } },
      },
    },
  });
}

function mostrarResultado(resultado) {
  const { resumen, referencia, modelos, metrica } = resultado;
  $('cal-resultado').hidden = false;

  $('cal-resumen').textContent =
    `${referencia.muestras} muestras de referencia (${fechaCorta(referencia.desde)} – ` +
    `${fechaCorta(referencia.hasta)}), ${resumen.mediciones_sensor} mediciones del nodo. ` +
    `${resumen.ventanas_comunes} ventanas en común` +
    (ES_PM(metrica) ? `, ${resumen.descartadas_humedad} descartadas por humedad` : '') +
    `: ${resumen.pares} pares para el ajuste.`;

  const validos = Object.values(modelos).filter((m) => !m.error);
  const mejor = validos.length
    ? validos.reduce((a, b) => (b.validacion.calibrado.rmse < a.validacion.calibrado.rmse ? b : a))
        .modelo
    : null;

  const cuerpo = $('cal-modelos');
  cuerpo.textContent = '';
  const base = validos[0]?.validacion.crudo;
  filaModelo(cuerpo, 'Sin calibrar', 'x', base);
  for (const modelo of Object.keys(modelos)) {
    const m = modelos[modelo];
    if (m.error) {
      const fila = document.createElement('tr');
      celda(fila, NOMBRES_MODELO[modelo]);
      const td = celda(fila, m.error);
      td.colSpan = 6;
      cuerpo.appendChild(fila);
      continue;
    }
    filaModelo(cuerpo, NOMBRES_MODELO[modelo], formula(m.coef), m.validacion.calibrado, {
      mejor: modelo === mejor,
      modelo,
    });
  }

  const tipo = validos[0]?.validacion.tipo;
  $('cal-tipo-validacion').textContent =
    tipo === 'temporal'
      ? `Métricas sobre las últimas ${validos[0].validacion.n} ventanas, que no se usaron ` +
        'para ajustar. Los coeficientes que se guardan se ajustan con todas.'
      : 'Hay pocas ventanas para separar un tramo de validación: las métricas son sobre ' +
        'los mismos datos del ajuste y resultan optimistas. Conviene juntar más días.';

  if (resultado.pares.length) dibujarDispersion(resultado, mejor);
}

async function ajustar(evento) {
  evento.preventDefault();
  const formulario = evento.currentTarget;
  const archivo = $('cal-archivo').files[0];
  if (!archivo) return;

  const datos = new FormData(formulario);
  const parametros = {
    estacion: datos.get('estacion'),
    metrica: datos.get('metrica'),
    ventana_min: Number(datos.get('ventana_min')),
    hr_max:
      !ES_PM(datos.get('metrica')) || datos.get('hr_max') === ''
        ? null
        : Number(datos.get('hr_max')),
    utc_offset_horas: datos.get('utc_offset_horas'),
    csv: await archivo.text(),
  };

  const boton = formulario.querySelector('button[type="submit"]');
  boton.disabled = true;
  try {
    const resultado = await ajustarCalibracion(parametros);
    ultimoAjuste = { parametros, resultado };
    mostrarResultado(resultado);
  } catch (error) {
    $('cal-resultado').hidden = true;
    manejarError(error);
  } finally {
    boton.disabled = false;
  }
}

async function guardar(modelo) {
  if (!ultimoAjuste) return;
  const desdeLocal = $('cal-desde').value;
  const { parametros, resultado } = ultimoAjuste;
  const coef = resultado.modelos[modelo].coef;
  const cuando = desdeLocal ? new Date(desdeLocal).toLocaleString('es-PY') : 'todo el historial';

  const confirmado = window.confirm(
    `Guardar ${NOMBRES_MODELO[modelo].toLowerCase()} para ${parametros.estacion} ` +
      `(${NOMBRES_METRICA[parametros.metrica]}):\n  ${formula(coef)}\n` +
      `Vigente desde: ${cuando}.\n\nEl dashboard y el historial pasan a mostrar el valor calibrado.`
  );
  if (!confirmado) return;

  try {
    await guardarCalibracion({
      ...parametros,
      modelo,
      // datetime-local no trae zona: el navegador la interpreta como local.
      valida_desde: desdeLocal ? new Date(desdeLocal).toISOString() : null,
      notas: $('cal-notas').value,
    });
    mostrarAviso('Calibración guardada.', 'ok');
    await cargarCalibraciones();
  } catch (error) {
    manejarError(error);
  }
}

// =================================================================
// Listado
// =================================================================

async function cargarCalibraciones() {
  const estado = $('estado-calibraciones');
  try {
    const lista = await listarCalibraciones({
      incluirAnuladas: $('cal-ver-anuladas').checked,
    });
    const cuerpo = $('tabla-calibraciones');
    cuerpo.textContent = '';
    for (const cal of lista) {
      const fila = document.createElement('tr');
      if (cal.anulada) fila.className = 'fila--inactiva';
      celda(fila, String(cal.id));
      celda(fila, cal.station_id);
      celda(fila, NOMBRES_METRICA[cal.metrica]);
      celda(fila, NOMBRES_MODELO[cal.modelo]);
      celda(fila, formula(cal), 'celda-formula');
      celda(fila, fechaCorta(cal.valida_desde), 'celda-fecha');
      celda(fila, String(cal.n_pares));
      celda(fila, `${num(cal.rmse_crudo)} → ${num(cal.rmse)}`);
      celda(fila, num(cal.r2, 3));
      const por = celda(fila, cal.email ?? '—');
      if (cal.notas) por.title = cal.notas;
      const acciones = celda(fila, cal.anulada ? 'anulada' : '', 'celda-estado');
      if (!cal.anulada) {
        const boton = document.createElement('button');
        boton.type = 'button';
        boton.textContent = 'Anular';
        boton.addEventListener('click', async () => {
          if (!window.confirm(`¿Anular la calibración #${cal.id}? Vuelve a regir la anterior.`)) {
            return;
          }
          try {
            await anularCalibracion(cal.id);
            await cargarCalibraciones();
          } catch (error) {
            manejarError(error);
          }
        });
        acciones.appendChild(boton);
      }
      cuerpo.appendChild(fila);
    }
    estado.hidden = lista.length > 0;
    estado.textContent = 'Sin calibraciones: el sistema muestra los valores crudos del sensor.';
  } catch (error) {
    estado.hidden = false;
    estado.textContent = error.message;
  }
}

async function cargarEstaciones() {
  const select = $('cal-estacion');
  const elegida = select.value;
  try {
    const estaciones = await listarEstaciones();
    select.textContent = '';
    for (const e of estaciones) {
      const opcion = document.createElement('option');
      opcion.value = e.id;
      opcion.textContent = `${e.nombre} (${e.id})`;
      select.appendChild(opcion);
    }
    if (elegida) select.value = elegida;
  } catch (error) {
    manejarError(error);
  }
}

async function humedadAlta() {
  const estacion = $('cal-estacion').value;
  if (!estacion) return;
  const confirmado = window.confirm(
    `Aplicar a ${estacion} la corrección por humedad alta de la red FIUNA ` +
      '(PM2.5 y PM10):\n  x ÷ e^(0,014·(HR−40)) cuando la HR supera 80 %.\n\n' +
      'Con humedad menor el valor no cambia. Usa la humedad ya calibrada de la estación.'
  );
  if (!confirmado) return;
  try {
    await aplicarHumedadAlta(estacion);
    mostrarAviso('Corrección por humedad alta aplicada.', 'ok');
    await cargarCalibraciones();
  } catch (error) {
    manejarError(error);
  }
}

export function iniciarVistaCalibracion(opciones = {}) {
  if (opciones.manejarError) manejarError = opciones.manejarError;
  $('form-calibracion').addEventListener('submit', ajustar);
  $('cal-humedad-alta').addEventListener('click', humedadAlta);
  // El descarte por humedad alta es solo para PM: con temperatura y humedad
  // interesa calibrar todo el rango.
  const alternarHr = () => {
    $('cal-grupo-hr').hidden = !ES_PM($('cal-metrica').value);
  };
  $('cal-metrica').addEventListener('change', alternarHr);
  alternarHr();
  $('form-cal-guardar').addEventListener('submit', (e) => e.preventDefault());
  $('cal-ver-anuladas').addEventListener('change', cargarCalibraciones);
}

export function mostrarVistaCalibracion() {
  cargarEstaciones();
  cargarCalibraciones();
}
