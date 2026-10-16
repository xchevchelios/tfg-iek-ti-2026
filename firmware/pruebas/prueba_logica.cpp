// Pruebas de la logica pura del protocolo v2, sin hardware.
//
// Simula un gateway y varios nodos que se hablan por un canal compartido,
// byte a byte, con el tiempo simulado en pasos de 1 ms. Usa exactamente los
// mismos headers que compila el firmware: protocolo.h, ensamblador.h,
// planificador.h (gateway) y medicion.h (nodo).
#include <cstdio>
#include <cmath>
#include <vector>
#include <map>
#include <set>
#include <functional>
#include "../gateway/protocolo.h"
#include "../gateway/ensamblador.h"
#include "../gateway/planificador.h"
#include "../nodo/medicion.h"

static int fallas = 0, chequeos = 0;
#define CHECK(cond, ...) do { chequeos++; if (!(cond)) { fallas++; \
  printf("  FALLA (%s:%d): ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

// --------------------------------------------------------------------
// Red simulada
// --------------------------------------------------------------------
const uint32_t LATENCIA_MS = 150;   // aire + UART del E22, del orden medido

struct Byte { uint32_t llega; uint8_t valor; };

struct NodoSimulado {
  uint8_t id;
  bool encendido = true;
  std::function<bool(uint16_t)> pierdeMedir = [](uint16_t) { return false; };
  std::function<float(uint32_t)> pm = [](uint32_t) { return 12.0f; };  // segun ms dentro de la ventana
  VentanaMedicion ventana;
  ResultadoMedicion resultado = {RONDA_NINGUNA, 0, 0, 0, 0, 0, 0};
  Ensamblador<sizeof(Comando)> ens{MAGIA_GATEWAY};
  std::vector<Byte> entrada;
  uint32_t msSdsDespierto = 0, ultimaLecturaSds = 0, ultimoDht = 0;
};

struct Red {
  uint32_t ahora = 0;
  Planificador gw;
  Ensamblador<sizeof(Respuesta)> ensGw{MAGIA_NODO};
  std::vector<Byte> entradaGw;
  std::vector<NodoSimulado> nodos;

  // Registro para las verificaciones
  std::vector<uint32_t> iniciosRonda;
  std::map<uint16_t, int> medirPorRonda;
  struct Publicada { uint16_t ronda; uint8_t nodo; float pm25; };
  std::vector<Publicada> publicadas;
  std::vector<uint8_t> viejas;
  struct Cierre { uint16_t ronda; uint32_t duracion; bool excedida; uint8_t n;
                  std::vector<uint8_t> desenlaces; };
  std::vector<Cierre> cierres;

  void transmitirDesdeGateway(const Comando& c) {
    const uint8_t* b = (const uint8_t*)&c;
    for (auto& n : nodos) {
      if (!n.encendido) continue;
      if (c.tipo == CMD_MEDIR && n.pierdeMedir(c.ronda)) continue;
      for (unsigned i = 0; i < sizeof(c); i++) n.entrada.push_back({ahora + LATENCIA_MS + i, b[i]});
    }
  }

  void transmitirDesdeNodo(const NodoSimulado& origen, const Respuesta& r) {
    const uint8_t* b = (const uint8_t*)&r;
    for (unsigned i = 0; i < sizeof(r); i++) entradaGw.push_back({ahora + LATENCIA_MS + i, b[i]});
    // Canal compartido: los demas nodos tambien la escuchan y deben ignorarla.
    for (auto& n : nodos)
      if (&n != &origen && n.encendido)
        for (unsigned i = 0; i < sizeof(r); i++) n.entrada.push_back({ahora + LATENCIA_MS + i, b[i]});
  }

  template <typename F>
  static void entregar(std::vector<Byte>& cola, uint32_t ahora, F alCompletar) {
    size_t k = 0;
    while (k < cola.size() && (int32_t)(ahora - cola[k].llega) >= 0) { alCompletar(cola[k]); k++; }
    cola.erase(cola.begin(), cola.begin() + k);
  }

  void pasoNodo(NodoSimulado& n) {
    entregar(n.entrada, ahora, [&](const Byte& b) {
      Comando c;
      if (!n.ens.empujar(b.valor, b.llega, &c) || !comandoValido(c)) return;
      if (c.tipo == CMD_MEDIR) {
        if (n.ventana.iniciar(c.ronda, c.estabilizacion_s, ahora)) { n.ultimaLecturaSds = ahora; n.ultimoDht = ahora; }
      } else if (c.tipo == CMD_ENVIAR && c.destino == n.id) {
        Respuesta r = {MAGIA_NODO, n.id, n.resultado.ronda, n.resultado.estado, n.resultado.muestras_pm,
                       n.resultado.pm25, n.resultado.pm10, n.resultado.temperatura, n.resultado.humedad};
        transmitirDesdeNodo(n, r);
      }
    });
    if (n.ventana.activa()) {
      n.msSdsDespierto++;
      if (ahora - n.ultimaLecturaSds >= 1000) {          // el SDS011 entrega 1 lectura por segundo
        n.ultimaLecturaSds = ahora;
        float v = n.pm(n.ventana.transcurrido(ahora));
        n.ventana.agregarPM(v, v * 1.5f, ahora);
      }
      if (n.ventana.enTramoDePromedio(ahora) && ahora - n.ultimoDht >= 2500) {
        n.ultimoDht = ahora;
        n.ventana.agregarTH(25.0f, 60.0f, ahora);
      }
      if (n.ventana.vencida(ahora)) n.resultado = n.ventana.cerrar();
    }
  }

  void pasoGateway() {
    entregar(entradaGw, ahora, [&](const Byte& b) {
      Respuesta r;
      if (!ensGw.empujar(b.valor, b.llega, &r) || !respuestaValida(r)) return;
      Recepcion rec = gw.recibir(r, ahora);
      if (rec == ACEPTADA_FRESCA) publicadas.push_back({r.ronda, r.id_nodo, r.pm25});
      if (rec == ACEPTADA_VIEJA) viejas.push_back(r.id_nodo);
    });
    switch (gw.tick(ahora)) {
      case DIFUNDIR_MEDIR: {
        const ParametrosRed& p = gw.parametros();
        if (medirPorRonda[gw.ronda()]++ == 0) iniciosRonda.push_back(ahora);
        transmitirDesdeGateway({MAGIA_GATEWAY, CMD_MEDIR, DIRECCION_TODOS, gw.ronda(), p.estabilizacion_s, p.periodo_s});
        break;
      }
      case PEDIR_DATO:
        transmitirDesdeGateway({MAGIA_GATEWAY, CMD_ENVIAR, gw.nodoActual(), gw.ronda(), 0, 0});
        break;
      case RONDA_TERMINADA: {
        Cierre c{gw.ronda(), gw.duracionUltimaRonda(), gw.ultimaExcedida(), gw.cantidadNodos(), {}};
        for (uint8_t i = 0; i < gw.cantidadNodos(); i++) c.desenlaces.push_back(gw.desenlace(i));
        cierres.push_back(c);
        break;
      }
      default: break;
    }
  }

  void correr(uint32_t ms) {
    for (uint32_t i = 0; i < ms; i++) {
      for (auto& n : nodos) pasoNodo(n);
      pasoGateway();
      ahora++;
    }
  }

  void configurar(ParametrosRed p, std::vector<uint8_t> lista) { gw.configurar(p, lista.data(), lista.size()); }
  NodoSimulado& nodo(uint8_t id) { for (auto& n : nodos) if (n.id == id) return n; throw 0; }
};

Red redCon(std::vector<uint8_t> ids, ParametrosRed p = PARAMETROS_POR_DEFECTO, uint32_t inicio = 0) {
  Red red;
  red.ahora = inicio;
  for (uint8_t id : ids) { NodoSimulado n; n.id = id; red.nodos.push_back(n); }
  red.configurar(p, ids);
  red.gw.arrancar(red.ahora, 100, 3000);
  return red;
}

int contar(const std::vector<Red::Publicada>& v, uint16_t ronda) {
  int n = 0; for (auto& p : v) if (p.ronda == ronda) n++; return n;
}

// --------------------------------------------------------------------
int main() {
  printf("1. Rondas normales: 2 nodos, periodo 300 s, ventana 30 s\n");
  {
    Red red = redCon({1, 2});
    // Las lecturas de los primeros 15 s simulan el sensor sin estabilizar.
    for (auto& n : red.nodos) n.pm = [](uint32_t t) { return t < 15000 ? 80.0f : 10.0f; };
    red.correr(3 * 300000 + 1000);

    CHECK(red.iniciosRonda.size() == 3, "se esperaban 3 rondas, hubo %zu", red.iniciosRonda.size());
    if (red.iniciosRonda.size() == 3) {
      CHECK(red.iniciosRonda[0] == 3000, "primera ronda a los 3 s, fue a los %u ms", red.iniciosRonda[0]);
      CHECK(red.iniciosRonda[1] - red.iniciosRonda[0] == 300000 && red.iniciosRonda[2] - red.iniciosRonda[1] == 300000,
            "las rondas deben arrancar cada 300 s exactos");
    }
    bool dosMedir = true;
    for (auto& kv : red.medirPorRonda) dosMedir &= kv.second == 2;
    CHECK(dosMedir, "MEDIR se difunde dos veces por ronda");
    CHECK(red.publicadas.size() == 6, "6 mediciones frescas (2 nodos x 3 rondas), hubo %zu", red.publicadas.size());
    bool soloEstables = true;
    for (auto& p : red.publicadas) soloEstables &= std::fabs(p.pm25 - 10.0f) < 0.01f;
    CHECK(soloEstables, "el promedio solo debe incluir el tramo final (10), no el arranque (80)");
    CHECK(red.viejas.empty(), "no deberia haber datos viejos");
    for (auto& c : red.cierres) {
      CHECK(!c.excedida, "ninguna ronda deberia exceder el periodo");
      CHECK(c.duracion > 32000 && c.duracion < 34000, "ronda de ~32-33 s, duro %u ms", c.duracion);
    }
    double ciclo = 100.0 * red.nodo(1).msSdsDespierto / red.ahora;
    printf("   SDS011 despierto el %.1f %% del tiempo; ronda de %.1f s\n", ciclo, red.cierres[0].duracion / 1000.0);
    CHECK(ciclo > 9.0 && ciclo < 11.0, "el SDS011 deberia trabajar ~10 %% del tiempo, trabajo %.1f %%", ciclo);
  }

  printf("2. Un nodo no escucha ninguna de las dos difusiones de MEDIR\n");
  {
    Red red = redCon({1, 2, 3});
    red.nodo(3).pierdeMedir = [](uint16_t ronda) { return ronda == 102; };
    red.correr(3 * 300000 + 1000);
    CHECK(contar(red.publicadas, 101) == 3 && contar(red.publicadas, 103) == 3, "rondas 101 y 103 completas");
    CHECK(contar(red.publicadas, 102) == 2, "en la ronda 102 solo publican los nodos 1 y 2");
    CHECK(red.viejas.size() == 1 && red.viejas[0] == 3, "el nodo 3 responde con su dato viejo y se descarta");
    CHECK(red.cierres.size() == 3 && red.cierres[1].desenlaces[2] == VIEJA, "el reporte marca al nodo 3 como VIEJA");
  }

  printf("3. Un nodo apagado\n");
  {
    Red red = redCon({1, 2});
    red.nodo(2).encendido = false;
    red.correr(300000 + 1000);
    CHECK(red.publicadas.size() == 1 && red.publicadas[0].nodo == 1, "solo publica el nodo 1");
    CHECK(red.cierres.size() == 1 && red.cierres[0].desenlaces[1] == SIN_RESPUESTA, "nodo 2 SIN_RESPUESTA");
    CHECK(red.cierres[0].duracion > 35000, "la ronda se alarga por el timeout de 3 s (%u ms)", red.cierres[0].duracion);
  }

  printf("4. Configuracion nueva a mitad de una ronda\n");
  {
    Red red = redCon({1, 2});
    red.correr(3000 + 10000);   // 10 s dentro de la primera ronda
    ParametrosRed p = PARAMETROS_POR_DEFECTO;
    p.periodo_s = 120;
    red.configurar(p, {1, 2, 3});
    NodoSimulado n3; n3.id = 3; red.nodos.push_back(n3);
    red.correr(300000 + 120000 + 1000);
    CHECK(red.cierres.size() >= 2 && red.cierres[0].n == 2, "la ronda en curso termina con 2 nodos");
    CHECK(red.cierres.size() >= 2 && red.cierres[1].n == 3, "la siguiente ya interroga a 3");
    CHECK(red.iniciosRonda.size() >= 3 && red.iniciosRonda[1] - red.iniciosRonda[0] == 300000,
          "la ronda en curso se programo con el periodo viejo");
    CHECK(red.iniciosRonda.size() >= 3 && red.iniciosRonda[2] - red.iniciosRonda[1] == 120000,
          "desde la ronda nueva rige el periodo de 120 s");
  }

  printf("5. Ronda que no entra en el periodo: 32 nodos sin respuesta, periodo 60 s\n");
  {
    std::vector<uint8_t> ids;
    for (uint8_t i = 1; i <= 32; i++) ids.push_back(i);
    ParametrosRed p = PARAMETROS_POR_DEFECTO;
    p.periodo_s = 60;
    Red red = redCon(ids, p);
    for (auto& n : red.nodos) n.encendido = false;
    uint32_t peor = duracionMaximaRonda_ms(p, 32);
    red.correr(2 * peor + 1000);
    CHECK(!red.cierres.empty() && red.cierres[0].excedida, "la ronda debe marcarse como excedida");
    CHECK(red.iniciosRonda.size() >= 2 &&
          red.iniciosRonda[1] - red.iniciosRonda[0] <= red.cierres[0].duracion + 5,
          "la siguiente arranca apenas termina la anterior");
    printf("   duracion: %.1f s (peor caso calculado: %.1f s)\n", red.cierres[0].duracion / 1000.0, peor / 1000.0);
  }

  printf("6. Desborde de millis() a los 49 dias\n");
  {
    Red red = redCon({1, 2}, PARAMETROS_POR_DEFECTO, 0xFFFFFFFFu - 100000);
    red.correr(3 * 300000 + 1000);
    CHECK(red.iniciosRonda.size() == 3, "3 rondas a pesar del desborde (%zu)", red.iniciosRonda.size());
    if (red.iniciosRonda.size() == 3)
      CHECK((uint32_t)(red.iniciosRonda[2] - red.iniciosRonda[1]) == 300000, "la cadencia se mantiene");
    CHECK(red.publicadas.size() == 6, "todas las mediciones llegan (%zu)", red.publicadas.size());
  }

  printf("7. Ensamblador: basura, respuestas ajenas y tramas cortadas\n");
  {
    Ensamblador<sizeof(Comando)> e(MAGIA_GATEWAY);
    Comando valido = {MAGIA_GATEWAY, CMD_ENVIAR, 7, 42, 0, 0}, salida;
    Respuesta ajena = {MAGIA_NODO, 3, 42, 3, 10, 1, 2, 3, 4};
    uint32_t t = 0;
    int completas = 0;
    for (uint8_t b : {0x00, 0x13, 0xFF}) completas += e.empujar(b, t++, &salida);
    for (unsigned i = 0; i < sizeof(ajena); i++) completas += e.empujar(((uint8_t*)&ajena)[i], t++, &salida);
    CHECK(completas == 0, "ni la basura ni la respuesta de otro nodo forman un comando");
    for (unsigned i = 0; i < 4; i++) e.empujar(((uint8_t*)&valido)[i], t++, &salida);   // mitad de una trama...
    t += 50;                                                                       // ...y silencio
    for (unsigned i = 0; i < sizeof(valido); i++) completas += e.empujar(((uint8_t*)&valido)[i], t++, &salida);
    CHECK(completas == 1 && memcmp(&salida, &valido, sizeof valido) == 0, "la trama cortada se descarta y la siguiente llega entera");
    CHECK(!comandoValido(Comando{MAGIA_GATEWAY, CMD_ENVIAR, 255, 1, 0, 0}), "ENVIAR a 255 no es valido");
    CHECK(!comandoValido(Comando{MAGIA_GATEWAY, CMD_MEDIR, 4, 1, 30, 300}), "MEDIR tiene que ser difusion");
    CHECK(siguienteRonda(65535) == 1, "la ronda salta el 0 al dar la vuelta");
  }

  printf("8. Validacion de configuracion\n");
  {
    uint8_t ok[] = {1, 2, 254}, cero[] = {0}, difusion[] = {255}, rep[] = {3, 5, 3};
    uint8_t muchos[33];
    for (int i = 0; i < 33; i++) muchos[i] = i + 1;
    CHECK(!validarNodos(ok, 3), "1, 2, 254 validos");
    CHECK(!validarNodos(ok, 0), "lista vacia valida");
    CHECK(validarNodos(cero, 1), "0 invalido");
    CHECK(validarNodos(difusion, 1), "255 reservado para difusion");
    CHECK(validarNodos(rep, 3), "repetidos invalidos");
    CHECK(validarNodos(muchos, 33), "mas de 32 invalido");
    ParametrosRed p = PARAMETROS_POR_DEFECTO;
    CHECK(!validarParametros(p), "los valores por defecto son validos");
    p.periodo_s = 59;  CHECK(validarParametros(p), "periodo 59 invalido");
    p.periodo_s = 60;  CHECK(!validarParametros(p), "periodo 60 valido");
    p.estabilizacion_s = 14; CHECK(validarParametros(p), "ventana 14 invalida");
    p.estabilizacion_s = 30; p.timeout_ms = 400; CHECK(validarParametros(p), "timeout 400 invalido");
  }

  printf("9. Ventana del nodo\n");
  {
    VentanaMedicion v;
    CHECK(v.iniciar(5, 30, 1000), "inicia");
    CHECK(!v.iniciar(5, 30, 1400), "la repeticion de MEDIR no reinicia la ventana");
    v.agregarTH(NAN, NAN, 25000);
    ResultadoMedicion r = (v.agregarPM(9, 9, 2000), v.cerrar());   // lectura fuera del tramo final
    CHECK(r.ronda == 5 && r.estado == 0 && r.muestras_pm == 0, "sin lecturas validas, estado 0");
    v.iniciar(6, 15, 0);
    v.agregarPM(10, 15, 7000);   // antes de los 7,5 s: no entra
    v.agregarPM(10, 15, 8000);  v.agregarPM(20, 30, 14000);
    v.agregarTH(24, 50, 8000);
    r = v.cerrar();
    CHECK(r.estado == (ESTADO_PM_OK | ESTADO_TH_OK) && r.muestras_pm == 2 && std::fabs(r.pm25 - 15) < 1e-4,  // (10 + 20) / 2
          "ventana de 15 s promedia la mitad final");
  }

  printf("\n%d chequeos, %d fallas\n", chequeos, fallas);
  return fallas ? 1 : 0;
}
