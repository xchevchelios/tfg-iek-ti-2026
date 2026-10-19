/**
 * Pestania Gateway: estado, lista de nodos y parametros.
 *
 * Lo mismo que las opciones 6 y 7 de gestor.py, con una diferencia de
 * fuente: el estado se lee en vivo de los mensajes retenidos del broker, y
 * los cambios se publican a traves de la API, que los valida y los anota en
 * la bitacora. El navegador nunca publica en el broker.
 *
 * Tres versiones de cada dato conviven y se muestran lado a lado:
 *   base        lo que dice la base de datos (solo la lista de nodos)
 *   deseado     el mensaje retenido en gateway/config/...
 *   aplicado    lo que el gateway reporta en gateway/estado/aplicado
 */
import { listarEstaciones, republicarConfiguracion } from '../api/estaciones.js';
import { guardarParametros, leerLimites } from '../api/gateway.js';
import { alCambiarEstadoPanel, alRecibirMensaje, ultimoMensaje } from '../api/mqttPanel.js';
import { mostrarAviso } from './aviso.js';

const TOPICS = {
  conexion: 'estaciones/estado',
  nodos: 'gateway/config/nodos',
  parametros: 'gateway/config/parametros',
  aplicado: 'gateway/estado/aplicado',
};
const CAMPOS = ['periodo_s', 'estabilizacion_s', 'timeout_ms', 'publicar'];
const POR_DEFECTO = { periodo_s: 300, estabilizacion_s: 30, timeout_ms: 3000, publicar: true };

const $ = (id) => document.getElementById(id);
const formulario = () => $('gw-form');

let limites = null;
let nodosBase = [];
let editado = false;
// Parametros recien publicados que todavia no volvieron por MQTT. Mientras
// tanto son la referencia del formulario, para que no "salte" al valor viejo.
let enviados = null;
let alError = (error) => mostrarAviso(error.message, 'error');

// ------------------------------------------------------------- utilidades

const numero = (valor, decimales = 1) =>
  Number(valor).toLocaleString('es-PY', { maximumFractionDigits: decimales });

function duracion(segundos) {
  const s = Math.max(0, Math.floor(segundos));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  return h ? `${h} h ${m} min` : `${m} min`;
}

function leerJson(topic) {
  const mensaje = ultimoMensaje(topic);
  if (!mensaje) return null;
  try {
    const datos = JSON.parse(mensaje.texto);
    return datos && typeof datos === 'object' && !Array.isArray(datos) ? { datos, mensaje } : null;
  } catch {
    return null;
  }
}

const mismaLista = (a, b) =>
  Array.isArray(a) && Array.isArray(b) &&
  JSON.stringify([...a].sort((x, y) => x - y)) === JSON.stringify([...b].sort((x, y) => x - y));

const mismosParametros = (a, b) => a && b && CAMPOS.every((c) => a[c] === b[c]);

function completar(datos) {
  const p = { ...(limites?.por_defecto ?? POR_DEFECTO) };
  if (datos) CAMPOS.forEach((c) => { if (c in datos) p[c] = datos[c]; });
  return p;
}

function textoParametro(clave, valor) {
  if (valor === undefined || valor === null) return '—';
  if (clave === 'publicar') return valor ? 'sí' : 'no';
  return clave === 'timeout_ms' ? `${valor} ms` : `${valor} s`;
}

/** Agrega un par termino/valor a un <dl>, siempre como texto. */
function dato(dl, termino, valor, clase) {
  const dt = document.createElement('dt');
  const dd = document.createElement('dd');
  dt.textContent = termino;
  dd.textContent = valor;
  if (clase) dd.className = clase;
  dl.append(dt, dd);
}

function alerta(lista, texto, tipo = 'aviso') {
  const li = document.createElement('li');
  li.textContent = texto;
  li.dataset.tipo = tipo;
  lista.append(li);
}

// ------------------------------------------------------------- render

const ETIQUETAS_CONEXION = {
  conectando: 'Conectando…',
  conectado: 'Conectado al broker',
  reconectando: 'Reconectando…',
  desconectado: 'Sin conexión',
  error: 'Sin conexión',
};

function pintarConexion(estado, detalle) {
  const chip = $('gw-conexion');
  chip.dataset.estado = estado === 'conectando' ? 'reconectando' : estado;
  chip.textContent = ETIQUETAS_CONEXION[estado] ?? estado;
  const aviso = $('gw-aviso');
  aviso.hidden = estado !== 'error';
  aviso.textContent = estado === 'error'
    ? `No se pudo leer el estado del gateway desde el broker: ${detalle}`
    : '';
}

