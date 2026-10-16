// Prueba del gateway.ino completo, compilado en la computadora contra el
// entorno simulado: configuracion por MQTT, NVS, rondas por la UART con
// nodos falsos, publicacion de mediciones y reporte de estado.
#include "Arduino.h"
#include "../gateway/gateway.ino"

static int fallas = 0, chequeos = 0;
#define CHECK(cond, ...) do { chequeos++; if (!(cond)) { fallas++; \
  printf("  FALLA (%s:%d): ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

void entregarMqtt(const char* topic, const char* json) {
  std::string t = topic, c = json;
  mqtt.callback(&t[0], (uint8_t*)&c[0], c.size());
}

int publicacionesEn(const std::string& topic) {
  int n = 0;
  for (auto& p : mqtt.publicadas) if (p.topic == topic) n++;
  return n;
}

const Publicacion* ultimaEn(const std::string& topic) {
  for (auto it = mqtt.publicadas.rbegin(); it != mqtt.publicadas.rend(); ++it)
    if (it->topic == topic) return &*it;
  return nullptr;
}

bool contiene(const Publicacion* p, const char* texto) { return p && p->carga.find(texto) != std::string::npos; }

// Nodos falsos: leen los comandos que el gateway escribio en Serial2 y
// responden por la misma UART. `estados[id]` decide que sensores andan.
std::map<uint8_t, uint8_t> estados = {{1, ESTADO_PM_OK | ESTADO_TH_OK}, {2, ESTADO_PM_OK | ESTADO_TH_OK}};
size_t leidos = 0;
void nodosFalsos() {
  while (Serial2.tx.size() - leidos >= sizeof(Comando)) {
    Comando c;
    memcpy(&c, &Serial2.tx[leidos], sizeof c);
    leidos += sizeof c;
    if (c.tipo != CMD_ENVIAR || !estados.count(c.destino)) continue;
    Respuesta r = {MAGIA_NODO, c.destino, c.ronda, estados[c.destino], 10, 12.34f, 20.0f, 25.06f, 61.0f};
    const uint8_t* b = (const uint8_t*)&r;
    Serial2.rx.insert(Serial2.rx.end(), b, b + sizeof r);
  }
}

void correr(uint32_t ms, bool red = true) {
  uint32_t fin = g_millis + ms;
  while ((int32_t)(g_millis - fin) < 0) {
    loop();          // avanza 1 ms (delay(1))
    nodosFalsos();
    if (red && g_millis % 10 == 0) cicloRed();
  }
}

int main() {
  printf("1. Primer arranque: sin NVS, lista inicial {1, 2} y parametros por defecto\n");
  setup();
  CHECK(configDeseada.n == 2 && configDeseada.nodos[0] == 1 && configDeseada.nodos[1] == 2, "lista inicial");
  CHECK(parametrosIguales(configDeseada.params, PARAMETROS_POR_DEFECTO), "parametros por defecto");
  cicloRed();   // conecta
  CHECK(mqtt.conectado, "conecta al broker");
  CHECK(mqtt.suscripciones.size() == 2, "se suscribe a los dos topics de configuracion");
  CHECK(contiene(ultimaEn(TOPIC_ESTADO), "ONLINE") && ultimaEn(TOPIC_ESTADO)->retenido, "publica ONLINE retenido");

  printf("2. Primera ronda con los dos nodos\n");
  correr(40000);
  const Publicacion* m1 = ultimaEn("estaciones/estacion_01/data");
  CHECK(m1 && !m1->retenido, "la medicion se publica SIN retener");
  CHECK(contiene(m1, "\"pm25\":12.3") && contiene(m1, "\"temperatura\":25.1"), "valores redondeados a un decimal: %s", m1 ? m1->carga.c_str() : "-");
  CHECK(contiene(m1, "\"timestamp\":\"20"), "lleva timestamp UTC");
  CHECK(publicacionesEn("estaciones/estacion_02/data") == 1, "el nodo 2 tambien publica");
  const Publicacion* rep = ultimaEn(TOPIC_APLICADO);
  CHECK(rep && rep->retenido, "el reporte de estado se publica retenido");
  CHECK(contiene(rep, "\"frescas\":[1,2]") && contiene(rep, "\"periodo_s\":300"), "reporte: %s", rep ? rep->carga.c_str() : "-");
  if (rep) printf("   reporte: %s\n", rep->carga.c_str());

  printf("3. Lista nueva desde la API\n");
  unsigned escriturasAntes = Preferences::escrituras;
  entregarMqtt(TOPIC_CONFIG_NODOS, "{\"actualizado\":\"2026-10-16T12:00:00Z\",\"nodos\":[1,2,3]}");
  CHECK(configDeseada.n == 3 && !rechazoNodos, "acepta [1,2,3]");
  CHECK(Preferences::escrituras > escriturasAntes, "la guarda en NVS");
  escriturasAntes = Preferences::escrituras;
  entregarMqtt(TOPIC_CONFIG_NODOS, "{\"actualizado\":\"2026-10-16T12:00:00Z\",\"nodos\":[1,2,3]}");
  CHECK(Preferences::escrituras == escriturasAntes, "el reenvio del retenido no reescribe la flash");

  printf("4. Listas invalidas: se rechazan enteras\n");
  for (const char* malo : {"{\"nodos\":[1,255]}", "{\"nodos\":[1,1]}", "{\"nodos\":\"1,2\"}", "{\"nodos\":[1,\"x\"]}", "no es json"}) {
    entregarMqtt(TOPIC_CONFIG_NODOS, malo);
    CHECK(rechazoNodos && configDeseada.n == 3, "rechaza %s", malo);
  }
  CHECK(contiene(ultimaEn(TOPIC_APLICADO), "\"rechazos\":{\"nodos\""), "el rechazo aparece enseguida en el reporte");
  entregarMqtt(TOPIC_CONFIG_NODOS, "{\"nodos\":[1,2,3]}");
  CHECK(!rechazoNodos, "un mensaje valido limpia el rechazo");

  printf("5. Parametros desde el gestor\n");
  entregarMqtt(TOPIC_CONFIG_PARAMETROS, "{\"periodo_s\":600}");
  CHECK(configDeseada.params.periodo_s == 600 && configDeseada.params.estabilizacion_s == 30,
        "cambio parcial: solo el periodo");
  for (const char* malo : {"{\"periodo_s\":10}", "{\"periodo_s\":-5}", "{\"periodo_s\":70000}",
                           "{\"timeout_ms\":100}", "{\"publicar\":\"si\"}", "{\"estabilizacion_s\":2.5}"}) {
    entregarMqtt(TOPIC_CONFIG_PARAMETROS, malo);
    CHECK(rechazoParametros && configDeseada.params.periodo_s == 600, "rechaza %s", malo);
  }
  entregarMqtt(TOPIC_CONFIG_PARAMETROS, "{\"periodo_s\":600}");
  CHECK(!rechazoParametros, "el valido limpia el rechazo");

  printf("6. Reinicio: arranca con lo que quedo en NVS\n");
  ConfigRed recargada;
  cargarConfig(recargada);
  CHECK(recargada.n == 3 && recargada.nodos[2] == 3 && recargada.params.periodo_s == 600, "NVS conserva lista y parametros");

  printf("7. La configuracion nueva rige desde la ronda siguiente\n");
  estados[3] = ESTADO_PM_OK;   // nodo 3 con el DHT21 roto
  correr(300000);              // termina el periodo viejo de 300 s y corre la ronda nueva
  rep = ultimaEn(TOPIC_APLICADO);
  CHECK(contiene(rep, "\"nodos\":[1,2,3]") && contiene(rep, "\"periodo_s\":600"), "reporte con la config nueva: %s", rep ? rep->carga.c_str() : "-");
  CHECK(contiene(rep, "\"sensor_fallando\":[3]"), "el nodo 3 aparece con un sensor fallando");
  const Publicacion* m3 = ultimaEn("estaciones/estacion_03/data");
  CHECK(contiene(m3, "pm25") && !contiene(m3, "temperatura"), "sin DHT21, publica PM y omite temperatura: %s", m3 ? m3->carga.c_str() : "-");

  printf("8. Lista vacia (todas las estaciones de baja)\n");
  entregarMqtt(TOPIC_CONFIG_NODOS, "{\"nodos\":[]}");
  cargarConfig(recargada);
  CHECK(configDeseada.n == 0 && recargada.n == 0, "se acepta y sobrevive al reinicio");
  entregarMqtt(TOPIC_CONFIG_NODOS, "{\"nodos\":[1,2]}");

  printf("9. Sin red durante 34 rondas de 60 s: la cola guarda 64 y descarta las mas viejas\n");
  entregarMqtt(TOPIC_CONFIG_PARAMETROS, "{\"periodo_s\":60}");
  correr(700000);              // aplica lista y periodo nuevos
  estados.erase(3);
  size_t antes = mqtt.publicadas.size();
  correr(34 * 60000, false);   // la tarea de red no corre: nada se publica
  CHECK(mqtt.publicadas.size() == antes, "sin red no se publica nada");
  cicloRed();                  // vuelve la red
  int publicadasAlVolver = 0;
  for (size_t i = antes; i < mqtt.publicadas.size(); i++)
    if (mqtt.publicadas[i].topic.rfind("estaciones/estacion_", 0) == 0) publicadasAlVolver++;
  CHECK(publicadasAlVolver == 64, "al volver publica las 64 guardadas (%d)", publicadasAlVolver);
  // Cada una sale con la hora en que se midio, no con la de publicacion:
  // entre la primera y la ultima guardadas tiene que haber ~31 rondas de 60 s.
  std::vector<time_t> horas;
  for (size_t i = antes; i < mqtt.publicadas.size(); i++) {
    const std::string& c = mqtt.publicadas[i].carga;
    size_t k = c.find("\"timestamp\":\"");
    if (mqtt.publicadas[i].topic.rfind("estaciones/", 0) != 0 || k == std::string::npos) continue;
    struct tm tm = {};
    strptime(c.substr(k + 13, 20).c_str(), "%Y-%m-%dT%H:%M:%SZ", &tm);
    horas.push_back(timegm(&tm));
  }
  long separacion = horas.empty() ? 0 : (long)(horas.back() - horas.front());
  CHECK(separacion >= 31 * 60 - 5 && separacion <= 31 * 60 + 5,
        "las guardadas conservan su hora: %ld s entre la primera y la ultima (esperado ~1860)", separacion);
  CHECK(contiene(ultimaEn(TOPIC_APLICADO), "\"descartadas\":4"), "reporta 4 descartadas: %s", ultimaEn(TOPIC_APLICADO)->carga.c_str());

  printf("10. publicar = false: sigue interrogando, no publica\n");
  entregarMqtt(TOPIC_CONFIG_PARAMETROS, "{\"publicar\":false}");
  correr(120000);
  antes = mqtt.publicadas.size();
  correr(60000);
  int mediciones = 0;
  for (size_t i = antes; i < mqtt.publicadas.size(); i++)
    if (mqtt.publicadas[i].topic.rfind("estaciones/estacion_", 0) == 0) mediciones++;
  CHECK(mediciones == 0, "no publica mediciones");
  CHECK(Serial.salida.find("publicacion desactivada") != std::string::npos, "pero las muestra por el monitor serie");

  printf("\n%d chequeos, %d fallas\n", chequeos, fallas);
  return fallas ? 1 : 0;
}
