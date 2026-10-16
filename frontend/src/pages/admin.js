/**
 * Panel de administración.
 *
 * La página resuelve su propio estado: al cargar le pregunta al servidor quién
 * soy y, según la respuesta, muestra la pantalla de acceso o el panel. No hay
 * rutas separadas ni redirecciones.
 *
 * La sesión vive en una cookie HttpOnly que este código no puede leer — y ese
 * es justamente el punto: un XSS no podría robarla. Lo único que el JavaScript
 * conserva es el token CSRF, en memoria.
 */
import { entrar, leerSesion, salir, tomarErrorDeAcceso } from '../api/auth.js';
import {
  actualizarEstacion,
  crearEstacion,
  darDeBajaEstacion,
  listarEstaciones,
} from '../api/estaciones.js';
import {
  actualizarUsuario,
  crearUsuario,
  darDeBajaUsuario,
  leerBitacora,
  listarUsuarios,
} from '../api/usuarios.js';
import { mostrarAviso } from '../ui/aviso.js';
import '../styles/base.css';
import '../styles/admin.css';

const pantallaAcceso = document.getElementById('pantalla-acceso');
const mensajeAcceso = document.getElementById('mensaje-acceso');
const panel = document.getElementById('panel');
const barraSesion = document.getElementById('barra-sesion');
const sesionUsuario = document.getElementById('sesion-usuario');

const formAlta = document.getElementById('form-alta');
const cuerpoEstaciones = document.getElementById('tabla-estaciones');
const estadoEstaciones = document.getElementById('estado-tabla');

const seccionUsuarios = document.getElementById('seccion-usuarios');
const formUsuario = document.getElementById('form-usuario');
const cuerpoUsuarios = document.getElementById('tabla-usuarios');
const estadoUsuarios = document.getElementById('estado-usuarios');

const seccionBitacora = document.getElementById('seccion-bitacora');
const cuerpoBitacora = document.getElementById('tabla-bitacora');
const estadoBitacora = document.getElementById('estado-bitacora');

let usuarioActual = null;

// =================================================================
// Utilidades
// =================================================================

function numeroONulo(valor) {
  const texto = String(valor ?? '').trim();
  return texto === '' ? null : Number(texto);
}

function textoCoordenada(valor) {
  return valor === null || valor === undefined ? '' : String(valor);
}

function fechaCorta(iso) {
  if (!iso) return '—';
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? '—' : fecha.toLocaleString('es-PY');
}

/**
 * Cualquier 401 en medio de la operación significa que la sesión se terminó
 * — venció, se cerró en otra pestaña, o un administrador dio de baja al
 * usuario. En todos esos casos corresponde volver a la pantalla de acceso.
 */
function manejarError(error) {
  if (error?.estado === 401) {
    usuarioActual = null;
    mostrarAcceso('Tu sesión terminó. Volvé a entrar.');
    return;
  }
  mostrarAviso(error.message, 'error');
}

// =================================================================
// Estado de la vista
// =================================================================

function mostrarAcceso(mensaje) {
  panel.hidden = true;
  barraSesion.hidden = true;
  pantallaAcceso.hidden = false;
  mensajeAcceso.textContent = mensaje ?? '';
  mensajeAcceso.hidden = !mensaje;
}

function mostrarPanel(usuario) {
  usuarioActual = usuario;
  pantallaAcceso.hidden = true;
  panel.hidden = false;
  barraSesion.hidden = false;

  const etiquetaRol = usuario.rol === 'admin' ? 'administrador' : 'operador';
  sesionUsuario.textContent = `${usuario.nombre} · ${etiquetaRol}`;
  sesionUsuario.title = usuario.email;

  const esAdmin = usuario.rol === 'admin';
  seccionUsuarios.hidden = !esAdmin;
  seccionBitacora.hidden = !esAdmin;
}

// =================================================================
// Estaciones
// =================================================================

