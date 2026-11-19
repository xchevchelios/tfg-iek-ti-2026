"""Pruebas del resumen de fiabilidad por version de firmware.

Correr desde api/:   python3 -m unittest discover -s pruebas -v
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fiabilidad  # noqa: E402


def _base():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE estaciones (id TEXT, nombre TEXT, direccion_lora INTEGER);
        CREATE TABLE rondas_gateway (id INTEGER PRIMARY KEY, arranque_id INTEGER, ronda INTEGER,
            recibida TEXT, nodos_configurados INTEGER, duracion_ms INTEGER,
            excedida INTEGER DEFAULT 0, descartadas INTEGER, firmware TEXT);
        CREATE TABLE rondas_nodo (ronda_id INTEGER, direccion INTEGER, desenlace TEXT,
            sensor_ok INTEGER, rssi INTEGER);
        INSERT INTO estaciones VALUES ('estacion_01', 'E1', 1), ('estacion_02', 'E2', 2);
    """)
    # 10 rondas con 2.3.1 (nodo 2 falla 5) y 10 con 2.4.0 (nodo 2 falla 1), una
    # por hora desde las 00:00 UTC; la ronda 15 no se reporto (salto de numero).
    rid = 0
    for i in range(21):
        if i == 15:
            continue
        rid += 1
        fw = "2.3.1" if i < 10 else "2.4.0"
        c.execute("INSERT INTO rondas_gateway VALUES (?, 1, ?, ?, 2, 9000, 0, 0, ?)",
                  (rid, i + 1, f"2026-11-18T{i:02d}:00:00Z", fw))
        c.execute("INSERT INTO rondas_nodo VALUES (?, 1, 'fresca', 1, -80)", (rid,))
        falla = (fw == "2.3.1" and i % 2 == 0) or (fw == "2.4.0" and i == 12)
        c.execute("INSERT INTO rondas_nodo VALUES (?, 2, ?, ?, ?)",
                  (rid, "sin_respuesta" if falla else "fresca",
                   None if falla else 1, None if falla else -110))
    return c


class TestFiabilidad(unittest.TestCase):
    def test_version(self):
        self.assertEqual(fiabilidad.version("2.4.0"), (2, 4, 0))
        self.assertEqual(fiabilidad.version("v2.10"), (2, 10, 0))
        self.assertGreater(fiabilidad.version("2.10.0"), fiabilidad.version("2.4.0"))
        self.assertIsNone(fiabilidad.version("desconocido"))

    def test_filtra_por_firmware(self):
        r = fiabilidad.calcular(_base(), "2.4.0", "dia")
        self.assertEqual(r["incluidas"], ["2.4.0"])
        n2 = next(n for n in r["nodos"] if n["direccion"] == 2)
        self.assertEqual((n2["total"], n2["sin_respuesta"]), (10, 1))
        self.assertEqual(n2["pct_sin_respuesta"], 10.0)
        self.assertEqual(n2["nombre"], "E2")
        self.assertEqual(n2["rssi_medio"], -110.0)
        self.assertEqual(r["gateway"]["sin_reporte"], 1)

    def test_comparacion_y_serie(self):
        r = fiabilidad.calcular(_base(), None, "hora")
        vers = {v["firmware"]: v for v in r["versiones"]}
        self.assertEqual(vers["2.3.1"]["pct_sin_respuesta"], 25.0)  # 5 de 20 intentos
        self.assertEqual(vers["2.4.0"]["pct_sin_respuesta"], 5.0)   # 1 de 20
        # Hora local (UTC-3): la ronda de las 00:00 UTC cae el dia anterior.
        self.assertEqual(r["serie"]["periodos"][0], "2026-11-17 21:00")
        self.assertEqual(r["serie"]["nodos"]["2"][0], 100.0)

    def test_errores(self):
        with self.assertRaises(ValueError):
            fiabilidad.calcular(_base(), "x.y", "dia")
        with self.assertRaises(ValueError):
            fiabilidad.calcular(_base(), None, "semana")
        self.assertEqual(fiabilidad.calcular(_base(), "9.0.0")["nodos"], [])


if __name__ == "__main__":
    unittest.main()
