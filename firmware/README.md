# Firmware — protocolo LoRa v2

Dos sketches para ESP32 (placa *ESP32 Dev Module* en el IDE de Arduino):

| Sketch | Librerías (Gestor de librerías del IDE) |
|---|---|
| `gateway/` | PubSubClient (Nick O'Leary), ArduinoJson (Benoit Blanchon, v6 o v7) |
| `nodo/` | SDS011 sensor Library (ricki-z), DHT sensor library (Adafruit) + Adafruit Unified Sensor |

## Cómo funciona una ronda

1. El gateway difunde `MEDIR` a todos los nodos (dos veces, porque la difusión no tiene confirmación).
2. Cada nodo despierta su SDS011, mide durante la ventana de estabilización, promedia el tramo final y lo vuelve a dormir.
3. El gateway pide el dato nodo por nodo con `ENVIAR`. Cada respuesta lleva el número de ronda: si no coincide, el nodo no escuchó la orden y el dato se descarta.

Con el período por defecto (300 s) y una ventana de 30 s, el SDS011 trabaja el 10 % del tiempo.

## Antes de flashear

- **Gateway:** copiar `gateway/secretos.example.h` como `gateway/secretos.h` y completarlo. `secretos.h` no se versiona.
- **Nodo:** poner en `ID_NODO` la dirección LoRa de ese nodo (1 a 254), la misma que se carga en el panel web.
- **v1 y v2 no se entienden entre sí.** Hay que flashear el gateway y todos los nodos en la misma visita. Un equipo con firmware viejo no rompe nada: simplemente queda sin responder.

## Configuración remota (mensajes MQTT retenidos)

| Topic | Quién publica | Contenido |
|---|---|---|
| `gateway/config/nodos` | API, al cambiar estaciones | lista de direcciones |
| `gateway/config/parametros` | `gestor.py`, opción 7 | período, ventana, timeout, publicar |
| `gateway/estado/aplicado` | el gateway, al cerrar cada ronda | lo que efectivamente aplicó y cómo le fue |

Una configuración inválida se rechaza entera; el motivo aparece en `gateway/estado/aplicado`. La última configuración aceptada queda en la NVS del gateway.

## Pruebas

```
bash firmware/pruebas/correr.sh
```

Compila los dos sketches contra las librerías reales, con Arduino y FreeRTOS simulados, y corre tres pruebas: la lógica del protocolo sobre una red simulada, el gateway completo y la demostración del error de retención del firmware v1.
