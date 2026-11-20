/**
 * Pestania Trafico MQTT: los mensajes del broker en vivo, como una terminal.
 *
 * Escucha desde que se abre la conexion del panel, aunque la pestania no
 * este visible, para que al abrirla ya muestre lo que paso. Conserva las
 * ultimas MAX_LINEAS: una terminal que crece sin limite termina trabando la
 * pestania del navegador.
 *
 * Todo el contenido se escribe como texto (textContent), nunca como HTML: lo
 * que llega por el broker no se interpreta.
 *
 * En pantalla cada mensaje se recorta a MAX_CARGA caracteres, pero se copia
 * entero: con el boton Copiar (los que pasan el filtro) o con doble clic en
 * una linea (ese mensaje, con el JSON formateado).
 */
import { alCambiarEstadoPanel, alRecibirMensaje } from '../api/mqttPanel.js';

const MAX_LINEAS = 500;
const MAX_CARGA = 400;

const $ = (id) => document.getElementById(id);

const buffer = [];
let visible = false;
let pausado = false;
let pendientes = 0;

function tipo(topic) {
  if (topic === 'estaciones/estado') return 'lwt';
  if (topic.startsWith('estaciones/')) return 'dato';
  if (topic.startsWith('gateway/config/')) return 'config';
  if (topic.startsWith('gateway/estado/')) return 'reporte';
  return 'otro';
}

function textoCarga(texto) {
  let salida = texto;
  try {
    salida = JSON.stringify(JSON.parse(texto));
  } catch {
    // no es JSON: se muestra tal cual
  }
  if (salida === '') return '(vacío: el broker borró el retenido)';
  return salida.length > MAX_CARGA ? `${salida.slice(0, MAX_CARGA)}…` : salida;
}

/** RSSI LoRa (dBm) que el gateway agrega a cada medicion, si viene. */
function rssiDe(texto) {
  try {
    const rssi = JSON.parse(texto)?.rssi;
    return Number.isFinite(rssi) ? rssi : null;
  } catch {
    return null;
  }
}

/** Un mensaje completo, en una linea, para copiar: hora, R si es retenido, topic y carga. */
function textoCompleto(mensaje) {
  const hora = mensaje.hora.toLocaleTimeString('es-PY', { hour12: false });
  return `${hora} ${mensaje.retenido ? 'R' : ' '} ${mensaje.topic} ${mensaje.texto}`;
}

/** El mensaje con el JSON indentado, para leerlo o pegarlo en un chat. */
function textoFormateado(mensaje) {
  let carga = mensaje.texto;
  try {
    carga = JSON.stringify(JSON.parse(mensaje.texto), null, 2);
  } catch {
    // no es JSON: va tal cual
  }
  const hora = mensaje.hora.toLocaleTimeString('es-PY', { hour12: false });
  return `${hora}${mensaje.retenido ? ' (retenido)' : ''} ${mensaje.topic}\n${carga}`;
}

async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    // Sin permiso o sin API (contexto no seguro): el metodo viejo.
    const area = document.createElement('textarea');
    area.value = texto;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}

let avisoCopia = null;
function avisarCopia(texto) {
  const caja = $('term-copiado');
  caja.textContent = texto;
  caja.hidden = false;
  clearTimeout(avisoCopia);
  avisoCopia = setTimeout(() => { caja.hidden = true; }, 2500);
}

function pasaFiltro(mensaje) {
  const filtro = $('term-filtro').value.trim().toLowerCase();
  return !filtro || mensaje.topic.toLowerCase().includes(filtro);
}

function linea(mensaje) {
  const div = document.createElement('div');
  div.className = `term-linea term-linea--${tipo(mensaje.topic)}`;
  div.title = 'Doble clic para copiar el mensaje completo';
  div.addEventListener('dblclick', async () => {
    window.getSelection()?.removeAllRanges();   // el doble clic selecciona una palabra
    const ok = await copiar(textoFormateado(mensaje));
    avisarCopia(ok ? `Copiado: ${mensaje.topic}` : 'No se pudo copiar');
  });

  const hora = document.createElement('span');
  hora.className = 't-hora';
  hora.textContent = mensaje.hora.toLocaleTimeString('es-PY', { hour12: false });

  const ret = document.createElement('span');
  ret.className = 't-ret';
  ret.textContent = mensaje.retenido ? 'R' : ' ';
  ret.title = mensaje.retenido ? 'Retenido: el último estado guardado en el broker' : '';

  const topic = document.createElement('span');
  topic.className = 't-topic';
  topic.textContent = mensaje.topic;

  const carga = document.createElement('span');
  carga.className = 't-carga';
  carga.textContent = textoCarga(mensaje.texto);

  div.append(hora, ' ', ret, ' ', topic, ' ');
  const rssi = tipo(mensaje.topic) === 'dato' ? rssiDe(mensaje.texto) : null;
  if (rssi !== null) {
    const etiqueta = document.createElement('span');
    etiqueta.className = 't-rssi';
    etiqueta.textContent = `[${rssi} dBm]`;
    etiqueta.title = 'RSSI LoRa con que el gateway recibió esta medición';
    div.append(etiqueta, ' ');
  }
  div.append(carga);
  return div;
}

