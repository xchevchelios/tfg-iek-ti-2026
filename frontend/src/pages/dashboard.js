/**
 * Dashboard: arma la vista a partir de las estaciones que existen en la base y
 * la mantiene actualizada con los mensajes que llegan por MQTT.
 */
import { agregarPunto } from '../charts/chart.js';
import { listarEstaciones } from '../api/estaciones.js';
import { ultimasMediciones } from '../api/mediciones.js';
import { conectarMqtt } from '../api/mqtt.js';
import { METRICAS } from '../config.js';
import { crearMapa } from '../map/mapa.js';
import { crearModalHistorial } from '../ui/historyModal.js';
import { crearSeccionEstacion } from '../ui/stationSection.js';
import '../styles/base.css';
import '../styles/dashboard.css';

const contenedorEstaciones = document.getElementById('estaciones');
const contenedorMapa = document.getElementById('mapa');
const notaMapa = document.getElementById('mapa-nota');
const indicador = document.getElementById('estado-conexion');

/** estacionId -> { estacion, graficos } */
const secciones = new Map();
/** estacionId -> ultima medicion conocida, para los popups del mapa */
const ultimaPorEstacion = {};

let mapa = null;
let modal = null;
let recargandoEstaciones = false;

function mostrarEstadoConexion(estado, detalle) {
  const textos = {
    conectado: 'En vivo',
    reconectando: 'Reconectando…',
    desconectado: 'Sin conexión en vivo',
    error: detalle ? `Error de conexión: ${detalle}` : 'Error de conexión',
  };
  indicador.dataset.estado = estado;
  indicador.textContent = textos[estado] ?? estado;
}

function horaLegible(timestamp) {
  const fecha = new Date(timestamp);
  return Number.isNaN(fecha.getTime())
    ? String(timestamp ?? '')
    : fecha.toLocaleTimeString('es-PY');
}

function aplicarMedicion(estacionId, medicion) {
  const seccion = secciones.get(estacionId);
  if (!seccion) return false;

  const etiqueta = horaLegible(medicion.timestamp);
  for (const { clave } of METRICAS) {
    agregarPunto(seccion.graficos[clave], etiqueta, medicion[clave]);
  }
  ultimaPorEstacion[estacionId] = medicion;
  return true;
}

function renderizarEstaciones(estaciones) {
  contenedorEstaciones.textContent = '';
  secciones.clear();

  if (estaciones.length === 0) {
    contenedorEstaciones.innerHTML = `
      <div class="vacio">
        <p>No hay estaciones registradas todavía.</p>
        <p><a href="/gestion.html">Dar de alta la primera estación</a></p>
      </div>`;
    return;
  }

  for (const estacion of estaciones) {
    const { elemento, graficos } = crearSeccionEstacion(estacion, {
      alPedirHistorial: (estacionId, metrica, etiquetaMetrica) =>
        modal.abrir({
          estacion: estacionId,
          nombreEstacion: estacion.nombre,
          metrica,
          etiqueta: etiquetaMetrica,
        }),
    });
    contenedorEstaciones.appendChild(elemento);
    secciones.set(estacion.id, { estacion, graficos });
  }
}

function actualizarMapa(estaciones) {
  const resumen = mapa.actualizar(estaciones, ultimaPorEstacion);
  mapa.reajustar();

  if (!resumen || resumen.ubicadas === 0) {
    notaMapa.textContent =
      'Ninguna estación tiene coordenadas cargadas todavía. Se pueden completar desde la página de gestión.';
    notaMapa.hidden = false;
  } else if (resumen.ubicadas < resumen.total) {
    notaMapa.textContent = `${resumen.total - resumen.ubicadas} de ${resumen.total} estaciones no tienen coordenadas cargadas.`;
    notaMapa.hidden = false;
  } else {
    notaMapa.hidden = true;
  }
}

async function cargarTodo() {
  const estaciones = await listarEstaciones();
  renderizarEstaciones(estaciones);

  const porEstacion = await ultimasMediciones(50);
  for (const [estacionId, mediciones] of Object.entries(porEstacion)) {
    for (const medicion of mediciones) {
      aplicarMedicion(estacionId, medicion);
    }
  }

  actualizarMapa(estaciones);
}

/**
 * Si llega un mensaje de una estacion que el dashboard no conoce, es un nodo
 * nuevo: el servicio de ingesta ya lo dio de alta en la base, asi que alcanza
 * con volver a pedir la lista y rearmar la vista.
 */
async function recargarPorEstacionDesconocida() {
  if (recargandoEstaciones) return;
  recargandoEstaciones = true;
  try {
    await cargarTodo();
  } catch (error) {
    console.error('No se pudo recargar la lista de estaciones:', error);
  } finally {
    recargandoEstaciones = false;
  }
}

async function iniciar() {
  modal = crearModalHistorial();
  mapa = crearMapa(contenedorMapa);

  try {
    await cargarTodo();
  } catch (error) {
    contenedorEstaciones.innerHTML = `<div class="vacio vacio--error"><p>${error.message}</p></div>`;
    console.error(error);
  }

  conectarMqtt({
    alCambiarEstado: mostrarEstadoConexion,
    alRecibir: (estacionId, medicion) => {
      const aplicada = aplicarMedicion(estacionId, medicion);
      if (!aplicada) {
        recargarPorEstacionDesconocida();
        return;
      }
      mapa.actualizar([...secciones.values()].map((s) => s.estacion), ultimaPorEstacion);
    },
  });
}

iniciar();
