/**
 * Gestion de nodos: alta, edicion y baja de estaciones.
 *
 * El token de administracion no viaja en el bundle: lo escribe quien opera y
 * queda en sessionStorage mientras dure la pestania. Un sitio estatico servido
 * desde un CDN no puede guardar secretos, asi que el secreto lo aporta la
 * persona, no el build.
 */
import { api, guardarToken, leerToken, olvidarToken } from '../api/client.js';
import {
  actualizarEstacion,
  crearEstacion,
  darDeBajaEstacion,
  listarEstaciones,
} from '../api/estaciones.js';
import { mostrarAviso } from '../ui/aviso.js';
import '../styles/base.css';
import '../styles/gestion.css';

const formToken = document.getElementById('form-token');
const inputToken = document.getElementById('token');
const estadoToken = document.getElementById('estado-token');
const btnOlvidar = document.getElementById('btn-olvidar-token');

const formAlta = document.getElementById('form-alta');
const cuerpoTabla = document.getElementById('tabla-estaciones');
const estadoTabla = document.getElementById('estado-tabla');

function numeroONulo(valor) {
  const texto = String(valor ?? '').trim();
  return texto === '' ? null : Number(texto);
}

function textoCoordenada(valor) {
  return valor === null || valor === undefined ? '' : String(valor);
}

function refrescarEstadoToken() {
  const hayToken = Boolean(leerToken());
  estadoToken.textContent = hayToken
    ? 'Token cargado en esta pestaña.'
    : 'Sin token: solo lectura.';
  estadoToken.dataset.estado = hayToken ? 'ok' : 'vacio';
  btnOlvidar.hidden = !hayToken;
}

formToken.addEventListener('submit', (evento) => {
  evento.preventDefault();
  const valor = inputToken.value.trim();
  if (!valor) {
    mostrarAviso('Escribí el token de administración.', 'error');
    return;
  }
  guardarToken(valor);
  inputToken.value = '';
  refrescarEstadoToken();
  mostrarAviso('Token guardado para esta pestaña.', 'ok');
});

btnOlvidar.addEventListener('click', () => {
  olvidarToken();
  refrescarEstadoToken();
  mostrarAviso('Token borrado.', 'ok');
});

formAlta.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  const datos = Object.fromEntries(new FormData(formAlta));

  try {
    await crearEstacion({
      id: String(datos.id).trim().toLowerCase(),
      nombre: String(datos.nombre).trim(),
      latitud: numeroONulo(datos.latitud),
      longitud: numeroONulo(datos.longitud),
    });
    formAlta.reset();
    mostrarAviso('Estación creada.', 'ok');
    await cargarTabla();
  } catch (error) {
    mostrarAviso(error.message, 'error');
  }
});

function filaEstacion(estacion) {
  const fila = document.createElement('tr');
  fila.dataset.id = estacion.id;
  if (!estacion.activa) fila.classList.add('fila--inactiva');

  fila.innerHTML = `
    <td><code>${estacion.id}</code></td>
    <td><input class="campo" name="nombre" value="" size="24"></td>
    <td><input class="campo" name="latitud" type="number" step="any" placeholder="—" size="10"></td>
    <td><input class="campo" name="longitud" type="number" step="any" placeholder="—" size="10"></td>
    <td class="celda-estado">${estacion.activa ? 'Activa' : 'Inactiva'}</td>
    <td class="celda-acciones">
      <button type="button" data-accion="guardar">Guardar</button>
      <button type="button" data-accion="alternar">${estacion.activa ? 'Dar de baja' : 'Reactivar'}</button>
    </td>
  `;

  // Los valores se asignan por propiedad y no dentro del HTML, para que un
  // nombre con comillas o etiquetas no pueda inyectar marcado.
  fila.querySelector('[name="nombre"]').value = estacion.nombre;
  fila.querySelector('[name="latitud"]').value = textoCoordenada(estacion.latitud);
  fila.querySelector('[name="longitud"]').value = textoCoordenada(estacion.longitud);

  fila.querySelector('[data-accion="guardar"]').addEventListener('click', async () => {
    try {
      await actualizarEstacion(estacion.id, {
        nombre: fila.querySelector('[name="nombre"]').value.trim(),
        latitud: numeroONulo(fila.querySelector('[name="latitud"]').value),
        longitud: numeroONulo(fila.querySelector('[name="longitud"]').value),
      });
      mostrarAviso(`Estación ${estacion.id} actualizada.`, 'ok');
      await cargarTabla();
    } catch (error) {
      mostrarAviso(error.message, 'error');
    }
  });

  fila.querySelector('[data-accion="alternar"]').addEventListener('click', async () => {
    try {
      if (estacion.activa) {
        await darDeBajaEstacion(estacion.id);
        mostrarAviso(
          `Estación ${estacion.id} dada de baja. Sus mediciones históricas se conservan.`,
          'ok'
        );
      } else {
        await actualizarEstacion(estacion.id, { activa: true });
        mostrarAviso(`Estación ${estacion.id} reactivada.`, 'ok');
      }
      await cargarTabla();
    } catch (error) {
      mostrarAviso(error.message, 'error');
    }
  });

  return fila;
}

async function cargarTabla() {
  estadoTabla.textContent = 'Cargando…';
  estadoTabla.hidden = false;
  try {
    const estaciones = await listarEstaciones({ incluirInactivas: true });
    cuerpoTabla.textContent = '';
    estaciones.forEach((estacion) => cuerpoTabla.appendChild(filaEstacion(estacion)));
    estadoTabla.hidden = estaciones.length > 0;
    if (estaciones.length === 0) estadoTabla.textContent = 'No hay estaciones registradas.';
  } catch (error) {
    estadoTabla.textContent = error.message;
  }
}

async function comprobarEscrituras() {
  try {
    const salud = await api.get('/api/salud');
    if (!salud.escrituras) {
      mostrarAviso(
        'El servidor tiene las escrituras deshabilitadas: falta configurar API_TOKEN.',
        'error',
        8000
      );
    }
  } catch {
    // El error de conexion ya se va a ver al cargar la tabla.
  }
}

refrescarEstadoToken();
cargarTabla();
comprobarEscrituras();