function seguirAlFinal() {
  if ($('term-seguir').checked) {
    const salida = $('term-salida');
    salida.scrollTop = salida.scrollHeight;
  }
}

function renderizarTodo() {
  const salida = $('term-salida');
  salida.textContent = '';
  const fragmento = document.createDocumentFragment();
  buffer.filter(pasaFiltro).forEach((m) => fragmento.append(linea(m)));
  salida.append(fragmento);
  if (!salida.childElementCount) {
    const vacio = document.createElement('div');
    vacio.className = 'term-vacio';
    vacio.textContent = buffer.length ? 'Ningún mensaje coincide con el filtro.' : 'Esperando mensajes…';
    salida.append(vacio);
  }
  pendientes = 0;
  seguirAlFinal();
  actualizarContador();
}

function agregar(mensaje) {
  buffer.push(mensaje);
  if (buffer.length > MAX_LINEAS) buffer.shift();
  if (!visible) return;
  if (pausado) {
    pendientes++;
    actualizarContador();
    return;
  }
  const salida = $('term-salida');
  salida.querySelector('.term-vacio')?.remove();
  if (pasaFiltro(mensaje)) salida.append(linea(mensaje));
  while (salida.childElementCount > MAX_LINEAS) salida.firstElementChild.remove();
  seguirAlFinal();
  actualizarContador();
}

function actualizarContador() {
  const haceUnMinuto = Date.now() - 60000;
  const porMinuto = buffer.filter((m) => !m.retenido && m.hora.getTime() >= haceUnMinuto).length;
  let texto = `${buffer.length} mensajes · ${porMinuto} en el último minuto`;
  if (pausado) texto += ` · en pausa (${pendientes} nuevos)`;
  $('term-contador').textContent = texto;
}

export function iniciarTerminal() {
  alRecibirMensaje(agregar);

  alCambiarEstadoPanel((estado) => {
    const chip = $('term-conexion');
    chip.dataset.estado = estado === 'conectando' ? 'reconectando' : estado;
    chip.textContent = {
      conectando: 'Conectando…',
      conectado: 'Escuchando estaciones/# y gateway/#',
      reconectando: 'Reconectando…',
    }[estado] ?? 'Sin conexión';
  });

  $('term-filtro').addEventListener('input', renderizarTodo);
  $('term-seguir').addEventListener('change', seguirAlFinal);

  $('term-pausa').addEventListener('click', () => {
    pausado = !pausado;
    $('term-pausa').textContent = pausado ? 'Reanudar' : 'Pausar';
    $('term-pausa').setAttribute('aria-pressed', String(pausado));
    if (!pausado) renderizarTodo();
    else actualizarContador();
  });

  $('term-copiar').addEventListener('click', async () => {
    const mensajes = buffer.filter(pasaFiltro);
    if (!mensajes.length) {
      avisarCopia('No hay mensajes para copiar');
      return;
    }
    const ok = await copiar(mensajes.map(textoCompleto).join('\n'));
    avisarCopia(ok ? `Copiados ${mensajes.length} mensajes completos` : 'No se pudo copiar');
  });

  // Ctrl+A dentro de la terminal selecciona solo la terminal, no la pagina.
  $('term-salida').addEventListener('keydown', (evento) => {
    if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === 'a') {
      evento.preventDefault();
      const rango = document.createRange();
      rango.selectNodeContents($('term-salida'));
      const seleccion = window.getSelection();
      seleccion.removeAllRanges();
      seleccion.addRange(rango);
    }
  });

  $('term-limpiar').addEventListener('click', () => {
    buffer.length = 0;
    renderizarTodo();
  });

  setInterval(() => { if (visible) actualizarContador(); }, 5000);
}

export function mostrarTerminal() {
  visible = true;
  renderizarTodo();
}

export function ocultarTerminal() {
  visible = false;
}
