"""Pruebas del registro de rondas del gateway.

Correr desde api/:   python3 -m unittest discover -s pruebas -v

Usa una base temporal: no toca sensores.db.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_TMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TMP.name) / "prueba.db")
os.environ["MQTT_TOPIC_RONDAS"] = "gateway/estado/aplicado"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import estadisticas_rondas  # noqa: E402
from rondas import ResumenInvalido, guardar_resumen, rondas_perdidas  # noqa: E402

T0 = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def resumen(ronda, encendido_s, frescas=(1, 2), viejas=(), sin_respuesta=(), fallando=(),
            nodos=(1, 2)):
    """Mismo JSON que arma publicarAplicado() en el firmware."""
    return {
        "firmware": "2.2.0", "ronda": ronda, "nodos": list(nodos),
        "periodo_s": 60, "estabilizacion_s": 30, "timeout_ms": 3000, "publicar": True,
        "ultima_ronda": {
            "duracion_ms": 9400, "excedida": False,
            "frescas": list(frescas), "viejas": list(viejas),
            "sin_respuesta": list(sin_respuesta), "sensor_fallando": list(fallando),
        },
        "rechazos": {}, "descartadas": 0, "encendido_s": encendido_s,
    }


class BaseLimpia(unittest.TestCase):
    def setUp(self):
        Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
        db.init_db()
        self.conn = db.get_connection()

    def tearDown(self):
        self.conn.close()

    def guardar(self, carga, ahora):
        rid = guardar_resumen(self.conn, carga, ahora)
        self.conn.commit()
        return rid

    def contar(self, tabla):
        return self.conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


class GuardarResumen(BaseLimpia):
    def test_guarda_desenlace_de_cada_nodo(self):
        self.guardar(resumen(100, 600, frescas=[1], sin_respuesta=[2]), T0)
        filas = dict(self.conn.execute("SELECT direccion, desenlace FROM rondas_nodo"))
        self.assertEqual(filas, {1: "fresca", 2: "sin_respuesta"})

    def test_vieja_y_sensor_fallando(self):
        self.guardar(resumen(100, 600, frescas=[1], viejas=[2], fallando=[1]), T0)
        filas = {r["direccion"]: (r["desenlace"], r["sensor_ok"])
                 for r in self.conn.execute("SELECT * FROM rondas_nodo")}
        self.assertEqual(filas, {1: ("fresca", 0), 2: ("vieja", None)})

    def test_republicacion_no_duplica(self):
        # El gateway republica el mismo resumen al reconectarse o al recibir config.
        self.assertIsNotNone(self.guardar(resumen(100, 600), T0))
        self.assertIsNone(self.guardar(resumen(100, 640), T0 + timedelta(seconds=40)))
        self.assertEqual(self.contar("rondas_gateway"), 1)
        self.assertEqual(self.contar("rondas_nodo"), 2)

    def test_mismo_arranque_con_deriva_de_reloj(self):
        for i in range(10):
            # 2 s de deriva por ronda: acumulada supera la tolerancia, pero de a poco
            self.guardar(resumen(100 + i, 600 + i * 60 - i * 2), T0 + timedelta(seconds=i * 60))
        self.assertEqual(self.contar("arranques_gateway"), 1)

    def test_reinicio_es_otro_arranque(self):
        self.guardar(resumen(100, 600), T0)
        # Reinicio: el contador arranca en otro valor al azar
        self.guardar(resumen(40000, 60), T0 + timedelta(minutes=5))
        self.assertEqual(self.contar("arranques_gateway"), 2)

    def test_misma_ronda_en_otro_arranque_no_es_duplicado(self):
        self.guardar(resumen(100, 600), T0)
        self.assertIsNotNone(self.guardar(resumen(100, 60), T0 + timedelta(minutes=5)))
        self.assertEqual(self.contar("rondas_gateway"), 2)

    def test_nodo_configurado_sin_lista(self):
        self.guardar(resumen(100, 600, frescas=[1], nodos=[1, 2]), T0)
        fila = self.conn.execute("SELECT desenlace FROM rondas_nodo WHERE direccion = 2").fetchone()
        self.assertEqual(fila[0], "sin_desenlace")

    def test_resumen_invalido(self):
        for carga in ({}, {"ronda": 1}, resumen(0, 10), resumen(70000, 10),
                      resumen(5, -1), resumen(True, 10), [], "x"):
            with self.assertRaises(ResumenInvalido):
                guardar_resumen(self.conn, carga, T0)
        self.assertEqual(self.contar("rondas_gateway"), 0)


class Contador(unittest.TestCase):
    def test_rondas_perdidas(self):
        self.assertEqual(rondas_perdidas(10, 11), 0)
        self.assertEqual(rondas_perdidas(10, 14), 3)
        self.assertEqual(rondas_perdidas(65535, 1), 0)   # da la vuelta salteando el 0
        self.assertEqual(rondas_perdidas(65534, 2), 2)   # faltan 65535 y 1


class Ingesta(BaseLimpia):
    def setUp(self):
        super().setUp()
        import ingesta
        self.ingesta = ingesta

    def mensaje(self, topic, carga, retain=False):
        payload = carga if isinstance(carga, bytes) else json.dumps(carga).encode()
        return SimpleNamespace(topic=topic, payload=payload, retain=retain)

    def test_resumen_en_vivo_se_guarda(self):
        self.ingesta.on_message(None, None, self.mensaje("gateway/estado/aplicado", resumen(7, 60)))
        self.assertEqual(self.contar("rondas_gateway"), 1)
        self.assertEqual(self.contar("mediciones"), 0)

    def test_resumen_retenido_se_ignora(self):
        self.ingesta.on_message(None, None,
                                self.mensaje("gateway/estado/aplicado", resumen(7, 60), retain=True))
        self.assertEqual(self.contar("rondas_gateway"), 0)

    def test_resumen_roto_no_rompe_la_ingesta(self):
        with self.assertLogs("ingesta", level="WARNING"):
            self.ingesta.on_message(None, None, self.mensaje("gateway/estado/aplicado", b"{no json"))
        with self.assertLogs("ingesta", level="WARNING"):
            self.ingesta.on_message(None, None, self.mensaje("gateway/estado/aplicado", {"ronda": 3}))
        # Y una medicion despues se sigue guardando
        self.ingesta.on_message(None, None, self.mensaje(
            "estaciones/estacion_01/data",
            {"timestamp": "2026-11-12T12:00:00Z", "pm25": 10.0, "pm10": 20.0}))
        self.assertEqual(self.contar("mediciones"), 1)

    def test_mediciones_siguen_igual(self):
        self.ingesta.on_message(None, None, self.mensaje(
            "estaciones/estacion_02/data",
            {"timestamp": "2026-11-12T12:00:00Z", "pm25": 1.0, "pm10": 2.0,
             "temperatura": 25.0, "humedad": 60.0}))
        fila = self.conn.execute("SELECT station_id, pm25, humedad FROM mediciones").fetchone()
        self.assertEqual(tuple(fila), ("estacion_02", 1.0, 60.0))
        self.assertEqual(self.contar("rondas_gateway"), 0)

    def test_registro_apagado_con_topic_vacio(self):
        anterior = self.ingesta.config.MQTT_TOPIC_RONDAS
        self.ingesta.config.MQTT_TOPIC_RONDAS = ""
        try:
            self.ingesta.on_message(None, None,
                                    self.mensaje("gateway/estado/aplicado", resumen(7, 60)))
        finally:
            self.ingesta.config.MQTT_TOPIC_RONDAS = anterior
        self.assertEqual(self.contar("rondas_gateway"), 0)


class Estadisticas(BaseLimpia):
    def test_resumen_y_rachas(self):
        # Nodo 2 falla en las rondas 101 y 102; falta el reporte de la 104.
        self.guardar(resumen(100, 600), T0)
        self.guardar(resumen(101, 660, frescas=[1], sin_respuesta=[2]), T0 + timedelta(minutes=1))
        self.guardar(resumen(102, 720, frescas=[1], viejas=[2]), T0 + timedelta(minutes=2))
        self.guardar(resumen(103, 780), T0 + timedelta(minutes=3))
        self.guardar(resumen(105, 900), T0 + timedelta(minutes=5))
        rondas, nodos = estadisticas_rondas.cargar(self.conn, None, None)

        g = estadisticas_rondas.resumen_gateway(rondas)
        self.assertEqual((g["registradas"], g["sin_reporte"], g["arranques"]), (5, 1, 1))

        n = estadisticas_rondas.resumen_nodos(nodos)
        self.assertEqual((n[1]["fresca"], n[1]["racha_max"]), (5, 0))
        self.assertEqual((n[2]["fresca"], n[2]["sin_respuesta"], n[2]["vieja"], n[2]["racha_max"]),
                         (3, 1, 1, 2))

    def test_script_corre_sobre_la_base(self):
        self.guardar(resumen(100, 600, frescas=[1], sin_respuesta=[2]), T0)
        salida = Path(_TMP.name) / "rondas.csv"
        with _Silencio():
            codigo = estadisticas_rondas.main(["--por-hora", "--csv", str(salida)])
        self.assertEqual(codigo, 0)
        self.assertEqual(len(salida.read_text().splitlines()), 3)


class _Silencio:
    def __enter__(self):
        import io
        self._antes, sys.stdout = sys.stdout, io.StringIO()

    def __exit__(self, *exc):
        sys.stdout = self._antes
        return False


if __name__ == "__main__":
    unittest.main()
