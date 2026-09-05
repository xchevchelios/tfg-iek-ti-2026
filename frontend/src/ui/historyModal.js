/**
 * Modal de historial: serie temporal de una metrica de una estacion.
 *
 * Se construye una sola vez y se reutiliza. El grafico si se destruye y se
 * vuelve a crear en cada apertura, porque cambia la metrica y con ella la
 * escala de colores del eje.
 */
import { historial } from '../api/mediciones.js';
import { crearGraficoHistorial, reemplazarDatos } from '../charts/chart.js';
import { mostrarAviso } from './aviso.js';

const RANGOS = [
  { clave: '24h', etiqueta: 'Últimas 24 h' },
  { clave: '7d', etiqueta: 'Últimos 7 días' },
  { clave: '30d', etiqueta: 'Último mes' },
];

export function crearModalHistorial() {
  const fondo = document.createElement('div');
  fondo.className = 'modal-fondo';
  fondo.hidden = true;
  fondo.innerHTML = `
    <div class="modal-caja" role="dialog" aria-modal="true" aria-labelledby="modal-titulo">
      <button class="modal-cerrar" type="button" aria-label="Cerrar">&times;</button>
      <h2 id="modal-titulo"></h2>
      <div class="modal-controles">
        ${RANGOS.map(
          (r) => `<button class="rango-btn" type="button" data-rango="${r.clave}">${r.etiqueta}</button>`
        ).join('')}
      </div>
      <div class="modal-controles">
        <label for="fecha-inicio">Inicio:</label>
        <input type="date" id="fecha-inicio">
        <label for="fecha-fin">Fin:</label>
        <input type="date" id="fecha-fin">
        <button id="btn-rango-custom" type="button">Aplicar</button>
      </div>
      <div class="modal-grafico"><canvas></canvas></div>
      <p class="modal-estado" hidden></p>
    </div>
  `;
  document.body.appendChild(fondo);

  const caja = fondo.querySelector('.modal-caja');
  const titulo = fondo.querySelector('#modal-titulo');
  const lienzo = fondo.querySelector('canvas');
  const estado = fondo.querySelector('.modal-estado');
  const botonesRango = [...fondo.querySelectorAll('.rango-btn')];
  const fechaInicio = fondo.querySelector('#fecha-inicio');
  const fechaFin = fondo.querySelector('#fecha-fin');

  let grafico = null;
  let actual = null;

  function mostrarEstado(mensaje) {
    estado.textContent = mensaje ?? '';
    estado.hidden = !mensaje;
  }

  async function cargar(parametros) {
    if (!actual) return;
    mostrarEstado('Cargando…');
    try {
      const puntos = await historial({ ...actual, ...parametros });
      reemplazarDatos(grafico, puntos);
      mostrarEstado(puntos.length ? '' : 'No hay mediciones en el rango elegido.');
    } catch (error) {
      mostrarEstado(error.message);
    }
  }

  function marcarRango(boton) {
    botonesRango.forEach((b) => b.classList.toggle('activo', b === boton));
  }

  botonesRango.forEach((boton) => {
    boton.addEventListener('click', () => {
      marcarRango(boton);
      fechaInicio.value = '';
      fechaFin.value = '';
      cargar({ rango: boton.dataset.rango });
    });
  });

  fondo.querySelector('#btn-rango-custom').addEventListener('click', () => {
    const start = fechaInicio.value;
    const end = fechaFin.value;
    if (!start || !end) {
      mostrarAviso('Elegí una fecha de inicio y una de fin.', 'error');
      return;
    }
    if (start > end) {
      mostrarAviso('La fecha de inicio no puede ser posterior a la de fin.', 'error');
      return;
    }
    marcarRango(null);
    cargar({ start, end });
  });

  function cerrar() {
    fondo.hidden = true;
    if (grafico) {
      grafico.destroy();
      grafico = null;
    }
    actual = null;
    marcarRango(null);
    fechaInicio.value = '';
    fechaFin.value = '';
    mostrarEstado(null);
  }

  fondo.querySelector('.modal-cerrar').addEventListener('click', cerrar);
  fondo.addEventListener('click', (evento) => {
    if (evento.target === fondo) cerrar();
  });
  document.addEventListener('keydown', (evento) => {
    if (evento.key === 'Escape' && !fondo.hidden) cerrar();
  });

  return {
    abrir({ estacion, nombreEstacion, metrica, etiqueta }) {
      actual = { estacion, metrica };
      titulo.textContent = `Historial de ${etiqueta} — ${nombreEstacion}`;
      fondo.hidden = false;
      caja.scrollTop = 0;

      if (grafico) grafico.destroy();
      grafico = crearGraficoHistorial(lienzo, { metrica, etiqueta });

      const porDefecto = botonesRango[0];
      marcarRango(porDefecto);
      cargar({ rango: porDefecto.dataset.rango });
    },
    cerrar,
  };
}
