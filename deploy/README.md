# Despliegue

El sistema tiene dos mitades que se despliegan por separado:

- **Frontend**: estatico, se publica solo en Cloudflare con cada push a `main`.
- **Backend + broker**: viven en la VM y se despliegan a mano con los pasos de abajo.

---

## Frontend (Cloudflare)

El proyecto de Cloudflare tiene que quedar configurado asi:

| Campo | Valor |
|---|---|
| Root directory | `frontend` |
| Build command | `npm run build` |
| Build output directory | `dist` |

> Si el proyecto fue creado antes de que el frontend tuviera build, hay que
> **actualizar estos tres campos**: antes no habia build command y la salida era
> la raiz. Sin el cambio, Cloudflare publica el codigo fuente en vez del sitio
> compilado.

Las URLs y credenciales salen de `frontend/.env.production`. Cambiar de backend
es editar ese archivo, no tocar codigo.

Para trabajar en local:

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

En desarrollo, Vite hace de proxy hacia el backend real (`VITE_DEV_BACKEND`), asi
que el navegador ve un solo origen y CORS no interviene.

---

## Backend (VM)

### 1. Dependencias

```bash
cd ~/tfg-sistema
source venv/bin/activate
pip install -r api/requirements.txt
```

### 2. Configuracion

```bash
cd api
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # token de escritura
nano .env
```

El mismo `.env` lo leen dos consumidores: `python-dotenv` cuando se corre a mano,
y systemd via `EnvironmentFile`. Por eso el formato tiene que ser `CLAVE=valor`
plano, sin `export` y sin comillas innecesarias.

Campos que hay que completar si o si:

- `DB_PATH`: ruta absoluta del `sensores.db` de produccion.
- `MQTT_USER` / `MQTT_PASS`: credenciales del usuario `tfg_nodo` del broker.
- `CORS_ORIGINS`: el dominio del frontend en Cloudflare.
- `API_TOKEN`: si queda vacio, las escrituras de la pagina de gestion se
  rechazan con 503. Es deliberado: el modo abierto tiene que ser una decision
  explicita, no el estado por defecto.

### 3. Esquema y migracion de la base

```bash
python3 db.py
```

Es idempotente. Crea la tabla `estaciones` si falta, activa WAL, crea el indice
compuesto `(station_id, timestamp)` y da de alta las estaciones que hasta ahora
solo existian de forma implicita como `station_id` dentro de `mediciones`. Las
coordenadas quedan en `NULL` y se cargan desde la pagina de gestion.

### 4. Servicios

```bash
sudo cp deploy/tfg-api.service deploy/tfg-ingesta.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tfg-api tfg-ingesta
systemctl status tfg-api tfg-ingesta
```

Revisar las rutas de los units si el repo no esta en `/home/xxv4th0xx/tfg-sistema`.

Si el proceso viejo de `mqtt_a_sqlite.py` sigue corriendo en tmux, hay que
matarlo antes: si no, quedan dos escritores insertando cada medicion dos veces.

```bash
tmux ls                    # buscar la sesion
tmux kill-session -t <nombre>
```

### 5. nginx

Aplicar `deploy/nginx-tfg.conf` sobre el bloque 443 de
`/etc/nginx/sites-enabled/default`, y despues:

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run
```

El `certbot renew --dry-run` **no es opcional**: el redirect general puede tapar
el challenge de ACME, y una renovacion rota no se nota hasta que el certificado
vence y se cae tambien el MQTTS del gateway.

### 6. Retirar el frontend viejo

Recien despues de verificar que el dashboard en Cloudflare carga datos:

```bash
sudo mkdir -p /var/www/_backup_front
sudo mv /var/www/html/{templates,scripts,styles,images} /var/www/_backup_front/
```

Cuando se confirme que nada quedo apuntando ahi, se puede eliminar tambien el
endpoint `/api/datos_recientes`, que existe solo para ese frontend.

---

## Verificacion

```bash
curl -s https://air-quality-campus-una.duckdns.org/api/salud
curl -s https://air-quality-campus-una.duckdns.org/api/estaciones
```

`/api/salud` devuelve `{"estado":"ok","escrituras":true|false}`. Si `escrituras`
es `false`, falta `API_TOKEN` en el `.env` del servidor.