function renderEstado() {
  const dl = $('gw-estado');
  const lista = $('gw-alertas');
  dl.textContent = '';
  lista.textContent = '';

  const conexion = leerJson(TOPICS.conexion);
  const estado = conexion ? String(conexion.datos.gateway_status ?? '—') : 'desconocida';
  dato(dl, 'Conexión', estado,
    estado.toUpperCase() === 'ONLINE' ? 'gw-ok' : estado.toUpperCase() === 'OFFLINE' ? 'gw-mal' : '');

  const aplicado = leerJson(TOPICS.aplicado);
  if (!aplicado) {
    dato(dl, 'Reporte', 'ninguno');
    alerta(lista, 'El gateway no reporta lo que aplicó: tiene el firmware v1 o todavía no completó una ronda.');
    return;
  }
  const r = aplicado.datos;
  const ultima = r.ultima_ronda ?? {};
  const frescas = ultima.frescas ?? [];
  const total = (r.nodos ?? []).length;
  dato(dl, 'Firmware', r.firmware ?? '—');
  dato(dl, 'Encendido hace', duracion(r.encendido_s ?? 0));
  dato(dl, 'Última ronda',
    `${frescas.length} de ${total} nodos con dato · ${numero((ultima.duracion_ms ?? 0) / 1000)} s`,
    total && frescas.length === total ? 'gw-ok' : frescas.length ? 'gw-aviso' : 'gw-mal');
  dato(dl, 'Reporte',
    aplicado.mensaje.retenido
      ? 'el último guardado en el broker'
      : `recibido a las ${aplicado.mensaje.hora.toLocaleTimeString('es-PY', { hour12: false })}`);

  if (ultima.sin_respuesta?.length) alerta(lista, `No respondieron: ${ultima.sin_respuesta.join(', ')}`);
  if (ultima.viejas?.length) alerta(lista, `No escucharon la orden de medir: ${ultima.viejas.join(', ')}`);
  if (ultima.sensor_fallando?.length) alerta(lista, `Con algún sensor sin lecturas: ${ultima.sensor_fallando.join(', ')}`);
  if (ultima.excedida) alerta(lista, 'La ronda no entró en el período: la siguiente arrancó tarde.', 'error');
  if (r.descartadas) alerta(lista, `${r.descartadas} mediciones descartadas desde el encendido (sin red y con la memoria llena).`);
  Object.entries(r.rechazos ?? {}).forEach(([que, motivo]) =>
    alerta(lista, `Rechazó la configuración de ${que}: ${motivo}`, 'error'));
}

function renderNodos() {
  const dl = $('gw-nodos');
  dl.textContent = '';
  const deseada = leerJson(TOPICS.nodos);
  const aplicado = leerJson(TOPICS.aplicado);
  const publicada = deseada?.datos.nodos;
  const aplicada = aplicado?.datos.nodos;

  const lista = (v) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'ninguno') : '—');
  dato(dl, 'Según la base', lista(nodosBase));
  dato(dl, 'Publicada en el broker', deseada ? lista(publicada) : 'ninguna');
  dato(dl, 'Aplicada por el gateway', aplicado ? lista(aplicada) : 'sin reporte');

  const veredicto = $('gw-nodos-veredicto');
  const boton = $('gw-republicar');
  boton.hidden = true;
  if (!mismaLista(publicada, nodosBase)) {
    veredicto.dataset.tipo = 'error';
    veredicto.textContent = 'La lista publicada no coincide con la base.';
    boton.hidden = false;
  } else if (!aplicado) {
    veredicto.dataset.tipo = 'pendiente';
    veredicto.textContent = 'El broker la tiene, pero el gateway no reporta qué aplicó.';
  } else if (!mismaLista(aplicada, nodosBase)) {
    veredicto.dataset.tipo = 'pendiente';
    veredicto.textContent = 'El gateway todavía no la aplicó: lo hace al empezar su próxima ronda.';
  } else {
    veredicto.dataset.tipo = 'ok';
    veredicto.textContent = 'Base, broker y gateway coinciden.';
  }
}

function referencia() {
  if (enviados) return enviados;
  return completar(leerJson(TOPICS.parametros)?.datos ?? leerJson(TOPICS.aplicado)?.datos);
}

function renderParametros() {
  const deseado = leerJson(TOPICS.parametros)?.datos;
  const aplicado = leerJson(TOPICS.aplicado)?.datos;
  if (enviados && mismosParametros(completar(deseado), enviados)) enviados = null;

  CAMPOS.forEach((clave) => {
    const d = deseado?.[clave];
    const a = aplicado?.[clave];
    formulario().querySelector(`[data-deseado="${clave}"]`).textContent = textoParametro(clave, d);
    const celda = formulario().querySelector(`[data-aplicado="${clave}"]`);
    celda.textContent = textoParametro(clave, a);
    if (d !== undefined && a !== undefined && d !== a) {
      const marca = document.createElement('span');
      marca.className = 'gw-pendiente';
      marca.textContent = 'pendiente';
      celda.append(' ', marca);
    }
  });
}

function rellenarFormulario() {
  const p = referencia();
  const f = formulario();
  f.periodo_s.value = p.periodo_s;
  f.estabilizacion_s.value = p.estabilizacion_s;
  f.timeout_ms.value = p.timeout_ms;
  f.publicar.checked = Boolean(p.publicar);
}

function leerFormulario() {
  const f = formulario();
  const entero = (input) => (input.value.trim() === '' ? NaN : Number(input.value));
  return {
    periodo_s: entero(f.periodo_s),
    estabilizacion_s: entero(f.estabilizacion_s),
    timeout_ms: entero(f.timeout_ms),
    publicar: f.publicar.checked,
  };
}

