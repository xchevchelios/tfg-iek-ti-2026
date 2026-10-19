/**
 * Pestanias del panel.
 *
 * La pestania activa vive en el hash de la URL (#gateway, #trafico, ...): se
 * puede recargar o compartir el enlace y se vuelve a la misma vista, y el
 * boton "atras" del navegador funciona. Sigue el patron de pestanias de
 * WAI-ARIA, con flechas para moverse entre ellas.
 */
export function iniciarPestanias(nav, { porDefecto, alMostrar = () => {} }) {
  const botones = [...nav.querySelectorAll('[data-pestania]')];
  const paneles = new Map(
    [...document.querySelectorAll('[data-panel]')].map((p) => [p.dataset.panel, p])
  );
  let actual = null;

  const visibles = () => botones.filter((b) => !b.hidden);
  const disponible = (nombre) => visibles().some((b) => b.dataset.pestania === nombre);

  function mostrar(nombre) {
    if (!disponible(nombre)) nombre = porDefecto;
    if (nombre === actual) return;
    actual = nombre;
    botones.forEach((b) => {
      const activa = b.dataset.pestania === nombre;
      b.setAttribute('aria-selected', String(activa));
      b.tabIndex = activa ? 0 : -1;
    });
    paneles.forEach((panel, clave) => { panel.hidden = clave !== nombre; });
    alMostrar(nombre);
  }

  botones.forEach((b) => {
    b.addEventListener('click', () => {
      if (location.hash.slice(1) === b.dataset.pestania) mostrar(b.dataset.pestania);
      else location.hash = b.dataset.pestania;
    });
  });

  nav.addEventListener('keydown', (evento) => {
    if (evento.key !== 'ArrowRight' && evento.key !== 'ArrowLeft') return;
    const lista = visibles();
    const i = lista.findIndex((b) => b.dataset.pestania === actual);
    const siguiente = lista[(i + (evento.key === 'ArrowRight' ? 1 : -1) + lista.length) % lista.length];
    siguiente.focus();
    siguiente.click();
  });

  window.addEventListener('hashchange', () => mostrar(location.hash.slice(1)));

  return {
    /** Vuelve a evaluar la pestania (por ejemplo, despues de cambiar permisos). */
    mostrarDesdeHash() {
      actual = null;
      mostrar(location.hash.slice(1) || porDefecto);
    },
  };
}
