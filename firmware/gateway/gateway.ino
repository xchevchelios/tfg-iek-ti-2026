/*
 * =====================================================================
 * GATEWAY — protocolo LoRa v2 (dos fases) + configuracion remota
 * =====================================================================
 *   Core 1: radio. Rondas de medicion segun planificador.h.
 *   Core 0: red. Wi-Fi, MQTT, configuracion remota y NVS.
 * Los nucleos solo se comunican por colas de FreeRTOS: cada uno es dueño
 * de su estado, y nunca hay dos tareas tocando el cliente MQTT o la UART.
 *
 * Configuracion remota (mensajes retenidos, el broker los entrega al
 * conectar):
 *   gateway/config/nodos       lista de direcciones (la publica la API)
 *   gateway/config/parametros  periodo, ventana, timeout, publicar (gestor)
 * Lo que el gateway efectivamente aplico lo reporta en:
 *   gateway/estado/aplicado    (retenido)
 * Una configuracion invalida se rechaza entera: el gateway sigue con la
 * que tenia y el motivo aparece en el reporte. La ultima configuracion
 * aceptada se guarda en NVS, asi que arranca con ella aunque no haya red.
 *
 * Sin red, el gateway sigue interrogando a los nodos (se ve por el monitor
 * serie) y guarda hasta 64 mediciones en memoria para publicarlas al
 * reconectar. Con rondas de 5 minutos y 2 nodos, son unas 2,5 horas.
 *
 * Librerias: PubSubClient (Nick O'Leary), ArduinoJson (Benoit Blanchon).
 * Credenciales: copiar secretos.example.h como secretos.h y completarlo.
 * =====================================================================
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <time.h>

#if __has_include("secretos.h")
#include "secretos.h"
#else
#error "Falta secretos.h: copiar secretos.example.h como secretos.h y completarlo."
#endif

#include "protocolo.h"
#include "ensamblador.h"
#include "planificador.h"

#define FIRMWARE_VERSION "2.0.0"

#define RXD2    16
#define TXD2    17
#define PIN_M0  19
#define PIN_M1  18
#define LED_PIN 2

const char*    MQTT_HOST      = "mqtt.aire-campus-una.org";
const uint16_t MQTT_PUERTO    = 8883;
const char*    MQTT_CLIENT_ID = "tfg_gateway";

const char* TOPIC_ESTADO            = "estaciones/estado";   // Last Will
const char* TOPIC_CONFIG_NODOS      = "gateway/config/nodos";
const char* TOPIC_CONFIG_PARAMETROS = "gateway/config/parametros";
const char* TOPIC_APLICADO          = "gateway/estado/aplicado";

// Lista con la que arranca un gateway recien flasheado, antes de recibir
// su primera configuracion. Desde entonces manda la que guardo en NVS.
const uint8_t NODOS_INICIALES[] = {1, 2};

// Cualquier hora anterior a 2024 significa que el NTP todavia no sincronizo.
const time_t EPOCH_MINIMO = 1704067200;

const unsigned LARGO_COLA_MEDICIONES = 64;

struct ConfigRed {
  ParametrosRed params;
  uint8_t n;
  uint8_t nodos[MAX_NODOS];
};

// La medicion guarda su instante en millis() y no en hora UTC: el gateway
// puede medir antes de que el NTP sincronice (la primera ronda arranca a los
// 3 s del encendido) o durante un corte de red. La hora UTC se calcula
// recien al publicar, restando la antiguedad de la medicion a la hora actual.
struct Medicion {
  uint8_t  id_nodo;
  uint8_t  estado;
  uint32_t marca_ms;
  float    pm25, pm10, temperatura, humedad;
};

struct Resumen {
  ConfigRed config;              // la que se uso en esta ronda
  uint16_t  ronda;
  uint32_t  duracion_ms;
  bool      excedida;
  uint8_t   desenlaces[MAX_NODOS];
  uint8_t   sensores[MAX_NODOS];
  uint32_t  descartadas;
};

WiFiClientSecure clienteTLS;
PubSubClient mqtt(clienteTLS);
Preferences nvs;

QueueHandle_t colaMediciones;   // core 1 -> core 0
QueueHandle_t buzonConfig;      // core 0 -> core 1; un lugar, gana la ultima
QueueHandle_t buzonResumen;     // core 1 -> core 0; un lugar, gana la ultima

volatile bool redOk = false;          // lo escribe core 0, lo lee el LED
volatile bool rondaSinDatos = false;  // lo escribe core 1, lo lee el LED

// ---- Estado del core 0 (red) ----
ConfigRed   configDeseada;            // la ultima aceptada; es la de NVS
const char* rechazoNodos = nullptr;
const char* rechazoParametros = nullptr;
Resumen     ultimoResumen;
bool        hayResumen = false;

// ---- Estado del core 1 (radio) ----
Planificador planificador;
Ensamblador<sizeof(Respuesta)> ensamblador(MAGIA_NODO);
uint16_t rondaMarcada = RONDA_NINGUNA;
uint32_t marcaRondaMs = 0;
uint32_t descartadas = 0;

// =====================================================================
// NVS
// =====================================================================
// Preferences no guarda bloques vacios, por eso la cantidad de nodos va
// aparte: una lista vacia (todas las estaciones de baja) es valida.
void cargarConfig(ConfigRed& c) {
  c.params = PARAMETROS_POR_DEFECTO;
  c.n = sizeof(NODOS_INICIALES);
  memcpy(c.nodos, NODOS_INICIALES, c.n);

  nvs.begin("gateway", true);
  if (nvs.getBytesLength("params") == sizeof(ParametrosRed)) {
    ParametrosRed p;
    nvs.getBytes("params", &p, sizeof(p));
    if (!validarParametros(p)) c.params = p;
  }
  if (nvs.isKey("n_nodos")) {
    uint8_t n = nvs.getUChar("n_nodos", 0);
    uint8_t tmp[MAX_NODOS];
    if (n == 0) {
      c.n = 0;
    } else if (n <= MAX_NODOS && nvs.getBytes("nodos", tmp, n) == n && !validarNodos(tmp, n)) {
      memcpy(c.nodos, tmp, n);
      c.n = n;
    }
  }
  nvs.end();
}

void guardarNodos(const ConfigRed& c) {
  nvs.begin("gateway", false);
  nvs.putUChar("n_nodos", c.n);
  if (c.n > 0) nvs.putBytes("nodos", c.nodos, c.n);
  nvs.end();
}

void guardarParametros(const ConfigRed& c) {
  nvs.begin("gateway", false);
  nvs.putBytes("params", &c.params, sizeof(c.params));
  nvs.end();
}

// =====================================================================
// RED (core 0)
// =====================================================================
double redondear(float x) { return round(x * 10.0) / 10.0; }

void publicarAplicado() {
  if (!hayResumen || !mqtt.connected()) return;
  const Resumen& r = ultimoResumen;

  StaticJsonDocument<1024> doc;
  doc["firmware"] = FIRMWARE_VERSION;
  doc["ronda"] = r.ronda;
  JsonArray nodos = doc["nodos"].to<JsonArray>();
  for (uint8_t i = 0; i < r.config.n; i++) nodos.add(r.config.nodos[i]);
  doc["periodo_s"] = r.config.params.periodo_s;
  doc["estabilizacion_s"] = r.config.params.estabilizacion_s;
  doc["timeout_ms"] = r.config.params.timeout_ms;
  doc["publicar"] = r.config.params.publicar;

  JsonObject ultima = doc["ultima_ronda"].to<JsonObject>();
  ultima["duracion_ms"] = r.duracion_ms;
  ultima["excedida"] = r.excedida;
  JsonArray frescas = ultima["frescas"].to<JsonArray>();
  JsonArray viejas = ultima["viejas"].to<JsonArray>();
  JsonArray sinRespuesta = ultima["sin_respuesta"].to<JsonArray>();
  JsonArray sensorFallando = ultima["sensor_fallando"].to<JsonArray>();
  for (uint8_t i = 0; i < r.config.n; i++) {
    uint8_t d = r.config.nodos[i];
    if (r.desenlaces[i] == FRESCA) {
      frescas.add(d);
      if ((r.sensores[i] & (ESTADO_PM_OK | ESTADO_TH_OK)) != (ESTADO_PM_OK | ESTADO_TH_OK))
        sensorFallando.add(d);
    } else if (r.desenlaces[i] == VIEJA) {
      viejas.add(d);
    } else if (r.desenlaces[i] == SIN_RESPUESTA) {
      sinRespuesta.add(d);
    }
  }

  JsonObject rechazos = doc["rechazos"].to<JsonObject>();
  if (rechazoNodos) rechazos["nodos"] = rechazoNodos;
  if (rechazoParametros) rechazos["parametros"] = rechazoParametros;
  doc["descartadas"] = r.descartadas;
  doc["encendido_s"] = millis() / 1000;

  char json[1024];
  size_t n = serializeJson(doc, json, sizeof(json));
  mqtt.publish(TOPIC_APLICADO, (const uint8_t*)json, n, true);
}

void enviarConfigAlRadio() {
  xQueueOverwrite(buzonConfig, &configDeseada);
}

void procesarNodos(JsonDocument& doc) {
  JsonArrayConst lista = doc["nodos"].as<JsonArrayConst>();
  if (lista.isNull()) { rechazoNodos = "falta la lista 'nodos'"; return; }

  uint8_t tmp[MAX_NODOS];
  unsigned n = 0;
  for (JsonVariantConst v : lista) {
    if (n >= MAX_NODOS) { rechazoNodos = "mas de 32 nodos"; return; }
    if (!v.is<int>()) { rechazoNodos = "direccion no numerica"; return; }
    int d = v.as<int>();
    if (d < 1 || d > 254) { rechazoNodos = "direccion fuera de rango (1 a 254)"; return; }
    tmp[n++] = (uint8_t)d;
  }
  const char* motivo = validarNodos(tmp, n);
  if (motivo) { rechazoNodos = motivo; return; }
  rechazoNodos = nullptr;

  if (n == configDeseada.n && memcmp(tmp, configDeseada.nodos, n) == 0) {
    // El broker reenvia el mensaje retenido en cada reconexion: si no
    // cambio nada, no se reescribe la flash.
    Serial.println("[CONFIG] Lista de nodos sin cambios.");
    return;
  }
  configDeseada.n = n;
  memcpy(configDeseada.nodos, tmp, n);
  guardarNodos(configDeseada);
  enviarConfigAlRadio();
  Serial.printf("[CONFIG] Lista de nodos nueva (%u): se aplica en la proxima ronda.\n", n);
}

// false si la clave esta pero no es un entero entre 0 y `maximo`. Si no
// esta, deja el valor que habia: se puede mandar solo lo que cambia.
bool tomarEntero(JsonVariantConst v, long maximo, long& destino) {
  if (v.isNull()) return true;
  if (!v.is<long>()) return false;
  long x = v.as<long>();
  if (x < 0 || x > maximo) return false;
  destino = x;
  return true;
}

void procesarParametros(JsonDocument& doc) {
  ParametrosRed p = configDeseada.params;
  long periodo = p.periodo_s, estab = p.estabilizacion_s, timeout = p.timeout_ms;

  if (!tomarEntero(doc["periodo_s"], 65535, periodo)) { rechazoParametros = "periodo_s no es un entero valido"; return; }
  if (!tomarEntero(doc["estabilizacion_s"], 255, estab)) { rechazoParametros = "estabilizacion_s no es un entero valido"; return; }
  if (!tomarEntero(doc["timeout_ms"], 65535, timeout)) { rechazoParametros = "timeout_ms no es un entero valido"; return; }
  if (!doc["publicar"].isNull()) {
    if (!doc["publicar"].is<bool>()) { rechazoParametros = "publicar no es true/false"; return; }
    p.publicar = doc["publicar"].as<bool>();
  }
  p.periodo_s = (uint16_t)periodo;
  p.estabilizacion_s = (uint8_t)estab;
  p.timeout_ms = (uint16_t)timeout;

  const char* motivo = validarParametros(p);
  if (motivo) { rechazoParametros = motivo; return; }
  rechazoParametros = nullptr;

  if (parametrosIguales(p, configDeseada.params)) {
    Serial.println("[CONFIG] Parametros sin cambios.");
    return;
  }
  configDeseada.params = p;
  guardarParametros(configDeseada);
  enviarConfigAlRadio();
  Serial.printf("[CONFIG] Parametros nuevos: periodo %u s, ventana %u s, timeout %u ms, "
                "publicar %s. Se aplican en la proxima ronda.\n",
                p.periodo_s, p.estabilizacion_s, p.timeout_ms, p.publicar ? "si" : "no");
}

void alRecibirMqtt(char* topic, uint8_t* carga, unsigned int largo) {
  if (largo == 0) return;   // mensaje retenido borrado: no hay nada que aplicar

  const bool esNodos = strcmp(topic, TOPIC_CONFIG_NODOS) == 0;
  const bool esParametros = strcmp(topic, TOPIC_CONFIG_PARAMETROS) == 0;
  if (!esNodos && !esParametros) return;

  StaticJsonDocument<768> doc;
  if (deserializeJson(doc, carga, largo)) {
    if (esNodos) rechazoNodos = "JSON invalido";
    else rechazoParametros = "JSON invalido";
  } else if (esNodos) {
    procesarNodos(doc);
  } else {
    procesarParametros(doc);
  }

  if (esNodos && rechazoNodos) Serial.printf("[-] Lista de nodos rechazada: %s\n", rechazoNodos);
  if (esParametros && rechazoParametros) Serial.printf("[-] Parametros rechazados: %s\n", rechazoParametros);
  publicarAplicado();   // para que el rechazo se vea enseguida
}

bool horaValida() { return time(nullptr) >= EPOCH_MINIMO; }

void publicarMedicion(const Medicion& m) {
  int32_t antiguedad_ms = (int32_t)(millis() - m.marca_ms);
  if (antiguedad_ms < 0) antiguedad_ms = 0;
  const time_t marca = time(nullptr) - antiguedad_ms / 1000;

  char topic[48];
  snprintf(topic, sizeof(topic), "estaciones/estacion_%02u/data", (unsigned)m.id_nodo);

  char hora[25];
  struct tm t;
  gmtime_r(&marca, &t);
  strftime(hora, sizeof(hora), "%Y-%m-%dT%H:%M:%SZ", &t);

  StaticJsonDocument<256> doc;
  doc["timestamp"] = hora;
  if (m.estado & ESTADO_PM_OK) {
    doc["pm25"] = redondear(m.pm25);
    doc["pm10"] = redondear(m.pm10);
  }
  if (m.estado & ESTADO_TH_OK) {
    doc["temperatura"] = redondear(m.temperatura);
    doc["humedad"] = redondear(m.humedad);
  }
  char json[256];
  size_t n = serializeJson(doc, json, sizeof(json));

  // retained = false, explicito. La version anterior llamaba
  // publish(topic, json, n): como json es char* y no uint8_t*, el
  // compilador elegia publish(topic, payload, retained) con retained = n,
  // y cada medicion quedaba retenida en el broker.
  if (mqtt.publish(topic, (const uint8_t*)json, n, false)) {
    Serial.printf("[CLOUD] %s %s\n", topic, json);
  } else {
    Serial.printf("[-] No se pudo publicar en %s\n", topic);
  }
}

void mantenerConexion() {
  if (WiFi.status() != WL_CONNECTED) {
    redOk = false;
    Serial.println("\n[WIFI] Desconectado. Reconectando...");
    WiFi.begin(WIFI_SSID, WIFI_CLAVE);
    uint32_t desde = millis();
    while (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(500 / portTICK_PERIOD_MS);
      if (millis() - desde > 15000) {
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_CLAVE);
        desde = millis();
      }
    }
    Serial.println("[WIFI] Conectado.");
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  }

  if (!mqtt.connected()) {
    redOk = false;
    Serial.println("[MQTT] Conectando al broker...");
    if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USUARIO, MQTT_CLAVE,
                     TOPIC_ESTADO, 1, true, "{\"gateway_status\":\"OFFLINE\"}")) {
      mqtt.publish(TOPIC_ESTADO, "{\"gateway_status\":\"ONLINE\"}", true);
      mqtt.subscribe(TOPIC_CONFIG_NODOS, 1);
      mqtt.subscribe(TOPIC_CONFIG_PARAMETROS, 1);
      publicarAplicado();
      Serial.println("[MQTT] Conectado y suscripto a la configuracion.");
    } else {
      Serial.printf("[-] Fallo MQTT, rc=%d. Reintento en 5 s.\n", mqtt.state());
      vTaskDelay(5000 / portTICK_PERIOD_MS);
      return;
    }
  }
  redOk = true;
}

// Una vuelta de la tarea de red. Separada de TareaRed para poder probarla
// en la computadora (firmware/pruebas/prueba_gateway.cpp).
void cicloRed() {
  mantenerConexion();
  mqtt.loop();

  // Solo se saca de la cola lo que se puede publicar: sin conexion o sin
  // hora, las mediciones esperan en la cola en vez de perderse.
  Medicion m;
  while (mqtt.connected() && horaValida() && xQueueReceive(colaMediciones, &m, 0) == pdPASS) {
    publicarMedicion(m);
  }

  Resumen r;
  if (xQueueReceive(buzonResumen, &r, 0) == pdPASS) {
    ultimoResumen = r;
    hayResumen = true;
    publicarAplicado();
  }
}

void TareaRed(void*) {
  for (;;) {
    cicloRed();
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// =====================================================================
// RADIO (core 1)
// =====================================================================
void enviar(const Comando& c) {
  while (Serial2.available()) Serial2.read();   // restos de la ronda anterior
  ensamblador.reiniciar();
  Serial2.write((const uint8_t*)&c, sizeof(c));
}

void difundirMedir() {
  const ParametrosRed& p = planificador.parametros();
  Comando c = {MAGIA_GATEWAY, CMD_MEDIR, DIRECCION_TODOS, planificador.ronda(),
               p.estabilizacion_s, p.periodo_s};
  enviar(c);

  if (rondaMarcada != planificador.ronda()) {
    // Todas las mediciones de la ronda llevan la misma hora: la del fin de
    // la ventana, que es el momento que representan.
    rondaMarcada = planificador.ronda();
    marcaRondaMs = millis() + (uint32_t)p.estabilizacion_s * 1000UL;
    Serial.printf("\n[RONDA %u] MEDIR a %u nodos, ventana de %u s\n",
                  planificador.ronda(), planificador.cantidadNodos(), p.estabilizacion_s);
  }
}

void pedirDato() {
  Comando c = {MAGIA_GATEWAY, CMD_ENVIAR, planificador.nodoActual(), planificador.ronda(), 0, 0};
  enviar(c);
  Serial.printf("[LORA TX] ENVIAR al nodo %02u\n", (unsigned)c.destino);
}

void encolar(const Medicion& m) {
  if (xQueueSend(colaMediciones, &m, 0) == pdPASS) return;
  // Cola llena: se descarta la mas vieja para hacer lugar a la nueva.
  Medicion vieja;
  xQueueReceive(colaMediciones, &vieja, 0);
  descartadas++;
  xQueueSend(colaMediciones, &m, 0);
}

void atenderRespuesta(const Respuesta& r) {
  Recepcion rec = planificador.recibir(r, millis());
  if (rec == IGNORADA) return;

  if (rec == ACEPTADA_VIEJA) {
    Serial.printf("[LORA RX] Nodo %02u respondio con la ronda %u: no escucho la orden "
                  "de medir. Se descarta.\n", (unsigned)r.id_nodo, r.ronda);
    return;
  }
  Serial.printf("[LORA RX] Nodo %02u | PM2.5 %.1f | PM10 %.1f (%u lecturas) | %.1f C | %.1f %%\n",
                (unsigned)r.id_nodo, r.pm25, r.pm10, r.muestras_pm, r.temperatura, r.humedad);

  if (!(r.estado & (ESTADO_PM_OK | ESTADO_TH_OK))) {
    Serial.printf("[-] Nodo %02u: ningun sensor entrego datos.\n", (unsigned)r.id_nodo);
    return;
  }
  if (!planificador.parametros().publicar) {
    Serial.println("    (publicacion desactivada: solo monitor serie)");
    return;
  }
  Medicion m = {r.id_nodo, r.estado, marcaRondaMs, r.pm25, r.pm10, r.temperatura, r.humedad};
  encolar(m);
}

void cerrarRonda() {
  Resumen res;
  res.config.params = planificador.parametros();
  res.config.n = planificador.cantidadNodos();
  uint8_t frescas = 0, viejas = 0, sinRespuesta = 0;
  for (uint8_t i = 0; i < res.config.n; i++) {
    res.config.nodos[i] = planificador.nodo(i);
    res.desenlaces[i] = planificador.desenlace(i);
    res.sensores[i] = planificador.sensores(i);
    if (res.desenlaces[i] == FRESCA) frescas++;
    else if (res.desenlaces[i] == VIEJA) viejas++;
    else if (res.desenlaces[i] == SIN_RESPUESTA) sinRespuesta++;
  }
  res.ronda = planificador.ronda();
  res.duracion_ms = planificador.duracionUltimaRonda();
  res.excedida = planificador.ultimaExcedida();
  res.descartadas = descartadas;
  xQueueOverwrite(buzonResumen, &res);

  rondaSinDatos = res.config.n > 0 && frescas == 0;
  Serial.printf("[RONDA %u] Cerrada en %.1f s: %u frescas, %u viejas, %u sin respuesta%s\n",
                res.ronda, res.duracion_ms / 1000.0, frescas, viejas, sinRespuesta,
                res.excedida ? " — EXCEDIO EL PERIODO" : "");
}

void loop() {
  ConfigRed nueva;
  if (xQueueReceive(buzonConfig, &nueva, 0) == pdPASS) {
    planificador.configurar(nueva.params, nueva.nodos, nueva.n);
    Serial.println("[RADIO] Configuracion nueva: se aplica al empezar la proxima ronda.");
  }

  while (Serial2.available()) {
    Respuesta r;
    if (ensamblador.empujar((uint8_t)Serial2.read(), millis(), &r) && respuestaValida(r)) {
      atenderRespuesta(r);
    }
  }

  switch (planificador.tick(millis())) {
    case DIFUNDIR_MEDIR:  difundirMedir(); break;
    case PEDIR_DATO:      pedirDato();     break;
    case RONDA_TERMINADA: cerrarRonda();   break;
    default: break;
  }
  delay(1);
}

// =====================================================================
// LED (core 1)
// =====================================================================
void parpadear(uint8_t veces, uint16_t encendido_ms, uint16_t pausa_final_ms) {
  for (uint8_t i = 0; i < veces; i++) {
    digitalWrite(LED_PIN, HIGH); vTaskDelay(encendido_ms / portTICK_PERIOD_MS);
    digitalWrite(LED_PIN, LOW);  vTaskDelay(encendido_ms / portTICK_PERIOD_MS);
  }
  vTaskDelay(pausa_final_ms / portTICK_PERIOD_MS);
}

// parpadeo rapido: sin Wi-Fi o sin broker | 2 parpadeos: la ultima ronda
// no trajo ningun dato | apagado: todo bien
void TareaLED(void*) {
  pinMode(LED_PIN, OUTPUT);
  for (;;) {
    if (!redOk) parpadear(1, 200, 0);
    else if (rondaSinDatos) parpadear(2, 150, 400);
    else { digitalWrite(LED_PIN, LOW); vTaskDelay(100 / portTICK_PERIOD_MS); }
  }
}

// =====================================================================
// SETUP
// =====================================================================
void setup() {
  btStop();
  Serial.begin(115200);

  pinMode(PIN_M0, OUTPUT);
  pinMode(PIN_M1, OUTPUT);
  digitalWrite(PIN_M0, LOW);   // E22 en modo transparente
  digitalWrite(PIN_M1, LOW);
  Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2);

  cargarConfig(configDeseada);
  planificador.configurar(configDeseada.params, configDeseada.nodos, configDeseada.n);
  planificador.arrancar(millis(), (uint16_t)(esp_random() & 0xFFFF), 3000);

  colaMediciones = xQueueCreate(LARGO_COLA_MEDICIONES, sizeof(Medicion));
  buzonConfig    = xQueueCreate(1, sizeof(ConfigRed));
  buzonResumen   = xQueueCreate(1, sizeof(Resumen));

  WiFi.mode(WIFI_STA);
  // TLS: el canal va cifrado, pero setInsecure() no verifica el
  // certificado del servidor, asi que no protege contra suplantacion. La
  // correccion es cargar la raiz de Let's Encrypt con setCACert(); queda
  // pendiente hasta confirmar en la VM que cadena entrega nginx.
  clienteTLS.setInsecure();
  mqtt.setServer(MQTT_HOST, MQTT_PUERTO);
  mqtt.setBufferSize(1024);   // el reporte de estado no entra en los 256 de fabrica
  mqtt.setCallback(alRecibirMqtt);

  Serial.println("\n=================================================");
  Serial.println(" GATEWAY " FIRMWARE_VERSION " — protocolo LoRa v2");
  Serial.println("=================================================");
  Serial.printf(" Periodo %u s | ventana %u s | timeout %u ms | publicar %s\n",
                configDeseada.params.periodo_s, configDeseada.params.estabilizacion_s,
                configDeseada.params.timeout_ms, configDeseada.params.publicar ? "si" : "no");
  Serial.print(" Nodos:");
  for (uint8_t i = 0; i < configDeseada.n; i++) Serial.printf(" %u", configDeseada.nodos[i]);
  Serial.println(configDeseada.n ? "" : " (ninguno)");

  xTaskCreatePinnedToCore(TareaRed, "red", 12288, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(TareaLED, "led", 2048, NULL, 1, NULL, 1);
}
