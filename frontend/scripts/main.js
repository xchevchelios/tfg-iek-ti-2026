// =================================================================
// MÓDULOS Y CONFIGURACIÓN
// =================================================================

import { brokerIp, brokerPort, brokerPath } from './config.js'; 
import { createChart, updateChartData, getMetricColor } from './chart-logic.js';

// =================================================================
// VARIABLES GLOBALES
// =================================================================

const charts = {};
let client;
let historialChart;
let modalContainer, modalCerrarBtn, modalTitulo, rangoBtns, 
    customRangoBtn, fechaInicio, fechaFin;
let currentModalParams = {
    stationId: null,
    metrica: null,
    label: null
};

// =================================================================
// LÓGICA DE INICIALIZACIÓN
// =================================================================

function initialize() {
    console.log("Inicializando aplicación...");
    initializeCharts();
    initializeModal(); 
    fetchInitialData();
    connectMqtt();
}

function initializeCharts() {
    // Estación 1
    charts['estacion_01_pm25'] = createChart('chart_estacion_01_pm25', 'µg/m³', 'pm25');
    charts['estacion_01_pm10'] = createChart('chart_estacion_01_pm10', 'µg/m³', 'pm10');
    charts['estacion_01_temperatura'] = createChart('chart_estacion_01_temperatura', '°C', 'temperatura');
    charts['estacion_01_humedad'] = createChart('chart_estacion_01_humedad', '% HR', 'humedad');
    
    // Estación 2
    charts['estacion_02_pm25'] = createChart('chart_estacion_02_pm25', 'µg/m³', 'pm25');
    charts['estacion_02_pm10'] = createChart('chart_estacion_02_pm10', 'µg/m³', 'pm10');
    charts['estacion_02_temperatura'] = createChart('chart_estacion_02_temperatura', '°C', 'temperatura');
    charts['estacion_02_humedad'] = createChart('chart_estacion_02_humedad', '% HR', 'humedad');

    addClickEventsToCharts();
}

// =================================================================
// LÓGICA DE DATOS INICIALES (API)
// =================================================================

async function fetchInitialData() {
    console.log("Obteniendo datos iniciales de la API...");
    try {
        const response = await fetch('/api/datos_recientes');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        data.forEach(record => {
            const stationId = record.station_id;
            const chartKeyBase = `${stationId}_`;
            const timestamp = new Date(record.timestamp).toLocaleTimeString('es-PY'); 

            updateChartData(charts[chartKeyBase + 'pm25'], timestamp, record.pm25);
            updateChartData(charts[chartKeyBase + 'pm10'], timestamp, record.pm10);
            updateChartData(charts[chartKeyBase + 'temperatura'], timestamp, record.temperatura);
            updateChartData(charts[chartKeyBase + 'humedad'], timestamp, record.humedad);
        });
    } catch (e) {
        console.error("No se pudieron obtener los datos iniciales:", e);
    }
}

// =================================================================
// LÓGICA DE TIEMPO REAL (MQTT)
// =================================================================

function connectMqtt() {
    client = new Paho.MQTT.Client(brokerIp, Number(brokerPort), brokerPath, `dashboard_${new Date().getTime()}`);
    client.onConnectionLost = onConnectionLost;
    client.onMessageArrived = onMessageArrived;

    client.connect({
        userName: "web_publico",     // <-- NUEVO: Usuario de Mosquitto
        password: "web2026",   // <-- NUEVO: Contraseña de Mosquitto
        useSSL: true,             // Fundamental para WSS a través de Nginx
        onSuccess: onConnect,
        onFailure: (e) => {
            console.error("Fallo al conectar con MQTT:", e);
            // Intentar reconectar si falla
            setTimeout(connectMqtt, 5000);
        }
    });
}

function onConnect() {
    console.log("Conectado al broker MQTT vía WebSockets Seguro (WSS).");
    client.subscribe("estaciones/+/data");
}

function onConnectionLost(responseObject) {
    if (responseObject.errorCode !== 0) {
        console.error(`Conexión MQTT perdida: ${responseObject.errorMessage}`);
        setTimeout(connectMqtt, 5000);
    }
}

