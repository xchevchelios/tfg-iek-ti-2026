"""Pruebas del debug LoRa: validacion y que el pedido no quede retenido.

Correr desde api/:   python3 -m unittest discover -s pruebas -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("DB_PATH", str(Path(_TMP.name) / "prueba.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import configuracion_red  # noqa: E402
from configuracion_red import DEBUG_POR_DEFECTO, publicar_debug, validar_debug  # noqa: E402

BASE = {"activo": True, **DEBUG_POR_DEFECTO, "nodo": 2}


class ValidarDebug(unittest.TestCase):
    def test_valido_y_detener(self):
        self.assertIsNone(validar_debug(BASE))
        self.assertIsNone(validar_debug({"activo": False}))

    def test_rangos(self):
        for clave, malo in (("nodo", 0), ("nodo", 255), ("periodo_ms", 999), ("periodo_ms", 60001),
                            ("timeout_ms", 499), ("duracion_s", 59), ("duracion_s", 3601),
                            ("nodo", 2.0), ("nodo", True), ("nodo", "2")):
            self.assertIsNotNone(validar_debug({**BASE, clave: malo}), (clave, malo))
        self.assertIsNotNone(validar_debug({"activo": "si"}))

    def test_timeout_menor_que_periodo(self):
        self.assertIsNone(validar_debug({**BASE, "periodo_ms": 5000, "timeout_ms": 4900}))
        self.assertIn("menor que el periodo", validar_debug({**BASE, "periodo_ms": 5000, "timeout_ms": 4950}))


class PublicarDebug(unittest.TestCase):
    def test_no_se_retiene(self):
        with mock.patch.object(configuracion_red, "_publicar_retenido", return_value=True) as pub:
            self.assertTrue(publicar_debug(BASE))
            topic, carga = pub.call_args.args
            self.assertEqual(topic, "gateway/debug/config")
            self.assertEqual(pub.call_args.kwargs, {"retener": False})
            self.assertEqual(json.loads(carga), {"activo": True, "nodo": 2, "periodo_ms": 5000,
                                                 "timeout_ms": 4000, "duracion_s": 600})
            publicar_debug({"activo": False})
            self.assertEqual(json.loads(pub.call_args.args[1]), {"activo": False})


if __name__ == "__main__":
    unittest.main()
