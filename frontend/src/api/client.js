/**
 * Cliente HTTP de la API.
 *
 * Centraliza la URL base, el manejo de errores y el token de escritura, para
 * que ningun modulo de interfaz tenga que saber como se arma una peticion.
 */
import { API_BASE_URL } from '../config.js';

const CLAVE_TOKEN = 'tfg_api_token';

/** Error con el codigo HTTP, para que quien llame pueda distinguir 401 de 500. */
export class ErrorApi extends Error {
  constructor(mensaje, estado, opciones) {
    super(mensaje, opciones);
    this.name = 'ErrorApi';
    this.estado = estado;
  }
}

/**
 * El token de administracion NO se compila en el bundle: lo escribe la persona
 * en la pagina de gestion y vive solo en sessionStorage, es decir mientras dure
 * la pestania. Un sitio estatico no puede guardar secretos, asi que el secreto
 * lo aporta quien opera.
 */
export function guardarToken(token) {
  sessionStorage.setItem(CLAVE_TOKEN, token);
}

export function leerToken() {
  return sessionStorage.getItem(CLAVE_TOKEN) ?? '';
}

export function olvidarToken() {
  sessionStorage.removeItem(CLAVE_TOKEN);
}

async function pedir(ruta, opciones = {}) {
  const { autenticado = false, ...resto } = opciones;

  const cabeceras = { ...(resto.headers ?? {}) };
  if (resto.body !== undefined) {
    cabeceras['Content-Type'] = 'application/json';
  }
  if (autenticado) {
    cabeceras['X-API-Token'] = leerToken();
  }

  let respuesta;
  try {
    respuesta = await fetch(`${API_BASE_URL}${ruta}`, { ...resto, headers: cabeceras });
  } catch (causa) {
    // fetch solo rechaza por fallo de red, DNS, TLS o CORS: nunca por un
    // codigo de estado. Vale la pena distinguirlo para el mensaje al usuario.
    throw new ErrorApi('No se pudo contactar al servidor. Verifique la conexion.', 0, {
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