function onMessageArrived(message) {
    try {
        const topicParts = message.destinationName.split('/');
        const stationId = topicParts[1];
        const payload = JSON.parse(message.payloadString);

        const chartKeyBase = `${stationId}_`;
        const timestamp = new Date(payload.timestamp).toLocaleTimeString('es-PY');

        updateChartData(charts[chartKeyBase + 'pm25'], timestamp, payload.pm25);
        updateChartData(charts[chartKeyBase + 'pm10'], timestamp, payload.pm10);
        updateChartData(charts[chartKeyBase + 'temperatura'], timestamp, payload.temperatura);
        updateChartData(charts[chartKeyBase + 'humedad'], timestamp, payload.humedad);

    } catch (e) {
        console.error("Error procesando mensaje MQTT:", e);
    }
}

// =================================================================
// LÓGICA DE LA VENTANA MODAL DE HISTORIAL
// =================================================================

function initializeModal() {
    modalContainer = document.getElementById('modal-historial-container');
    modalCerrarBtn = document.getElementById('modal-cerrar');
    modalTitulo = document.getElementById('modal-titulo');
    rangoBtns = document.querySelectorAll('.rango-btn');
    customRangoBtn = document.getElementById('btn-custom-rango');
    fechaInicio = document.getElementById('fecha-inicio');
    fechaFin = document.getElementById('fecha-fin');

    modalCerrarBtn.onclick = cerrarModal;
    
    // Cerrar modal al hacer click fuera del contenido
    modalContainer.onclick = (event) => {
        if (event.target === modalContainer) {
            cerrarModal();
        }
    };
    
    rangoBtns.forEach(btn => {
        btn.onclick = () => handleRangoClick(btn);
    });
    
    customRangoBtn.onclick = handleCustomRangoClick;
}

function addClickEventsToCharts() {
    // Estación 1
    charts['estacion_01_pm25'].canvas.onclick = () => mostrarModalHistorial('estacion_01', 'pm25', 'PM2.5');
    charts['estacion_01_pm10'].canvas.onclick = () => mostrarModalHistorial('estacion_01', 'pm10', 'PM10');
    charts['estacion_01_temperatura'].canvas.onclick = () => mostrarModalHistorial('estacion_01', 'temperatura', 'Temperatura');
    charts['estacion_01_humedad'].canvas.onclick = () => mostrarModalHistorial('estacion_01', 'humedad', 'Humedad');
    
    // Estación 2
    charts['estacion_02_pm25'].canvas.onclick = () => mostrarModalHistorial('estacion_02', 'pm25', 'PM2.5');
    charts['estacion_02_pm10'].canvas.onclick = () => mostrarModalHistorial('estacion_02', 'pm10', 'PM10');
    charts['estacion_02_temperatura'].canvas.onclick = () => mostrarModalHistorial('estacion_02', 'temperatura', 'Temperatura');
    charts['estacion_02_humedad'].canvas.onclick = () => mostrarModalHistorial('estacion_02', 'humedad', 'Humedad');

    Object.values(charts).forEach(chart => {
        chart.canvas.style.cursor = 'pointer';
    });
}

function cerrarModal() {
    modalContainer.classList.remove('modal-container-visible');
    modalContainer.classList.add('modal-container-oculto');
    if (historialChart) {
        historialChart.destroy();
        historialChart = null;
    }
    // Limpiar el estado del modal
    rangoBtns.forEach(btn => btn.classList.remove('activo'));
    fechaInicio.value = '';
    fechaFin.value = '';
}