function peorRonda(p, n) {
  const margen = limites?.margen_medicion_ms ?? 2000;
  const pausa = limites?.pausa_entre_nodos_ms ?? 100;
  return (p.estabilizacion_s * 1000 + margen + n * (p.timeout_ms + pausa)) / 1000;
}

/** La misma validacion que el servidor, para avisar antes de enviar. */
function validar(p) {
  const rangos = limites?.limites ?? {};
  const nombres = { periodo_s: 'El período', estabilizacion_s: 'La ventana', timeout_ms: 'El timeout' };
  for (const [clave, { min, max }] of Object.entries(rangos)) {
    if (!Number.isInteger(p[clave])) return `${nombres[clave]} tiene que ser un número entero.`;
    if (p[clave] < min || p[clave] > max) return `${nombres[clave]} tiene que estar entre ${min} y ${max}.`;
  }
  const peor = peorRonda(p, nodosBase.length);
  if (peor > p.periodo_s) {
    return `Con ${nodosBase.length} nodos, una ronda puede tardar hasta ${Math.round(peor)} s si ninguno responde: ` +
      `el período tiene que ser de al menos ${Math.ceil(peor)} s.`;
  }
  return null;
}

function renderPrevia() {
  const p = leerFormulario();
  const motivo = limites ? validar(p) : null;
  const previa = $('gw-previa');
  const error = $('gw-error');
  previa.textContent = '';

  error.hidden = !motivo;
  error.textContent = motivo ?? '';

  if (Number.isFinite(p.periodo_s) && Number.isFinite(p.estabilizacion_s) && p.periodo_s > 0) {
    const encendido = p.estabilizacion_s / p.periodo_s;
    const vidaH = limites?.vida_laser_h ?? 8000;
    const n = nodosBase.length;
    const peor = Number.isFinite(p.timeout_ms) ? peorRonda(p, n) : NaN;
    const items = [
      ['Láser encendido', `${numero(encendido * 100)} % del tiempo`],
      ['Vida estimada del láser', `≈ ${numero(vidaH / encendido / 8760)} años`],
      ['Mediciones por estación', `${numero(Math.floor(86400 / p.periodo_s), 0)} por día`],
      ['Ronda en el peor caso', Number.isFinite(peor) ? `${Math.round(peor)} s de ${p.periodo_s} s (${n} nodos)` : '—'],
    ];
    items.forEach(([titulo, valor]) => {
      const div = document.createElement('div');
      div.className = 'gw-previa__item';
      const t = document.createElement('span');
      t.className = 'gw-previa__titulo';
      t.textContent = titulo;
      const v = document.createElement('strong');
      v.textContent = valor;
      div.append(t, v);
      previa.append(div);
    });
  }

  const cambio = !mismosParametros(p, referencia());
  $('gw-guardar').disabled = Boolean(motivo) || !cambio || !limites;
  $('gw-descartar').disabled = !cambio;
}

function renderizar() {
  renderEstado();
  renderNodos();
  renderParametros();
  if (!editado) rellenarFormulario();
  renderPrevia();
}

// ------------------------------------------------------------- API publica

export function iniciarVistaGateway({ manejarError } = {}) {
  if (manejarError) alError = manejarError;
  alCambiarEstadoPanel(pintarConexion);
  alRecibirMensaje((mensaje) => {
    if (Object.values(TOPICS).includes(mensaje.topic)) renderizar();
  });

  const f = formulario();
  f.addEventListener('input', () => {
    editado = true;
    renderPrevia();
  });

  $('gw-descartar').addEventListener('click', () => {
    editado = false;
    rellenarFormulario();
    renderPrevia();
  });

  f.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const p = leerFormulario();
    const motivo = validar(p);
    if (motivo) return;
    const boton = $('gw-guardar');
    boton.disabled = true;
    try {
      await guardarParametros(p);
      enviados = p;
      editado = false;
      mostrarAviso('Publicado. El gateway lo aplica al empezar su próxima ronda.', 'ok');
      renderizar();
    } catch (error) {
      alError(error);
      renderPrevia();
    }
  });

  $('gw-republicar').addEventListener('click', async () => {
    try {
      await republicarConfiguracion();
      mostrarAviso('Lista de nodos reenviada al gateway.', 'ok');
    } catch (error) {
      alError(error);
    }
  });
}

/** Se llama cada vez que se abre la pestania: refresca lo que viene de la API. */
export async function mostrarVistaGateway() {
  try {
    const [datosLimites, estaciones] = await Promise.all([leerLimites(), listarEstaciones()]);
    limites = datosLimites;
    const f = formulario();
    Object.entries(limites.limites).forEach(([clave, { min, max }]) => {
      f[clave].min = min;
      f[clave].max = max;
    });
    nodosBase = estaciones
      .filter((e) => e.activa && e.direccion_lora !== null && e.direccion_lora !== undefined)
      .map((e) => e.direccion_lora)
      .sort((a, b) => a - b);
  } catch (error) {
    alError(error);
  }
  renderizar();
}
