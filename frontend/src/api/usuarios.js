/** Gestión de usuarios. Solo responde para el rol admin. */
import { api } from './client.js';

export function listarUsuarios() {
  return api.get('/api/usuarios');
}

export function crearUsuario(datos) {
  return api.post('/api/usuarios', datos);
}

export function actualizarUsuario(id, datos) {
  return api.put(`/api/usuarios/${id}`, datos);
}

export function darDeBajaUsuario(id) {
  return api.del(`/api/usuarios/${id}`);
}

export function leerBitacora(limite = 50) {
  return api.get(`/api/usuarios/bitacora?limite=${limite}`);
}
