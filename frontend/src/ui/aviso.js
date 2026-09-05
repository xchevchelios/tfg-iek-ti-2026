/**
 * Avisos no bloqueantes.
 *
 * Reemplaza a alert(), que congela la pestania y no se puede estilar. Ademas,
 * en un dashboard que recibe datos en vivo, un alert detiene el renderizado
 * hasta que alguien lo cierre.
 */
let contenedor = null;

function asegurarContenedor() {
  if (contenedor) return contenedor;
  contenedor = document.createElement('div');
  contenedor.className = 'avisos';
  contenedor.setAttribute('role', 'status');
  contenedor.setAttribute('aria-live', 'polite');
  document.body.appendChild(contenedor);
  return contenedor;
}

export function mostrarAviso(mensaje, tipo = 'info', milisegundos = 4000) {
  const nodo = document.createElement('div');
  nodo.className = `aviso aviso--${tipo}`;
  nodo.textContent = mensaje;
  asegurarContenedor().appendChild(nodo);

  setTimeout(() => {
    nodo.classList.add('aviso--saliendo');
    setTimeout(() => nodo.remove(), 300);
  }, milisegundos);
}
