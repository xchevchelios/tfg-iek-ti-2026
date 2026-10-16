/*
 * =====================================================================
 * NODO DE MEDICION — protocolo LoRa v2 (dos fases)
 * =====================================================================
 * El SDS011 pasa dormido casi todo el tiempo. Se despierta solo cuando el
 * gateway difunde la orden MEDIR, trabaja durante la ventana de
 * estabilizacion, se promedia el tramo final y se vuelve a dormir. Con una
 * ronda cada 5 minutos y 30 s de ventana, el laser trabaja el 10 % del
 * tiempo: unos 9 anios teoricos de vida util en vez de menos de uno.
 *
 *   Core 0: sensores (SDS011, DHT21) y ventana de medicion.
 *   Core 1: radio LoRa (E22 por UART) y LED de diagnostico.
 * Los nucleos se comunican por una cola (la orden de medir) y por el
 * resultado, protegido con una seccion critica.
 *
 * Pines: E22 (RX 16, TX 17, M0 19, M1 18) | SDS011 (TX->33, RX->32)
 *        DHT21 (4) | LED (2)
 * Librerias: "SDS011 sensor Library" (ricki-z), "DHT sensor library"
 *            (Adafruit) y su dependencia "Adafruit Unified Sensor".
 *
 * LED:  fijo           -> midiendo (SDS011 despierto)
 *       parpadeo       -> no llegan ordenes del gateway
 *       2 parpadeos    -> el DHT21 no respondio en la ultima medicion
 *       3 parpadeos    -> el SDS011 no respondio en la ultima medicion
 *       apagado        -> en reposo, todo bien
 * =====================================================================
 */
#include <WiFi.h>
#include "DHT.h"
#include <SDS011.h>
#include "protocolo.h"
#include "ensamblador.h"
#include "medicion.h"

// ======================= CONFIGURACION DEL NODO =======================
// Direccion LoRa, de 1 a 254. Es la misma que se carga en el panel web.
const uint8_t ID_NODO = 2;
// ======================================================================

#define RXD2    16
#define TXD2    17
#define PIN_M0  19
#define PIN_M1  18
#define RX_SDS  33   // TX del SDS011
#define TX_SDS  32   // RX del SDS011
#define DHTPIN  4
#define DHTTYPE DHT21
#define LED_PIN 2

// Si a los 5 s de despertarlo el SDS011 no entrego ninguna lectura, se le
// reenvia la orden una vez: a veces la primera se pierde.
const uint32_t REINTENTO_DESPERTAR_MS = 5000;
const uint32_t INTERVALO_DHT_MS = 2500;      // el DHT21 pide 2 s entre lecturas
const uint32_t PERIODO_INICIAL_S = 300;      // hasta escuchar la primera orden

DHT dht(DHTPIN, DHTTYPE);
SDS011 sds;

struct OrdenMedir {
  uint16_t ronda;
  uint8_t  estabilizacion_s;
};

QueueHandle_t buzonOrden;   // core 1 -> core 0, de un solo lugar
portMUX_TYPE muxResultado = portMUX_INITIALIZER_UNLOCKED;
ResultadoMedicion resultado = {RONDA_NINGUNA, 0, 0, 0, 0, 0, 0};

volatile bool     midiendo    = false;
volatile bool     errorSDS    = false;
volatile bool     errorDHT    = false;
volatile uint32_t ultimaOrden = 0;
volatile uint32_t periodoMs   = PERIODO_INICIAL_S * 1000UL;

Ensamblador<sizeof(Comando)> ensamblador(MAGIA_GATEWAY);

// ========================= SENSORES (core 0) ==========================
void TareaSensores(void*) {
  VentanaMedicion ventana;
  uint32_t ultimoDHT = 0;
  bool hubLecturaSDS = false;
  bool reintentado = false;

  for (;;) {
    uint32_t ahora = millis();

    OrdenMedir orden;
    if (xQueueReceive(buzonOrden, &orden, 0) == pdPASS &&
        ventana.iniciar(orden.ronda, orden.estabilizacion_s, ahora)) {
      while (Serial1.available()) Serial1.read();
      sds.wakeup();
      midiendo = true;
      hubLecturaSDS = false;
      reintentado = false;
      ultimoDHT = ahora;
      Serial.printf("[MEDICION] Ronda %u: SDS011 despierto, ventana de %u s\n",
                    orden.ronda, orden.estabilizacion_s);
    }

    if (ventana.activa()) {
      float p25, p10;
      if (sds.read(&p25, &p10) == 0) {
        hubLecturaSDS = true;
        ventana.agregarPM(p25, p10, ahora);
      } else if (!hubLecturaSDS && !reintentado &&
                 ventana.transcurrido(ahora) > REINTENTO_DESPERTAR_MS) {
        sds.wakeup();
        reintentado = true;
        Serial.println("[MEDICION] El SDS011 no responde: reenvio la orden de despertar.");
      }

      if (ventana.enTramoDePromedio(ahora) && ahora - ultimoDHT >= INTERVALO_DHT_MS) {
        // readHumidity reutiliza la lectura que acaba de hacer readTemperature.
        ventana.agregarTH(dht.readTemperature(), dht.readHumidity(), ahora);
        ultimoDHT = ahora;
      }

      if (ventana.vencida(ahora)) {
        ResultadoMedicion r = ventana.cerrar();
        sds.sleep();
        portENTER_CRITICAL(&muxResultado);
        resultado = r;
        portEXIT_CRITICAL(&muxResultado);
        midiendo = false;
        errorSDS = !(r.estado & ESTADO_PM_OK);
        errorDHT = !(r.estado & ESTADO_TH_OK);

        Serial.printf("[MEDICION] Ronda %u cerrada: PM2.5 %.1f | PM10 %.1f (%u lecturas) | "
                      "%.1f C | %.1f %% | SDS011 dormido\n",
                      r.ronda, r.pm25, r.pm10, r.muestras_pm, r.temperatura, r.humedad);
        if (errorSDS) Serial.println("[-] El SDS011 no entrego lecturas en esta ventana.");
        if (errorDHT) Serial.println("[-] El DHT21 no respondio: revisar pull-up o cableado.");
      }
    }

    vTaskDelay(100 / portTICK_PERIOD_MS);
  }
}

