"""Tests del prefiltro determinista (007, T3). Puros. Correr: python3 -m unittest -q tests.test_filtros"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import filtros  # noqa: E402

FIX_REAL = Path(__file__).resolve().parent / "fixtures" / "triage" / "casos.jsonl"          # privado (etiquetas reales del usuario)
FIX_PUB = Path(__file__).resolve().parent / "fixtures" / "triage" / "casos-publico.jsonl"   # sintético (espejo público)
FIX = FIX_REAL if FIX_REAL.exists() else FIX_PUB


def cargar_casos():
    return [json.loads(l) for l in FIX.read_text().splitlines() if l.strip()]


def as_gig(c):
    return {"title": c["titulo"], "desc": c["desc"], "location": c["ubicacion"], "lang": c["lang"], "source": c["fuente"]}


DESC_ES = "Buscamos una persona para automatizar procesos con n8n y Python. Trabajo remoto para el equipo de operaciones."
DESC_EN = "We are looking for an engineer to automate our workflows with n8n and Python. You will work with the team and our clients."


class ReglaIngles(unittest.TestCase):
    def test_lang_en_bloquea(self):
        self.assertEqual(filtros.prefiltro({"title": "Automation Engineer", "desc": DESC_EN, "lang": "en"}), (False, "ingles"))

    def test_heuristica_en_sin_lang(self):
        self.assertEqual(filtros.prefiltro({"title": "Automation Engineer", "desc": DESC_EN, "lang": ""}), (False, "ingles"))

    def test_es_pasa(self):
        self.assertEqual(filtros.prefiltro({"title": "Desarrollador de automatizaciones", "desc": DESC_ES, "lang": ""}), (True, ""))

    def test_ingles_que_declara_espanol_pasa(self):
        self.assertEqual(filtros.prefiltro({"title": "Automation Engineer", "desc": DESC_EN + " Spanish speaking team, español requerido.", "lang": "en"}), (True, ""))

    def test_ubicacion_no_cuenta(self):  # H1: "latam" en ubicación no convierte un aviso inglés en apto
        self.assertEqual(filtros.prefiltro({"title": "Automation Engineer", "desc": DESC_EN, "lang": "", "location": "LATAM, Europe"}), (False, "ingles"))


class ReglaSeniority(unittest.TestCase):
    def test_bloquea(self):
        for t in ("Senior Backend Developer", "Sr. Automation Engineer", "Tech Lead Full-Stack", "Gerente de TI / CTO", "Jefe de Proyecto", "Head of Engineering"):
            self.assertEqual(filtros.prefiltro({"title": t, "desc": DESC_ES}), (False, "seniority"), t)

    def test_semi_senior_no_bloquea(self):
        for t in ("Desarrollador Semi Senior Python", "Desarrollador SSR Python", "Semi-Senior Backend Python", "Desarrollador Python Semisenior"):
            ok, motivo = filtros.prefiltro({"title": t, "desc": DESC_ES})
            self.assertNotEqual(motivo, "seniority", t)


class ReglaRol(unittest.TestCase):
    def test_bloquea(self):
        for t, k in (("Desarrollador iOS", "ios"), ("QA Funcional", "qa"), ("ML Engineer (Forecasting)", "ml engineer"), ("Product Marketing Manager", "manager"),
                     ("Desarrollador de Negocio SSR", "desarrollador de negocio"), ("IBM DB2 Application DBA", "dba"), ("Forward Deployed Engineer", "forward deployed")):
            ok, motivo = filtros.prefiltro({"title": t, "desc": DESC_ES})
            self.assertFalse(ok, t); self.assertIn(motivo, ("rol", "seniority"), t)

    def test_perfil_pasa(self):
        for t in ("Desarrollador de Automatizaciones e IA", "Desarrollador Full-Stack Django + Next.JS", "Ingeniero de Implementación de IA"):
            self.assertEqual(filtros.prefiltro({"title": t, "desc": DESC_ES}), (True, ""), t)


class ReglaSinHit(unittest.TestCase):
    def test_sin_keyword_real(self):  # H3: el [q] de relleno no cuenta
        self.assertEqual(filtros.prefiltro({"title": "Asesor Externo de Ventas por Catálogo", "desc": "Buscamos asesor para venta de productos en terreno."}), (False, "rol"))
        self.assertEqual(filtros.prefiltro({"title": "Recepcionista", "desc": "Atención de público y agenda."}), (False, "sin_hit"))


class ReglaPresencial(unittest.TestCase):
    def test_fuera_de_santiago_bloquea(self):
        self.assertEqual(filtros.prefiltro({"title": "Desarrollador Python", "desc": DESC_ES, "location": "híbrido · Valparaíso"}), (False, "presencial"))

    def test_en_santiago_pasa(self):  # H5
        self.assertEqual(filtros.prefiltro({"title": "Desarrollador Python", "desc": DESC_ES, "location": "presencial · Santiago"}), (True, ""))
        self.assertEqual(filtros.prefiltro({"title": "Desarrollador Python", "desc": DESC_ES + " Oficinas en Quilicura.", "location": "presencial"}), (True, ""))


class FixturesPublicas(unittest.TestCase):  # casos sintéticos (viajan al espejo público): cada regla cubierta
    def test_sinteticos(self):
        casos = [json.loads(l) for l in FIX_PUB.read_text().splitlines() if l.strip()]
        self.assertGreaterEqual(len(casos), 10)
        motivos = set()
        for c in casos:
            ok, motivo = filtros.prefiltro(as_gig(c))
            self.assertEqual(ok, c["etiqueta"] == "apta", c["id"]); motivos.add(motivo)
        self.assertTrue({"ingles", "seniority", "rol", "sin_hit", "presencial"} <= motivos, motivos)


class Fixtures(unittest.TestCase):
    def test_no_aptas_reales_caen_por_regla(self):
        for c in cargar_casos():
            if c["etiqueta"] == "no_apta" and c["origen"] == "isaac":
                ok, motivo = filtros.prefiltro(as_gig(c))
                self.assertFalse(ok, c["id"]); self.assertIn(motivo, ("ingles", "seniority", "rol", "sin_hit", "presencial"), c["id"])

    @unittest.skipUnless(FIX_REAL.exists(), "sin fixtures reales (espejo público): el assert duro solo corre en el repo privado")
    def test_ASSERT_DURO_ninguna_apta_real_rechazada(self):
        for c in cargar_casos():
            if c["etiqueta"] == "apta" and c["origen"] == "isaac" and not c.get("excluido_eval"):
                self.assertEqual(filtros.prefiltro(as_gig(c)), (True, ""), c["id"])

    def test_excluidos_D1_caen_por_ingles(self):  # D1 (008): inglés = bloqueo duro; los excluidos no son ejemplo ni cuentan
        for c in cargar_casos():
            if c.get("excluido_eval"):
                self.assertEqual(c.get("motivo_exclusion"), "ingles_decision_A", c["id"])
                self.assertEqual(filtros.prefiltro(as_gig(c)), (False, "ingles"), c["id"])


if __name__ == "__main__":
    unittest.main()
