"""Pruebas de la calibracion contra equipo de referencia.

Correr desde api/:   python3 -m unittest discover -s pruebas -v

Usa una base temporal y datos sinteticos con coeficientes conocidos: el ajuste
tiene que recuperarlos.
"""
import math
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TMP.name) / "calibracion.db")
os.environ["GOOGLE_CLIENT_ID"] = "prueba"
os.environ["GOOGLE_CLIENT_SECRET"] = "prueba"
os.environ["COOKIE_SECURE"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calibracion  # noqa: E402
import db  # noqa: E402
import seguridad  # noqa: E402

T0 = datetime(2026, 11, 12, 0, 0, 0, tzinfo=timezone.utc)
LOCAL = timezone(timedelta(hours=-3))
ESTACION = "estacion_01"

# Modelo "verdadero" del sensor: sds = 2 + 1.6·ref + 0.12·(HR − 50) + ruido
A_SDS, B_SDS, C_SDS = 2.0, 1.6, 0.12


def _verdad(t):
    horas = (t - T0).total_seconds() / 3600
    pm = 25 + 18 * math.sin(horas / 24 * 2 * math.pi) + 8 * math.sin(horas / 5.3)
    hr = 62 + 22 * math.sin(horas / 24 * 2 * math.pi + 2.0)
    return max(2.0, pm), hr


def _sembrar(conn, dias=6, periodo_s=300):
    rnd = random.Random(7)
    ahora = T0.isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO estaciones (id, nombre, activa, direccion_lora, creada_en) "
        "VALUES (?, 'E1', 1, 1, ?)",
        (ESTACION, ahora),
    )
    t = T0
    while t < T0 + timedelta(days=dias):
        pm, hr = _verdad(t)
        sds = A_SDS + B_SDS * pm + C_SDS * (hr - 50) + rnd.gauss(0, 1.0)
        conn.execute(
            "INSERT INTO mediciones (timestamp, station_id, pm25, pm10, temperatura, humedad) "
            "VALUES (?, ?, ?, ?, 25, ?)",
            (calibracion.texto_utc(t), ESTACION, round(sds, 1), round(sds * 1.3, 1), round(hr, 1)),
        )
        t += timedelta(seconds=periodo_s)


def _csv_referencia(dias=6, periodo_s=60):
    """Planilla 'a la espanola': punto y coma, coma decimal y hora local sin zona."""
    lineas = ["Fecha;PM2.5;PM10"]
    t = T0
    while t < T0 + timedelta(days=dias):
        pm, _ = _verdad(t)
        local = t.astimezone(LOCAL).strftime("%d/%m/%Y %H:%M:%S")
        lineas.append(f"{local};{pm:.2f}".replace(".", ",") + f";{pm * 1.25:.2f}".replace(".", ","))
        t += timedelta(seconds=periodo_s)
    return "\n".join(lineas)


