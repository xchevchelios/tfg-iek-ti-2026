import { defineConfig, loadEnv } from 'vite';
import { resolve } from 'node:path';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendDesarrollo = env.VITE_DEV_BACKEND ?? 'http://127.0.0.1:5000';

  return {
    build: {
      outDir: 'dist',
      // Sitio multipagina: cada HTML es un punto de entrada propio.
      rollupOptions: {
        input: {
          dashboard: resolve(__dirname, 'index.html'),
          gestion: resolve(__dirname, 'gestion.html'),
        },
        output: {
          // Las librerias van en chunks propios: cambian mucho menos que el
          // codigo de la aplicacion, asi que el navegador las conserva en cache
          // entre despliegues en vez de volver a bajarlas en cada build.
          manualChunks: {
            graficos: ['chart.js/auto', 'chartjs-adapter-luxon', 'luxon'],
            mapa: ['leaflet'],
            mqtt: ['mqtt'],
          },
        },
      },
    },

    server: {
      port: 5173,
      // En desarrollo, Vite hace de proxy hacia el backend real. El navegador
      // ve un unico origen (localhost:5173), asi que CORS no interviene
      // mientras se programa: solo aplica en produccion, donde el frontend
      // vive en Cloudflare y la API en la VM.
      proxy: {
        '/api': {
          target: backendDesarrollo,
          changeOrigin: true,
          secure: true,
        },
        '/mqtt': {
          target: backendDesarrollo,
          changeOrigin: true,
          secure: true,
          ws: true,
        },
      },
    },
  };
});