function filaEstacion(estacion) {
  const fila = document.createElement('tr');
  if (!estacion.activa) fila.classList.add('fila--inactiva');

  fila.innerHTML = `
    <td><code>${estacion.id}</code></td>
    <td><input class="campo" name="nombre" size="24"></td>
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
      await cargarEstaciones();
    } catch (error) {
      manejarError(error);
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
      await cargarEstaciones();
    } catch (error) {
      manejarError(error);
    }
  });

  return fila;
}

async function cargarEstaciones() {
  estadoEstaciones.textContent = 'Cargando…';
  estadoEstaciones.hidden = false;
  try {
    const estaciones = await listarEstaciones({ incluirInactivas: true });
    cuerpoEstaciones.textContent = '';
    estaciones.forEach((e) => cuerpoEstaciones.appendChild(filaEstacion(e)));
    estadoEstaciones.hidden = estaciones.length > 0;
    if (estaciones.length === 0) estadoEstaciones.textContent = 'No hay estaciones registradas.';
  } catch (error) {
    estadoEstaciones.textContent = error.message;
  }
}

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
    await cargarEstaciones();
  } catch (error) {
    manejarError(error);
  }
});

// =================================================================
// Usuarios (solo admin)
// =================================================================

function filaUsuario(usuario) {
  const fila = document.createElement('tr');
  if (!usuario.activo) fila.classList.add('fila--inactiva');

  // Nadie puede modificarse a sí mismo: el servidor lo rechaza igual, pero
  // deshabilitar los controles evita que la persona lo intente y reciba un error.
  const esYo = usuario.id === usuarioActual?.id;

  fila.innerHTML = `
    <td class="celda-correo"></td>
    <td></td>
    <td>
      <select class="campo" name="rol">
        <option value="operador">Operador</option>
        <option value="admin">Administrador</option>
      </select>
    </td>
    <td class="celda-estado">${usuario.activo ? 'Activo' : 'Inactivo'}</td>
    <td class="celda-fecha">${fechaCorta(usuario.ultimo_acceso)}</td>
    <td class="celda-acciones">
      <button type="button" data-accion="guardar">Guardar</button>
      <button type="button" data-accion="alternar">${usuario.activo ? 'Dar de baja' : 'Reactivar'}</button>
    </td>
  `;

  fila.querySelector('.celda-correo').textContent = usuario.email;
  fila.querySelectorAll('td')[1].textContent = usuario.nombre;

  const selectorRol = fila.querySelector('[name="rol"]');
  selectorRol.value = usuario.rol;

  const btnGuardar = fila.querySelector('[data-accion="guardar"]');
  const btnAlternar = fila.querySelector('[data-accion="alternar"]');

  if (esYo) {
    selectorRol.disabled = true;
    btnGuardar.disabled = true;
    btnAlternar.disabled = true;
    btnAlternar.title = 'No podés modificar tu propio usuario';
    fila.classList.add('fila--propia');
  }

  btnGuardar.addEventListener('click', async () => {
    try {
      await actualizarUsuario(usuario.id, { rol: selectorRol.value });
      mostrarAviso(`${usuario.email} actualizado.`, 'ok');
      await cargarUsuarios();
    } catch (error) {
      manejarError(error);
    }
  });

  btnAlternar.addEventListener('click', async () => {
    try {
      if (usuario.activo) {
        await darDeBajaUsuario(usuario.id);
        mostrarAviso(`${usuario.email} dado de baja. Su sesión se cerró.`, 'ok');
      } else {
        await actualizarUsuario(usuario.id, { activo: true });
        mostrarAviso(`${usuario.email} reactivado.`, 'ok');
      }
      await cargarUsuarios();
      await cargarBitacora();
    } catch (error) {
      manejarError(error);
    }
  });

  return fila;
}

async function cargarUsuarios() {
  if (usuarioActual?.rol !== 'admin') return;
  estadoUsuarios.textContent = 'Cargando…';
  estadoUsuarios.hidden = false;
  try {
    const usuarios = await listarUsuarios();
    cuerpoUsuarios.textContent = '';
    usuarios.forEach((u) => cuerpoUsuarios.appendChild(filaUsuario(u)));
    estadoUsuarios.hidden = usuarios.length > 0;
  } catch (error) {
    estadoUsuarios.textContent = error.message;
  }
}

formUsuario.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  const datos = Object.fromEntries(new FormData(formUsuario));
  try {
    await crearUsuario({
      email: String(datos.email).trim().toLowerCase(),
      nombre: String(datos.nombre).trim(),
      rol: String(datos.rol),
    });
    formUsuario.reset();
    mostrarAviso('Usuario habilitado. Ya puede entrar con esa cuenta de Google.', 'ok');
    await cargarUsuarios();
    await cargarBitacora();
  } catch (error) {
    manejarError(error);
  }
});

// =================================================================
// Bitácora (solo admin)
// =================================================================

async function cargarBitacora() {
  if (usuarioActual?.rol !== 'admin') return;
  try {
    const entradas = await leerBitacora(50);
    cuerpoBitacora.textContent = '';
    entradas.forEach((entrada) => {
      const fila = document.createElement('tr');
      const celdas = [
        fechaCorta(entrada.momento),
        entrada.email ?? '—',
        entrada.accion,
        [entrada.entidad, entrada.detalle].filter(Boolean).join(' · ') || '—',
      ];
      celdas.forEach((texto, indice) => {
        const celda = document.createElement('td');
        celda.textContent = texto;
        if (indice === 0) celda.className = 'celda-fecha';
        fila.appendChild(celda);
      });
      cuerpoBitacora.appendChild(fila);
    });
    estadoBitacora.hidden = entradas.length > 0;
    if (entradas.length === 0) estadoBitacora.textContent = 'Sin acciones registradas.';
  } catch (error) {
    estadoBitacora.textContent = error.message;
  }
}

// =================================================================
// Arranque
// =================================================================

document.getElementById('btn-entrar').addEventListener('click', () => entrar('/admin'));

document.getElementById('btn-salir').addEventListener('click', async () => {
  try {
    await salir();
  } catch {
    // Si el servidor no respondió, la sesión local se descarta igual.
  }
  usuarioActual = null;
  mostrarAcceso('Cerraste la sesión.');
});

async function iniciar() {
  const errorDeAcceso = tomarErrorDeAcceso();
  const estado = await leerSesion();

  if (estado.sinConexion) {
    mostrarAcceso('No se pudo contactar al servidor.');
    return;
  }

  if (!estado.configurado) {
    mostrarAcceso(
      'La autenticación no está configurada en el servidor: faltan las credenciales de Google.'
    );
    document.getElementById('btn-entrar').disabled = true;
    return;
  }

  if (!estado.autenticado) {
    mostrarAcceso(errorDeAcceso);
    return;
  }

  mostrarPanel(estado.usuario);
  await cargarEstaciones();
  await cargarUsuarios();
  await cargarBitacora();
}

iniciar();
