# Simulador del gateway

Reemplaza al ESP32 para probar el sistema completo sin hardware. Emula los nodos, el enlace LoRa y el gateway, y publica en el broker MQTT real **exactamente** el mismo topic y el mismo JSON que el firmware.

## Qué replica del gateway real

| Aspecto | Firmware (`gateway_master.ino`) | Simulador |
|---|---|---|
| Estrategia | Polling master/slave, un nodo por vez | igual |
| Trama de comando | `ComandoPolling` empaquetada, 2 bytes | `struct.pack("<BB", ...)` |
| Trama de datos | `PayloadSensores` empaquetada, 19 bytes | `struct.pack("<BHffff", ...)` |
| Intervalo entre nodos | 2000 ms | `--intervalo 2` |
| Timeout | 3000 ms | `--timeout 3` |
| Topic | `estaciones/estacion_%02d/data` | igual |
| Payload | `timestamp` UTC con sufijo `Z`, `pm25`, `pm10`, `temperatura`, `humedad` | igual |
| Last Will | `estaciones/estado` → `{"gateway_status":"OFFLINE"}`, retenido | igual |
| Al conectar | publica `ONLINE` retenido | igual |
| TLS | `setInsecure()` (sin verificar certificado) | `tls_insecure_set(True)` |

Que las tramas binarias tengan el mismo layout no es decorativo: si alguien cambia una `struct` del firmware y se olvida de la otra punta, el simulador deja de coincidir en tamaño y el problema aparece acá y no en el campo.

## Requisitos

`paho-mqtt`, que ya está en `api/requirements.txt`. Desde la VM alcanza con el venv del backend:

```bash
~/tfg-sistema/api/venv/bin/python simulador/gateway_sim.py --help
```

## Uso

**Probar sin tocar el broker** (imprime lo que publicaría):

```bash
python3 gateway_sim.py --seco
```

**En vivo contra el broker real:**

```bash
python3 gateway_sim.py --clave 'la-clave-de-tfg_nodo'
```

**Llenar el dashboard con una semana de historia**, una muestra cada 5 minutos, y después seguir en vivo:

```bash
python3 gateway_sim.py --clave '...' --historico 7 --cadencia 300
```

Agregando `--solo-historico` genera los datos y sale.

**Ejercitar el camino del timeout** (30 % de tramas perdidas):

```bash
python3 gateway_sim.py --clave '...' --perdida 0.3
```

**Contra un broker local sin TLS:**

```bash
python3 gateway_sim.py --broker localhost --puerto 1883 --sin-tls --clave '...'
```

## Los datos que genera

No son números al azar. Cada nodo tiene un modelo con ciclo diario y reversión a la media, de modo que dos muestras consecutivas se parecen entre sí — una serie de ruido blanco se nota a simple vista en el gráfico.

- **PM2.5**: base de ~11 µg/m³ con dos picos de tráfico, a las 7:30 y a las 18:30 hora local.
- **PM10**: siempre mayor que el PM2.5, con la relación típica de aire urbano (1.8 a 2.6).
- **Temperatura**: ciclo diario con máxima a las 15:00 y mínima cerca de las 3:00, entre ~18 y ~32 °C.
- **Humedad**: en contrafase con la temperatura, entre 40 % y 90 %.

El segundo nodo lleva un sesgo de +6 µg/m³ en material particulado, para que las dos estaciones no se vean idénticas.

El ciclo diario se calcula en hora local del campus (`--huso -3`), no con el reloj de la máquina: la VM corre en UTC, y tomar su hora correría los picos tres horas.

## Advertencia

El simulador publica con el usuario `tfg_nodo`, el mismo del gateway real. Si lo dejás corriendo **mientras el gateway físico también publica**, las dos fuentes escriben en la misma base y los datos quedan mezclados sin forma de distinguirlos después. Usalo con el gateway apagado, o cambiá los ids de nodo con `--nodos`.
