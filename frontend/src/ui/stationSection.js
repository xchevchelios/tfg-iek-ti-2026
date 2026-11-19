/**
 * Seccion de una estacion: se construye desde los datos que devuelve la API.
 *
 * Aca esta el cambio de fondo respecto de la version anterior: antes el HTML
 * tenia ocho <canvas> con ids fijos ('chart_estacion_01_pm25', ...) y el JS los
 * buscaba por nombre, asi que sumar un nodo obligaba a editar el HTML y el JS.
 * Ahora la seccion se genera para cualquier estacion que exista en la base.
 */
import { METRICAS } from '../config.js';
import { crearGraficoVivo } from '../charts/chart.js';

/**
 * @param {{id: string, nombre: string}} estacion
 * @param {{alPedirHistorial: (estacionId: string, metrica: string, etiqueta: string) => void}} acciones
 * @returns {{elemento: HTMLElement, graficos: Record<string, import('chart.js').Chart>}}
 */
export function crearSeccionEstacion(estacion, { alPedirHistorial }) {
  const seccion = document.createElement('section');
  seccion.className = 'station-section';
  seccion.dataset.estacion = estacion.id;

  const titulo = document.createElement('h2');
  titulo.className = 'station-title';
  titulo.textContent = estacion.nombre;
  // Se muestra cuando la ultima lectura tiene humedad >= 80 %: el SDS011 es
  // menos confiable y el PM viene corregido por humedad (si hay correccion).
  const avisoHumedad = document.createElement('span');
  avisoHumedad.className = 'aviso-humedad';
  avisoHumedad.textContent = 'Humedad alta: PM menos confiable';
  avisoHumedad.title =
    'Con humedad relativa de 80 % o más el sensor de partículas mide de más. ' +
    'Si la estación tiene la corrección por humedad alta, el valor ya viene corregido.';
  avisoHumedad.hidden = true;
  titulo.appendChild(avisoHumedad);
  seccion.appendChild(titulo);

  const grilla = document.createElement('div');
  grilla.className = 'chart-grid';
  seccion.appendChild(grilla);

  const graficos = {};

  for (const { clave, etiqueta, unidad } of METRICAS) {
    const tarjeta = document.createElement('div');
    tarjeta.className = 'chart-card';

    const encabezado = document.createElement('h4');
    encabezado.textContent = etiqueta;
    tarjeta.appendChild(encabezado);

    const lienzo = document.createElement('canvas');
    tarjeta.appendChild(lienzo);
    grilla.appendChild(tarjeta);

    graficos[clave] = crearGraficoVivo(lienzo, { metrica: clave, unidad });

    // El grafico completo es el disparador del historial. Se usa un boton
    // invisible por accesibilidad: un <canvas> con onclick no es alcanzable
    // por teclado.
    lienzo.style.cursor = 'pointer';
    lienzo.setAttribute('role', 'button');
    lienzo.setAttribute('tabindex', '0');
    lienzo.setAttribute('aria-label', `Ver historial de ${etiqueta} en ${estacion.nombre}`);

    const abrir = () => alPedirHistorial(estacion.id, clave, etiqueta);
    lienzo.addEventListener('click', abrir);
    lienzo.addEventListener('keydown', (evento) => {
      if (evento.key === 'Enter' || evento.key === ' ') {
        evento.preventDefault();
        abrir();
      }
    });
  }

  const marcarHumedadAlta = (activa) => {
    avisoHumedad.hidden = !activa;
  };

  return { elemento: seccion, graficos, marcarHumedadAlta };
}
