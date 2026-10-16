#!/usr/bin/env bash
# Compila los dos firmwares contra las librerias reales y corre las pruebas,
# todo en la computadora (no hace falta el toolchain del ESP32).
#   bash firmware/pruebas/correr.sh
# Las librerias se descargan una vez en firmware/pruebas/.deps/.
set -euo pipefail
cd "$(dirname "$0")"
DEPS=.deps
mkdir -p "$DEPS/json6" "$DEPS/bin"

bajar() { [ -s "$2" ] || curl -fsSL --max-time 60 -o "$2" "$1"; }
GH=https://raw.githubusercontent.com
bajar $GH/knolleary/pubsubclient/master/src/PubSubClient.h   $DEPS/PubSubClient.h
bajar $GH/knolleary/pubsubclient/master/src/PubSubClient.cpp $DEPS/PubSubClient.cpp
bajar $GH/adafruit/DHT-sensor-library/master/DHT.h           $DEPS/DHT.h
bajar $GH/ricki-z/SDS011/master/SDS011.h                     $DEPS/SDS011.h
bajar https://github.com/bblanchon/ArduinoJson/releases/download/v7.2.1/ArduinoJson-v7.2.1.h  $DEPS/ArduinoJson.h
bajar https://github.com/bblanchon/ArduinoJson/releases/download/v6.21.5/ArduinoJson-v6.21.5.h $DEPS/json6/ArduinoJson.h

JSON="-DARDUINOJSON_ENABLE_PROGMEM=0 -DARDUINOJSON_ENABLE_ARDUINO_STRING=0 -DARDUINOJSON_ENABLE_ARDUINO_STREAM=0 -DARDUINOJSON_ENABLE_ARDUINO_PRINT=0"
INO="g++ -std=gnu++17 -fsyntax-only -Wall -Wextra -Wno-unused-parameter -Wno-deprecated-declarations -Werror -x c++ -include Arduino.h -I simulado $JSON"

# El gateway necesita secretos.h; si no esta (otra computadora), se compila con el ejemplo.
SECRETOS=""
[ -f ../gateway/secretos.h ] || { mkdir -p $DEPS/secretos; cp ../gateway/secretos.example.h $DEPS/secretos/secretos.h; SECRETOS="-I $DEPS/secretos"; }

echo "== Las copias compartidas son identicas"
cmp ../gateway/protocolo.h ../nodo/protocolo.h
cmp ../gateway/ensamblador.h ../nodo/ensamblador.h

echo "== gateway.ino compila (ArduinoJson 7 y 6, PubSubClient real)"
$INO $SECRETOS -I $DEPS -I ../gateway ../gateway/gateway.ino
$INO $SECRETOS -I $DEPS/json6 -I $DEPS -I ../gateway ../gateway/gateway.ino
echo "== nodo.ino compila"
$INO -I $DEPS -I ../nodo ../nodo/nodo.ino

echo "== Logica del protocolo (red simulada)"
g++ -std=gnu++17 -O1 -Wall -Wextra -o $DEPS/bin/logica prueba_logica.cpp && $DEPS/bin/logica

echo "== Gateway completo (configuracion, NVS, rondas, publicacion)"
g++ -std=gnu++17 -O1 -w $JSON $SECRETOS -I simulado_mqtt -I simulado -I $DEPS -I ../gateway \
    -o $DEPS/bin/gateway prueba_gateway.cpp simulado/simulado.cpp && $DEPS/bin/gateway

echo "== Error de retencion del firmware v1 (PubSubClient real)"
g++ -std=gnu++17 -w -I simulado -I $DEPS -o $DEPS/bin/retenido \
    prueba_pubsub_retenido.cpp simulado/simulado.cpp $DEPS/PubSubClient.cpp && $DEPS/bin/retenido

echo; echo "Todo en orden."