function mostrarModalHistorial(stationId, metrica, label) {
    // 1. Guardar el estado actual
    currentModalParams = { stationId, metrica, label };

    // 2. Actualizar título
    const numeroEstacion = stationId.split('_')[1];
    modalTitulo.innerText = `Historial de ${label} - Estación ${numeroEstacion}`;
    
    // 3. Mostrar modal
    modalContainer.classList.remove('modal-container-oculto');
    modalContainer.classList.add('modal-container-visible');
    
    // 4. Crear el gráfico del modal con el color correcto
    if (historialChart) {
        historialChart.destroy();
    }
    const ctx = document.getElementById('chart-historial').getContext('2d');
    
    // Obtener el color base para esta métrica
    const colorString = getMetricColor(metrica, 0); // Obtenemos el color base
    const backgroundColor = colorString.replace(')', ', 0.1)').replace('rgb', 'rgba');

    historialChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: label,
                data: [],
                borderColor: colorString,
                backgroundColor: 'transparent', // Sin relleno
                fill: false, // Desactivar el relleno completamente
                pointRadius: 1, 
                pointHoverRadius: 7,
                pointBackgroundColor: [],
                pointBorderColor: [],
                borderWidth: 2,
                tension: 0.1, // Líneas rectas (sin curvas)
                segment: {
                    borderColor: function(ctx) {
                        // Color dinámico basado en el valor del punto
                        const value = ctx.p1.parsed.y;
                        return getMetricColor(metrica, value);
                    }
                }
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            metricType: metrica, // Guardamos el tipo de métrica
            scales: {
                x: {
                    type: 'time', 
                    time: {
                        tooltipFormat: 'dd/MM/yyyy HH:mm',
                        displayFormats: {
                            hour: 'HH:mm',
                            day: 'dd/MM',
                            month: 'MM/yyyy'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Fecha y Hora'
                    },
                    ticks: {
                        autoSkip: true, 
                        maxRotation: 0, 
                        maxTicksLimit: 12
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: label
                    }
                }
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            return `${context.dataset.label}: ${context.parsed.y.toFixed(2)}`;
                        }
                    }
                }
            }
      }
    });

    // 5. Marcar el botón de 24h como activo y cargar sus datos por defecto
    const btn24h = document.querySelector('.rango-btn[data-rango="24h"]');
    if (btn24h) {
        handleRangoClick(btn24h);
    }
}

function handleRangoClick(clickedBtn) {
    // Marcar el botón activo
    rangoBtns.forEach(btn => btn.classList.remove('activo'));
    clickedBtn.classList.add('activo');
    
    // Limpiar fechas personalizadas
    fechaInicio.value = '';
    fechaFin.value = '';

    const rango = clickedBtn.dataset.rango;
    fetchYActualizarGrafico({ rango });
}

function handleCustomRangoClick() {
    const start = fechaInicio.value;
    const end = fechaFin.value;

    if (start && end) {
        rangoBtns.forEach(btn => btn.classList.remove('activo'));
        fetchYActualizarGrafico({ start, end });
    } else {
        alert("Por favor, selecciona una fecha de inicio y una de fin.");
    }
}

async function fetchYActualizarGrafico(params) {
    const { stationId, metrica, label } = currentModalParams;
    
    const url = new URL('/api/historial', window.location.origin);
    url.searchParams.append('estacion', stationId);
    url.searchParams.append('metrica', metrica);
    
    if (params.rango) {
        url.searchParams.append('rango', params.rango);
    } else if (params.start && params.end) {
        url.searchParams.append('start', params.start);
        url.searchParams.append('end', params.end);
    }

    // Asegurarse de que el gráfico exista antes de limpiarlo
    if (!historialChart) {
        console.error("El gráfico del historial no se inicializó.");
        return;
    }
    
    // Limpiar datos existentes
    historialChart.data.datasets[0].data = [];
    historialChart.update();

    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`Error en la API: ${response.statusText}`);
        }
        const data = await response.json(); 

        if (data.length === 0) {
            console.log("No se encontraron datos para este rango.");
            alert("No hay datos disponibles para el rango seleccionado.");
        } else {
            // Actualizar colores de los puntos según sus valores
            const dataset = historialChart.data.datasets[0];
            const metricType = historialChart.options.metricType;

            dataset.pointBackgroundColor = data.map(point => getMetricColor(metricType, point.y));
            dataset.pointBorderColor = dataset.pointBackgroundColor;

            historialChart.data.datasets[0].data = data; 
            historialChart.options.scales.y.title.text = label; 
            historialChart.update();
            console.log(`Cargados ${data.length} registros para ${label}`);
        }

    } catch (e) {
        console.error("No se pudieron obtener datos del historial:", e);
        alert("Error al cargar los datos del historial. Por favor, intenta nuevamente.");
    }
}

// =================================================================
// INICIO DE LA APLICACIÓN
// =================================================================
document.addEventListener('DOMContentLoaded', initialize);
