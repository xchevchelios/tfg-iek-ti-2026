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
# DHT21: temp_nodo = 0.8 + 1.03·T_ref ; hr_nodo = 4 + 0.92·HR_ref
A_T, B_T = 0.8, 1.03
A_HR, B_HR = 4.0, 0.92


def _temp_ref(t):
    horas = (t - T0).total_seconds() / 3600
    return 27 + 6 * math.sin(horas / 24 * 2 * math.pi - 1.0)


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
            "VALUES (?, ?, ?, ?, ?, ?)",
            (calibracion.texto_utc(t), ESTACION, round(sds, 1), round(sds * 1.3, 1),
             round(A_T + B_T * _temp_ref(t) + rnd.gauss(0, 0.1), 2), round(hr, 1)),
        )
        t += timedelta(seconds=periodo_s)


def _csv_referencia(dias=6, periodo_s=60):
    """Planilla 'a la espanola': punto y coma, coma decimal, hora local sin zona
    y unidades en la cabecera."""
    lineas = ["Fecha;PM2.5 (µg/m³);PM10;Temperatura (°C);HR [%]"]
    t = T0
    while t < T0 + timedelta(days=dias):
        pm, hr_nodo = _verdad(t)
        hr_ref = (hr_nodo - A_HR) / B_HR
        local = t.astimezone(LOCAL).strftime("%d/%m/%Y %H:%M:%S")
        numeros = ";".join(f"{v:.2f}" for v in (pm, pm * 1.25, _temp_ref(t), hr_ref))
        lineas.append(f"{local};" + numeros.replace(".", ","))
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

    def test_temperatura_y_humedad(self):
        with db.get_connection() as conn:
            temp = calibracion.calibrar_desde_csv(conn, ESTACION, "temperatura", self.csv,
                                                  offset_horas=-3)
            hum = calibracion.calibrar_desde_csv(conn, ESTACION, "humedad", self.csv,
                                                 offset_horas=-3)
        for r in (temp, hum):
            # Solo el modelo lineal, sin filtro por humedad (aunque se pida 80 %).
            self.assertEqual(list(r["modelos"]), ["lineal"])
            self.assertIsNone(r["hr_max"])
            self.assertEqual(r["resumen"]["descartadas_humedad"], 0)
            self.assertEqual(r["resumen"]["pares"], 6 * 24)
        ct = temp["modelos"]["lineal"]["coef"]
        self.assertAlmostEqual(ct["b"], 1 / B_T, delta=0.01)
        self.assertAlmostEqual(ct["a"], -A_T / B_T, delta=0.3)
        ch = hum["modelos"]["lineal"]["coef"]
        self.assertAlmostEqual(ch["b"], 1 / B_HR, delta=0.01)
        self.assertAlmostEqual(ch["a"], -A_HR / B_HR, delta=0.5)
        self.assertLess(hum["modelos"]["lineal"]["validacion"]["calibrado"]["rmse"], 0.5)

    def test_limites_fisicos(self):
        cal = calibracion.Calibrador([
            {"id": 1, "station_id": "e", "metrica": "humedad", "a": 5, "b": 1.1, "c": 0,
             "valida_desde": calibracion.DESDE_SIEMPRE},
            {"id": 2, "station_id": "e", "metrica": "temperatura", "a": -2, "b": 1, "c": 0,
             "valida_desde": calibracion.DESDE_SIEMPRE},
        ])
        self.assertEqual(cal.valor("e", "humedad", "2026-11-15T00:00:00Z", 98, None), (100.0, 1))
        self.assertEqual(cal.valor("e", "temperatura", "2026-11-15T00:00:00Z", 1, None), (-1.0, 2))

    def test_pm_usa_la_humedad_cruda(self):
        cal = calibracion.Calibrador([
            {"id": 1, "station_id": "e", "metrica": "humedad", "a": 0, "b": 0.5, "c": 0,
             "valida_desde": calibracion.DESDE_SIEMPRE},
            {"id": 2, "station_id": "e", "metrica": "pm25", "a": 0, "b": 1, "c": 0.1,
             "valida_desde": calibracion.DESDE_SIEMPRE},
        ])
        fila = cal.calibrar_fila("e", {"timestamp": "2026-11-15T00:00:00Z", "pm25": 10,
                                       "humedad": 80, "temperatura": 25})
        self.assertEqual(fila["humedad"], 40.0)
        self.assertEqual(fila["pm25"], 18.0)  # 10 + 0.1·80, no 10 + 0.1·40
        self.assertEqual(fila["temperatura"], 25)
        self.assertIsNone(fila["calibracion"]["temperatura"])

    def test_migra_la_tabla_vieja(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE calibraciones (id INTEGER PRIMARY KEY, metrica TEXT "
                     "CHECK (metrica IN ('pm25', 'pm10')))")
        conn.execute("INSERT INTO calibraciones VALUES (7, 'pm25')")
        # Con las columnas reales de la primera version:
        conn.execute("DROP TABLE calibraciones")
        calibracion._crear_tabla_actual(conn)
        sql_v1 = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'calibraciones'"
                              ).fetchone()[0]
        conn.execute("DROP TABLE calibraciones")
        conn.execute(sql_v1.replace("('pm25', 'pm10', 'temperatura', 'humedad')",
                                    "('pm25', 'pm10')")
                     .replace("('lineal', 'humedad', 'humedad_exp')", "('lineal', 'humedad')"))
        conn.execute("INSERT INTO calibraciones (station_id, metrica, modelo, a, b, valida_desde, "
                     "ventana_min, n_pares, validacion, n_validacion, pares_json, creada_en) "
                     "VALUES ('e', 'pm25', 'lineal', 1, 2, 'x', 60, 3, 'en_muestra', 3, '[]', 'x')")
        calibracion.crear_tabla(conn)
        self.assertEqual(conn.execute("SELECT metrica FROM calibraciones").fetchall(), [("pm25",)])
        conn.execute("INSERT INTO calibraciones (station_id, metrica, modelo, a, b, valida_desde, "
                     "ventana_min, n_pares, validacion, n_validacion, pares_json, creada_en) "
                     "VALUES ('e', 'humedad', 'lineal', 1, 2, 'x', 60, 3, 'en_muestra', 3, '[]', 'x')")
        calibracion.crear_tabla(conn)  # idempotente
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM calibraciones").fetchone()[0], 2)

    def test_humedad_exp_usa_hr_calibrada(self):
        cal = calibracion.Calibrador([
            {"id": 1, "station_id": "e", "metrica": "humedad", "modelo": "lineal", "a": 21.7,
             "b": 1, "c": 0, "valida_desde": calibracion.DESDE_SIEMPRE},
            {"id": 2, "station_id": "e", "metrica": "pm25", "modelo": "humedad_exp",
             "a": 0.014, "b": 40, "c": 80, "valida_desde": calibracion.DESDE_SIEMPRE},
        ])
        ts = "2026-11-15T00:00:00Z"
        # HR cruda 70 -> calibrada 91,7 -> se corrige
        v, cid = cal.valor("e", "pm25", ts, 40.0, 70.0)
        self.assertEqual(cid, 2)
        self.assertAlmostEqual(v, round(40 / math.exp(0.014 * 51.7), 1))
        # HR cruda 50 -> 71,7: por debajo del umbral, sin cambio
        self.assertEqual(cal.valor("e", "pm25", ts, 40.0, 50.0), (40.0, 2))
        # Sin HR no se aplica
        self.assertEqual(cal.valor("e", "pm25", ts, 40.0, None), (40.0, None))
        fila = cal.calibrar_fila("e", {"timestamp": ts, "pm25": 40.0, "humedad": 70.0})
        self.assertTrue(fila["humedad_alta"])
        self.assertFalse(cal.calibrar_fila("e", {"timestamp": ts, "pm25": 4.0,
                                                 "humedad": 50.0})["humedad_alta"])

    def test_migra_tabla_sin_humedad_exp(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        calibracion._crear_tabla_actual(conn)
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='calibraciones'").fetchone()[0]
        conn.execute("DROP TABLE calibraciones")
        conn.execute(sql.replace("('lineal', 'humedad', 'humedad_exp')", "('lineal', 'humedad')"))
        conn.execute("INSERT INTO calibraciones (station_id, metrica, modelo, a, b, valida_desde, "
                     "ventana_min, n_pares, validacion, n_validacion, pares_json, creada_en) "
                     "VALUES ('e','humedad','lineal',21.7,1,'x',0,3,'en_muestra',3,'[]','x')")
        calibracion.crear_tabla(conn)
        calibracion.guardar_humedad_exp(conn, "e", "pm25")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM calibraciones").fetchone()[0], 2)
        with self.assertRaises(calibracion.ErrorCalibracion):
            calibracion.guardar_humedad_exp(conn, "e", "humedad")

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
        self.assertEqual(hist_hr[0]["y"], hist_hr[0]["crudo"])  # sin calibracion de HR

        r = self.cliente.post("/api/calibraciones", json=self._cuerpo(metrica="temperatura",
                              modelo="lineal"), headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        fila_t = self.cliente.get("/api/mediciones/ultimas?limite=1").get_json()[ESTACION][-1]
        self.assertEqual(fila_t["calibracion"]["temperatura"], r.get_json()["id"])
        t_ref = _temp_ref(calibracion.parsear_ts(fila_t["timestamp"]))
        self.assertAlmostEqual(fila_t["temperatura"], t_ref, delta=0.3)
        self.cliente.delete(f"/api/calibraciones/{r.get_json()['id']}", headers=self.h)

        # Correccion FIUNA por humedad alta: alta de PM2.5 y PM10 sin CSV.
        r = self.cliente.post("/api/calibraciones/humedad-alta", json={"estacion": ESTACION},
                              headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        ids_exp = [c["id"] for c in r.get_json()]
        self.assertEqual({c["modelo"] for c in r.get_json()}, {"humedad_exp"})
        fila_exp = self.cliente.get("/api/mediciones/ultimas?limite=1").get_json()[ESTACION][-1]
        self.assertIn("humedad_alta", fila_exp)
        if fila_exp["humedad"] <= 80:
            self.assertEqual(fila_exp["pm25"], fila_exp["pm25_crudo"])
        for i in ids_exp:
            self.cliente.delete(f"/api/calibraciones/{i}", headers=self.h)

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
                              json=self._cuerpo(metrica="co2"), headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.cliente.post("/api/calibraciones", json=self._cuerpo(modelo="cubico"),
                              headers=self.h)
        self.assertEqual(r.status_code, 400)
        # El modelo con HR no existe para temperatura ni para humedad.
        r = self.cliente.post("/api/calibraciones",
                              json=self._cuerpo(metrica="temperatura", modelo="humedad"),
                              headers=self.h)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
