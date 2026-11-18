"""Pruebas de la validacion de parametros del gateway (configuracion_red.py).

Correr desde api/:   python3 -m unittest discover -s pruebas -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("DB_PATH", str(Path(_TMP.name) / "prueba.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from configuracion_red import POR_DEFECTO, duracion_maxima_ronda_s, validar_parametros  # noqa: E402


class Reintentos(unittest.TestCase):
    def test_por_defecto_es_10(self):
        self.assertEqual(POR_DEFECTO["reintentos"], 10)
        self.assertIsNone(validar_parametros(dict(POR_DEFECTO), 2))

    def test_rango_0_a_10(self):
        for valor in (0, 5, 10):
            self.assertIsNone(validar_parametros({**POR_DEFECTO, "reintentos": valor}, 2))
        for valor in (-1, 11, 2.5, True, "3"):
            self.assertIsNotNone(validar_parametros({**POR_DEFECTO, "reintentos": valor}, 2))

    def test_peor_ronda_cuenta_los_reintentos(self):
        # 2 nodos, 30 s de ventana, 3 s de timeout, 10 reintentos: ~100 s.
        p = dict(POR_DEFECTO)
        self.assertAlmostEqual(duracion_maxima_ronda_s(p, 2), 30 + 2 + 2 * 11 * 3.1, places=3)
        self.assertAlmostEqual(duracion_maxima_ronda_s({**p, "reintentos": 0}, 2), 30 + 2 + 2 * 3.1,
                               places=3)

    def test_periodo_corto_para_tantos_reintentos(self):
        # 10 nodos que no responden con 10 reintentos: 373 s > 300 s.
        motivo = validar_parametros(dict(POR_DEFECTO), 10)
        self.assertIn("al menos 373 s", motivo)
        self.assertIsNone(validar_parametros({**POR_DEFECTO, "periodo_s": 600}, 10))

    def test_sin_reintentos_en_el_dict_usa_el_defecto(self):
        p = {k: v for k, v in POR_DEFECTO.items() if k != "reintentos"}
        self.assertEqual(duracion_maxima_ronda_s(p, 2), duracion_maxima_ronda_s(POR_DEFECTO, 2))


if __name__ == "__main__":
    unittest.main()
