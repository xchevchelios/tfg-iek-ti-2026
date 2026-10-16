/**
 * Cliente HTTP de la API.
 *
 * Centraliza la URL base, el envio de la cookie de sesion y el token CSRF, para
 * que ningun modulo de interfaz tenga que saber como se arma una peticion.
 */
import { API_BASE_URL } from '../config.js';

/** Error con el codigo HTTP, para que quien llame pueda distinguir 401 de 500. */
export class ErrorApi extends Error {
  constructor(mensaje, estado, opciones) {
    super(mensaje, opciones);
    this.name = 'ErrorApi';
    this.estado = estado;
  }
}

/**
 * Token CSRF de la sesion actual.
 *
 * No se guarda en localStorage ni en una cookie: vive en memoria mientras dure
 * la pagina. Lo entrega /api/auth/sesion y se reenvia en cada escritura. Un
 * sitio de otro origen no puede leerlo, porque CORS le impide leer esa
 * respuesta, ni fijar la cabecera sin un preflight que la allowlist rechaza.
 */
let csrf = '';

export function fijarCsrf(valor) {
  csrf = valor ?? '';
}

async function pedir(ruta, opciones = {}) {
  const { autenticado = false, ...resto } = opciones;

  const cabeceras = { ...(resto.headers ?? {}) };
  if (resto.body !== undefined) {
    cabeceras['Content-Type'] = 'application/json';
  }
  if (autenticado) {
    cabeceras['X-CSRF-Token'] = csrf;
  }

  let respuesta;
  try {
    respuesta = await fetch(`${API_BASE_URL}${ruta}`, {
      ...resto,
      headers: cabeceras,
      // La cookie de sesion no viaja sin esto, aunque el frontend y la API
      // compartan dominio registrable: la peticion sigue siendo cross-origin.
      credentials: 'include',
    });
  } catch (causa) {
    // fetch solo rechaza por fallo de red, DNS, TLS o CORS: nunca por un
    // codigo de estado. Vale la pena distinguirlo para el mensaje al usuario.
    throw new ErrorApi('No se pudo contactar al servidor. Verificá la conexión.', 0, {
      cause: causa,
    });
  }

  if (respuesta.status === 204) return null;

  const texto = await respuesta.text();
  let cuerpo = null;
  try {
    cuerpo = texto ? JSON.parse(texto) : null;
  } catch {
    cuerpo = null;
  }

  if (!respuesta.ok) {
    const mensaje = cuerpo?.error ?? `Error ${respuesta.status} del servidor.`;
    throw new ErrorApi(mensaje, respuesta.status);
  }

  return cuerpo;
}

export const api = {
  get: (ruta) => pedir(ruta),
  post: (ruta, datos) =>
    pedir(ruta, { method: 'POST', body: JSON.stringify(datos), autenticado: true }),
  put: (ruta, datos) =>
    pedir(ruta, { method: 'PUT', body: JSON.stringify(datos), autenticado: true }),
  del: (ruta) => pedir(ruta, { method: 'DELETE', autenticado: true }),
};