// =========================== RADIO (core 1) ===========================
void responder() {
  portENTER_CRITICAL(&muxResultado);
  ResultadoMedicion r = resultado;
  portEXIT_CRITICAL(&muxResultado);

  Respuesta resp;
  resp.magia       = MAGIA_NODO;
  resp.id_nodo     = ID_NODO;
  resp.ronda       = r.ronda;
  resp.estado      = r.estado;
  resp.muestras_pm = r.muestras_pm;
  resp.pm25        = r.pm25;
  resp.pm10        = r.pm10;
  resp.temperatura = r.temperatura;
  resp.humedad     = r.humedad;
  Serial2.write((const uint8_t*)&resp, sizeof(resp));

  Serial.printf("[LORA TX] Respuesta con la medicion de la ronda %u\n", r.ronda);
}

void atenderComando(const Comando& c) {
  if (!comandoValido(c)) return;

  if (c.tipo == CMD_MEDIR) {
    ultimaOrden = millis();
    if (c.periodo_s > 0) periodoMs = (uint32_t)c.periodo_s * 1000UL;
    OrdenMedir orden = {c.ronda, c.estabilizacion_s};
    xQueueOverwrite(buzonOrden, &orden);
    Serial.printf("\n[LORA RX] Orden MEDIR, ronda %u\n", c.ronda);
  } else if (c.tipo == CMD_ENVIAR && c.destino == ID_NODO) {
    Serial.printf("[LORA RX] Orden ENVIAR, ronda %u\n", c.ronda);
    responder();
  }
}

void loop() {
  while (Serial2.available()) {
    Comando c;
    if (ensamblador.empujar((uint8_t)Serial2.read(), millis(), &c)) atenderComando(c);
  }
  delay(2);
}

// ============================ LED (core 1) ============================
void parpadear(uint8_t veces, uint16_t encendido_ms, uint16_t pausa_final_ms) {
  for (uint8_t i = 0; i < veces; i++) {
    digitalWrite(LED_PIN, HIGH); vTaskDelay(encendido_ms / portTICK_PERIOD_MS);
    digitalWrite(LED_PIN, LOW);  vTaskDelay(encendido_ms / portTICK_PERIOD_MS);
  }
  vTaskDelay(pausa_final_ms / portTICK_PERIOD_MS);
}

void TareaLED(void*) {
  pinMode(LED_PIN, OUTPUT);
  for (;;) {
    // Sin ordenes durante dos periodos y medio: el nodo quedo fuera de la red.
    bool sinOrdenes = millis() - ultimaOrden > 2 * periodoMs + 30000UL;
    if (midiendo) {
      digitalWrite(LED_PIN, HIGH);
      vTaskDelay(100 / portTICK_PERIOD_MS);
    } else if (sinOrdenes) {
      parpadear(1, 200, 0);
    } else if (errorDHT) {
      parpadear(2, 150, 400);
    } else if (errorSDS) {
      parpadear(3, 150, 400);
    } else {
      digitalWrite(LED_PIN, LOW);
      vTaskDelay(100 / portTICK_PERIOD_MS);
    }
  }
}

// =============================== SETUP ================================
void setup() {
  WiFi.mode(WIFI_OFF);
  btStop();
  Serial.begin(115200);

  pinMode(PIN_M0, OUTPUT);
  pinMode(PIN_M1, OUTPUT);
  digitalWrite(PIN_M0, LOW);   // E22 en modo transparente
  digitalWrite(PIN_M1, LOW);
  Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2);

  // Con los pines explicitos: la variante de un solo argumento reinicia el
  // puerto sin pines y, segun la version del core, puede ir a los de fabrica.
  sds.begin(&Serial1, RX_SDS, TX_SDS);
  delay(1000);   // el SDS011 recien encendido no atiende comandos de inmediato
  sds.sleep();   // arranca dormido: solo trabaja cuando el gateway lo pide
  dht.begin();

  buzonOrden = xQueueCreate(1, sizeof(OrdenMedir));
  ultimaOrden = millis();

  Serial.println("\n=================================================");
  Serial.printf(" NODO %02u — protocolo LoRa v2 (SDS011 dormido)\n", ID_NODO);
  Serial.println("=================================================");

  xTaskCreatePinnedToCore(TareaSensores, "sensores", 8192, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(TareaLED, "led", 2048, NULL, 1, NULL, 1);
}
