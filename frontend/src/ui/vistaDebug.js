/**
 * Debug LoRa (pestania Gateway): prueba de comunicacion con un nodo.
 *
 * El gateway (firmware 2.7.0 en adelante) pausa las rondas y le pide el dato
 * a un nodo cada `periodo_ms`, esperando hasta `timeout_ms`, como el sketch
 * de prueba "pedir dato por LoRa". Como no hay monitor serie, cada pedido se
 * publica en gateway/debug/log y el estado, retenido, en gateway/debug/estado.
 *
 * Igual que los parametros: el pedido de inicio o fin pasa por la API (valida
 * y deja la bitacora) y el navegador solo lee del broker.
 */
import { iniciarDebugLora } from '../api/gateway.js';
import { alRecibirMensaje, ultimoMensaje } from '../api/mqttPanel.js';
import { mostrarAviso } from './aviso.js';

const TOPIC_LOG = 'gateway/debug/log';
const TOPIC_ESTADO = 'gateway/debug/estado';
const MAX_LINEAS = 300;

const $ = (id) => document.getElementById(id);
let alError = (error) => mostrarAviso(error.message, 'error');
const lineas = [];

const hora = (fecha) => fecha.toLocaleTimeString('es-PY', { hour12: false });

function leerEstado() {
  const m = ultimoMensaje(TOPIC_ESTADO);
  if (!m) return null;
  try {
    return JSON.parse(m.texto);
  } catch {
    return null;
  }
}

function renderEstado() {
  const e = leerEstado();
  const caja = $('dbg-estado');
  let texto = 'Inactivo';
  let tipo = '';
  if (e?.activo) {
    texto = `Activo: estación ${e.nodo}, un pedido cada ${e.periodo_ms} ms (timeout ${e.timeout_ms} ms)`;
    if (e.pedidos) texto += ` · respondió ${e.respuestas} de ${e.pedidos}`;
    if (Number.isFinite(e.restante_s)) texto += ` · termina en ${Math.ceil(e.restante_s / 60)} min`;
    texto += ' · rondas en pausa';
    tipo = 'activo';
  } else if (e?.esperando_ronda) {
    texto = `Esperando que termine la ronda en curso para empezar con la estación ${e.nodo}…`;
    tipo = 'pendiente';
  } else if (e?.fin) {
    texto = `Terminado (${e.fin})`;
    if (e.pedidos) texto += `: respondió ${e.respuestas} de ${e.pedidos}`;
  }
  if (e?.rechazo) {
    texto += ` · último pedido rechazado por el gateway: ${e.rechazo}`;
    tipo = 'error';
  }
  caja.textContent = texto;
  caja.dataset.tipo = tipo;
  $('dbg-detener').disabled = !(e?.activo || e?.esperando_ronda);
}

/** Una linea del log, todo como texto (lo que llega del broker no es HTML). */
function filaDe(mensaje) {
  let d;
  try {
    d = JSON.parse(mensaje.texto);
  } catch {
    d = { tipo: 'texto', crudo: mensaje.texto };
  }
  const celdas = {
    hora: hora(mensaje.hora),
    evento: {
      respuesta: 'Respondió', sin_respuesta: 'Sin respuesta', otro_nodo: 'Otro nodo / tarde',
      inicio: 'Inicio', fin: 'Fin', esperando_ronda: 'Esperando ronda',
    }[d.tipo] ?? d.tipo,
    pedido: d.pedido ?? '',
    nodo: d.nodo ?? '',
    ms: d.tipo === 'respuesta' ? `${d.ms} ms` : d.tipo === 'sin_respuesta' ? `> ${d.timeout_ms} ms` : '',
    rssi: Number.isFinite(d.rssi) ? `${d.rssi} dBm` : '',
    tasa: d.tasa ?? '',
    detalle: [
      d.tipo === 'inicio' ? `cada ${d.periodo_ms} ms, timeout ${d.timeout_ms} ms, ${d.duracion_s} s${d.lora_rssi === false ? ', sin RSSI' : ''}` : '',
      d.tipo === 'fin' ? d.motivo : '',
      d.crudo ? `bytes: ${d.crudo}` : '',
    ].filter(Boolean).join(' · '),
  };
  const tr = document.createElement('tr');
  tr.dataset.tipo = d.tipo;
  for (const valor of Object.values(celdas)) {
    const td = document.createElement('td');
    td.textContent = String(valor);
    tr.append(td);
  }
  return tr;
}

function agregar(mensaje) {
  const tr = filaDe(mensaje);
  lineas.unshift(tr);
  const cuerpo = $('dbg-log');
  cuerpo.prepend(tr);
  while (lineas.length > MAX_LINEAS) lineas.pop().remove();
  $('dbg-vacio').hidden = true;
}

function leerFormulario() {
  const campos = $('dbg-form').elements;
  const entero = (nombre) => {
    const valor = campos.namedItem(nombre).value.trim();
    return valor === '' ? NaN : Number(valor);
  };
  return {
    activo: true,
    nodo: entero('nodo'),
    periodo_ms: entero('periodo_ms'),
    timeout_ms: entero('timeout_ms'),
    duracion_s: entero('duracion_min') * 60,
  };
}

async function enviar(pedido, boton) {
  boton.disabled = true;
  try {
    await iniciarDebugLora(pedido);
    mostrarAviso(pedido.activo
      ? 'Pedido enviado. El gateway empieza al terminar la ronda en curso.'
      : 'Pedido de detener enviado.', 'ok');
  } catch (error) {
    alError(error);
  } finally {
    boton.disabled = false;
    renderEstado();
  }
}

export function iniciarVistaDebug({ manejarError } = {}) {
  if (manejarError) alError = manejarError;
  alRecibirMensaje((mensaje) => {
    if (mensaje.topic === TOPIC_LOG && !mensaje.retenido) agregar(mensaje);
    if (mensaje.topic === TOPIC_ESTADO) renderEstado();
  });

  $('dbg-form').addEventListener('submit', (evento) => {
    evento.preventDefault();
    const pedido = leerFormulario();
    if (![pedido.nodo, pedido.periodo_ms, pedido.timeout_ms, pedido.duracion_s].every(Number.isInteger)) {
      mostrarAviso('Completá todos los campos con números enteros.', 'error');
      return;
    }
    enviar(pedido, $('dbg-iniciar'));
  });
  $('dbg-detener').addEventListener('click', () => enviar({ activo: false }, $('dbg-detener')));
  $('dbg-limpiar').addEventListener('click', () => {
    lineas.splice(0).forEach((tr) => tr.remove());
    $('dbg-vacio').hidden = false;
  });
  renderEstado();
}