class TestNucleo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        with db.get_connection() as conn:
            _sembrar(conn)
            conn.commit()
        cls.csv = _csv_referencia()

    def test_parseo_de_fechas(self):
        self.assertEqual(
            calibracion.texto_utc(calibracion.parsear_ts("2026-11-12T00:27:00Z")),
            "2026-11-12T00:27:00Z",
        )
        self.assertEqual(
            calibracion.texto_utc(calibracion.parsear_ts("12/11/2026 09:00", -3)),
            "2026-11-12T12:00:00Z",
        )

    def test_recupera_el_modelo_con_humedad(self):
        with db.get_connection() as conn:
            r = calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", self.csv,
                                               hr_max=None, offset_horas=-3)
        hum = r["modelos"]["humedad"]
        # Invertir sds = A + B·ref + C·(HR−50)  ->  ref = (sds − A + 50C)/B − (C/B)·HR
        self.assertAlmostEqual(hum["coef"]["b"], 1 / B_SDS, delta=0.02)
        self.assertAlmostEqual(hum["coef"]["c"], -C_SDS / B_SDS, delta=0.02)
        self.assertAlmostEqual(hum["coef"]["a"], (-A_SDS + 50 * C_SDS) / B_SDS, delta=1.0)
        val = hum["validacion"]
        self.assertEqual(val["tipo"], "temporal")
        self.assertLess(val["calibrado"]["rmse"], 1.5)
        self.assertGreater(val["crudo"]["rmse"], 10)
        # El modelo lineal mejora el RMSE pero no el r2 (transformacion afin).
        lin = r["modelos"]["lineal"]["validacion"]
        self.assertLess(lin["calibrado"]["rmse"], lin["crudo"]["rmse"])
        self.assertAlmostEqual(lin["calibrado"]["r2"], lin["crudo"]["r2"], places=3)
        self.assertGreater(val["calibrado"]["r2"], lin["calibrado"]["r2"])

    def test_filtro_de_humedad_y_ventanas(self):
        with db.get_connection() as conn:
            con = calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", self.csv,
                                                 hr_max=80, offset_horas=-3)
            sin = calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", self.csv,
                                                 hr_max=None, offset_horas=-3)
        self.assertGreater(con["resumen"]["descartadas_humedad"], 0)
        self.assertEqual(
            con["resumen"]["pares"] + con["resumen"]["descartadas_humedad"],
            sin["resumen"]["pares"],
        )
        self.assertEqual(sin["resumen"]["pares"], 6 * 24)

    def test_zona_horaria_equivocada_no_alinea_bien(self):
        """Leer la hora local como UTC corre todo 3 h: el ajuste empeora."""
        with db.get_connection() as conn:
            bien = calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", self.csv,
                                                  hr_max=None, offset_horas=-3)
            mal = calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", self.csv,
                                                 hr_max=None, offset_horas=0)
        self.assertGreater(
            mal["modelos"]["humedad"]["validacion"]["calibrado"]["rmse"],
            3 * bien["modelos"]["humedad"]["validacion"]["calibrado"]["rmse"],
        )

    def test_errores_legibles(self):
        with db.get_connection() as conn:
            with self.assertRaises(calibracion.ErrorCalibracion):
                calibracion.calibrar_desde_csv(conn, ESTACION, "pm25", "x;y\n1;2", offset_horas=-3)
            with self.assertRaises(calibracion.ErrorCalibracion):
                # Referencia de otra semana: sin ventanas en comun.
                calibracion.calibrar_desde_csv(
                    conn, ESTACION, "pm25", "timestamp,pm25\n2025-01-01T00:00:00Z,10",
                    offset_horas=-3)

    def test_versionado_por_fecha(self):
        cal = calibracion.Calibrador([
            {"id": 1, "station_id": "e", "metrica": "pm25", "a": 0, "b": 0.5, "c": 0,
             "valida_desde": calibracion.DESDE_SIEMPRE},
            {"id": 2, "station_id": "e", "metrica": "pm25", "a": 0, "b": 0.25, "c": 0,
             "valida_desde": "2026-11-15T00:00:00Z"},
        ])
        self.assertEqual(cal.valor("e", "pm25", "2026-11-14T23:59:59Z", 40, 50), (20.0, 1))
        self.assertEqual(cal.valor("e", "pm25", "2026-11-15T00:00:00Z", 40, 50), (10.0, 2))
        self.assertEqual(cal.valor("e", "pm10", "2026-11-15T00:00:00Z", 40, 50), (40, None))
        # Nunca negativo, y sin HR el modelo con humedad no se aplica.
        neg = calibracion.Calibrador([{"id": 3, "station_id": "e", "metrica": "pm25", "a": -5,
                                       "b": 1, "c": 0.1, "valida_desde": calibracion.DESDE_SIEMPRE}])
        self.assertEqual(neg.valor("e", "pm25", "2026-11-15T00:00:00Z", 1, 0), (0.0, 3))
        self.assertEqual(neg.valor("e", "pm25", "2026-11-15T00:00:00Z", 1, None), (1, None))


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        with db.get_connection() as conn:
            if not conn.execute("SELECT 1 FROM mediciones").fetchone():
                _sembrar(conn)
            conn.execute(
                "INSERT OR IGNORE INTO usuarios (id, email, nombre, rol, creado_en) "
                "VALUES (1, 'admin@x.com', 'Admin', 'admin', ?)", (T0.isoformat(),))
            conn.commit()
        cls.csv = _csv_referencia()

        usuario = {"id": 1, "email": "admin@x.com", "nombre": "Admin", "rol": "admin"}
        seguridad.leer_sesion = lambda _cookie: {"usuario": usuario, "csrf": "tok"}
        from app import create_app
        cls.cliente = create_app().test_client()
        cls.h = {"X-CSRF-Token": "tok"}

    def _cuerpo(self, **extra):
        return {"estacion": ESTACION, "metrica": "pm25", "csv": self.csv, "hr_max": None,
                **extra}

    def test_flujo_completo(self):
        r = self.cliente.post("/api/calibraciones/ajustar", json=self._cuerpo(), headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("humedad", r.get_json()["modelos"])

        # Sin CSRF no se guarda.
        r = self.cliente.post("/api/calibraciones", json=self._cuerpo(modelo="humedad"))
        self.assertEqual(r.status_code, 403)

        r = self.cliente.post("/api/calibraciones", json=self._cuerpo(modelo="humedad"),
                              headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        cal_id = r.get_json()["id"]

        ultimas = self.cliente.get("/api/mediciones/ultimas?limite=5").get_json()[ESTACION]
        fila = ultimas[-1]
        self.assertEqual(fila["calibracion"]["pm25"], cal_id)
        self.assertIsNone(fila["calibracion"]["pm10"])
        self.assertLess(fila["pm25"], fila["pm25_crudo"])
        pm_real, _ = _verdad(calibracion.parsear_ts(fila["timestamp"]))
        self.assertAlmostEqual(fila["pm25"], pm_real, delta=3)

        crudo = self.cliente.get("/api/mediciones/ultimas?limite=5&crudo=1").get_json()[ESTACION]
        self.assertEqual(crudo[-1]["pm25"], fila["pm25_crudo"])

        hist = self.cliente.get(
            f"/api/historial?estacion={ESTACION}&metrica=pm25&start=2026-11-12&end=2026-11-13"
        ).get_json()
        self.assertTrue(all("crudo" in p for p in hist))
        hist_hr = self.cliente.get(
            f"/api/historial?estacion={ESTACION}&metrica=humedad&start=2026-11-12&end=2026-11-13"
        ).get_json()
        self.assertNotIn("crudo", hist_hr[0])

        # Anular vuelve al crudo.
        r = self.cliente.delete(f"/api/calibraciones/{cal_id}", headers=self.h)
        self.assertEqual(r.status_code, 200)
        fila2 = self.cliente.get("/api/mediciones/ultimas?limite=1").get_json()[ESTACION][-1]
        self.assertEqual(fila2["pm25"], fila2["pm25_crudo"])
        self.assertEqual(self.cliente.get("/api/calibraciones").get_json(), [])

    def test_validaciones(self):
        r = self.cliente.post("/api/calibraciones/ajustar",
                              json=self._cuerpo(estacion="estacion_99"), headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.cliente.post("/api/calibraciones/ajustar",
                              json=self._cuerpo(metrica="humedad"), headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.cliente.post("/api/calibraciones", json=self._cuerpo(modelo="cubico"),
                              headers=self.h)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
