"""Tests puros (sin red, sin escritura). Pinean los bugs medidos en F0 (H4a/H4b/H5')
y la LEY DEL CANAL REAL (DRY_RUN). Correr: python3 -m unittest -q tests.test_puro"""
import os
import sys
import unittest
from pathlib import Path

os.environ["DRY_RUN"] = "1"      # antes de importar: los módulos leen el env al cargar
os.environ["TRANSLATE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buscar      # noqa: E402
import proponer    # noqa: E402


class PickArchetype(unittest.TestCase):  # T4 — H4a/H4b: palabra entera, no substring
    def test_both_no_es_bot(self):
        self.assertNotEqual(proponer.pick_archetype("we need both apps synced"), "bot-whatsapp")

    def test_capital_no_es_api(self):
        self.assertNotEqual(proponer.pick_archetype("capital integration project"), "integracion")

    def test_positivos(self):
        self.assertEqual(proponer.pick_archetype("whatsapp bot"), "bot-whatsapp")
        self.assertEqual(proponer.pick_archetype("build a chatbot for support"), "bot-whatsapp")
        self.assertEqual(proponer.pick_archetype("api integration with hubspot"), "integracion")
        self.assertEqual(proponer.pick_archetype("scrape prices daily"), "scraper")
        self.assertEqual(proponer.pick_archetype("zapier workflow"), "automation-workflow")


class GeoStatus(unittest.TestCase):  # T5 — H5': brazil/emea sueltos ya no bloquean
    def test_latam_con_brazil_en_desc(self):
        self.assertNotEqual(buscar.geo_status("Remote", "Automation Engineer", "LATAM incl. Brazil ok"), "block")

    def test_titulo_brazil_or_chile(self):
        self.assertNotEqual(buscar.geo_status("LATAM", "Automation Engineer (Brazil or Chile)", ""), "block")

    def test_emea_team_en_desc(self):
        self.assertNotEqual(buscar.geo_status("Remote", "n8n dev", "also emea team"), "block")

    def test_usa_only_sigue_bloqueado(self):
        self.assertEqual(buscar.geo_status("Anywhere", "USA Only — Zapier expert", ""), "block")

    def test_brazil_only_bloqueado(self):
        self.assertEqual(buscar.geo_status("Remote", "dev", "brazil only role"), "block")


class DryRun(unittest.TestCase):  # T3 — LEY DEL CANAL REAL
    def test_flag_cargado(self):
        self.assertTrue(proponer.DRY_RUN)
        self.assertTrue(buscar.DRY_RUN)

    def test_deliver_no_envia(self):
        self.assertEqual(proponer.deliver("t", "", "p"), "dry-run")

    def test_senders_no_envian(self):
        self.assertFalse(proponer.send_to_n8n("t", "", "p"))
        self.assertFalse(proponer.send_to_telegram("t", "", "p"))
        self.assertFalse(buscar.tg_send("hola"))

    def test_log_interes_no_escribe(self):
        antes = proponer.INTERESES.exists() and proponer.INTERESES.read_bytes()
        kws = proponer.log_interes("bot de whatsapp", "automation", "")
        self.assertIn("whatsapp", kws)
        despues = proponer.INTERESES.exists() and proponer.INTERESES.read_bytes()
        self.assertEqual(antes, despues)


class TriageClaude(unittest.TestCase):  # 003 — T1: parse + DRY_RUN no invoca claude
    ENV = '{"type":"result","is_error":false,"result":"{\\"tipo\\":\\"empleo\\"}","structured_output":{"tipo":"proyecto","apto":true,"motivo":"m","texto":"t"}}'

    def test_parse_fences(self):
        v = proponer.parse_claude_output('```json\n{"tipo":"empleo","apto":false,"motivo":"x","texto":""}\n```')
        self.assertEqual(v, {"tipo": "empleo", "apto": False, "motivo": "x", "texto": ""})

    def test_parse_envelope(self):
        v = proponer.parse_claude_output(self.ENV)
        self.assertEqual(v["tipo"], "proyecto"); self.assertTrue(v["apto"])

    def test_parse_basura(self):
        self.assertIsNone(proponer.parse_claude_output("no hay json acá"))
        self.assertIsNone(proponer.parse_claude_output(""))
        self.assertIsNone(proponer.parse_claude_output('{"tipo":"otro","apto":true,"motivo":"","texto":""}'))
        self.assertIsNone(proponer.parse_claude_output('{"tipo":"empleo","apto":"si","motivo":"","texto":""}'))

    def test_dry_run_no_invoca_subprocess(self):
        import subprocess
        orig = subprocess.run
        def boom(*a, **k): raise AssertionError("subprocess.run invocado en DRY_RUN")
        subprocess.run = boom
        try:
            self.assertIsNone(proponer.redactar("perfil", "t", "x", "test"))
        finally:
            subprocess.run = orig
        self.assertFalse(proponer.TRIAGE_FILE.exists() and "\"test\"" in proponer.TRIAGE_FILE.read_text()[-500:])

    def test_claude_ausente_fallback_sin_excepcion(self):  # control negativo D13
        orig_env, orig_dry = os.environ.get("PATH"), proponer.DRY_RUN
        proponer.DRY_RUN = False
        os.environ["PATH"] = "/nonexistent"
        try:
            self.assertFalse(proponer.claude_disponible())
            self.assertIsNone(proponer.via_claude("perfil", "t", "x"))
        finally:
            os.environ["PATH"] = orig_env; proponer.DRY_RUN = orig_dry


class TriageBuscar(unittest.TestCase):  # 003 — T2: apto=False → deliver NO se llama
    G = {"title": "Senior QA Dynamics", "desc": "", "url": "", "company": "c", "source": "test"}

    def _run(self, veredicto):
        llamadas = []
        orig_r, orig_d = proponer.redactar, proponer.deliver
        proponer.redactar = lambda *a, **k: veredicto
        proponer.deliver = lambda *a, **k: llamadas.append(a) or "n8n"
        try:
            return buscar.proponer_gig(self.G, "perfil"), llamadas
        finally:
            proponer.redactar, proponer.deliver = orig_r, orig_d

    def test_no_apto_no_entrega(self):
        res, llamadas = self._run({"tipo": "empleo", "apto": False, "motivo": "senior", "texto": ""})
        self.assertEqual(res, "descartado"); self.assertEqual(llamadas, [])

    def test_apto_entrega_texto_claude(self):
        res, llamadas = self._run({"tipo": "proyecto", "apto": True, "motivo": "ok", "texto": "HOLA"})
        self.assertEqual(res, "entregado"); self.assertEqual(llamadas[0][2], "HOLA")

    def test_none_cae_a_plantilla(self):
        res, llamadas = self._run(None)
        self.assertEqual(res, "plantilla"); self.assertIn("Soy", llamadas[0][2])


if __name__ == "__main__":
    unittest.main()
