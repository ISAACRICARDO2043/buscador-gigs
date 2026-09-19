"""Tests puros (sin red, sin escritura). Pinean los bugs medidos en F0 (H4a/H4b/H5')
y la LEY DEL CANAL REAL (DRY_RUN). Correr: python3 -m unittest -q tests.test_puro"""
import os
import re
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
        self.assertEqual(v, {"tipo": "empleo", "apto": False, "motivo": "x", "texto": "", "huecos": []})

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
        self.assertEqual(res, "plantilla"); self.assertIn("me dedico a", llamadas[0][2])  # T3/T8: sin nombre no hay "soy"


class DetectorIngles(unittest.TestCase):  # 005 — T2 (medido: 7/123 sin marcar, frases C1/B2)
    def test_positivos(self):
        for txt in ("This role requires applying in English", "Inglés C1", "English B2 or higher",
                    "C1 English level or higher", "Fluent in English required", "Nivel de inglés avanzado"):
            self.assertTrue(buscar.needs_english(txt), txt)

    def test_negativos(self):
        for txt in ("Atenderás English-speaking customers vía chatbot", "Automatización con n8n, 100% remoto, español",
                    "Integración de APIs para pyme en Chile"):
            self.assertFalse(buscar.needs_english(txt), txt)

    def test_d24_elegible_auto(self):
        self.assertFalse(buscar.elegible_auto({"en": True, "esp": False}))   # exige inglés, no LATAM → sin cupo
        self.assertTrue(buscar.elegible_auto({"en": True, "esp": True}))     # bilingüe/LATAM → sí
        self.assertTrue(buscar.elegible_auto({"en": False, "esp": False}))


VOSEO = re.compile(r"\b(necesit[aá]s|pod[eé]s|cont[aá]s|ten[eé]s|quer[eé]s|vos)\b")


class PlantillaSinNombre(unittest.TestCase):  # 005 — T3 · 007 — T8
    def test_sin_perfil(self):
        p = proponer.via_plantilla("", "Bot n8n", "necesito n8n")
        self.assertNotIn("freelancer", p); self.assertNotIn("soy", p.lower().split("\n")[0].replace("hola, me", "")); self.assertIn("Hola, me dedico a", p)

    def test_con_nombre(self):
        p = proponer.via_plantilla("cómo me presento: Ana", "Bot n8n", "necesito n8n")
        self.assertIn("Hola, soy Ana", p); self.assertTrue(p.endswith("— Ana"))


class CartasIsacc(unittest.TestCase):  # 007 — T8: grafía Isacc, empleo sin precio, sin voseo, sin nivel de inglés
    PERFIL = "cómo me presento: Isacc\nubicación: Santiago, Chile"

    def test_empleo_sin_precio_con_isacc(self):
        p = proponer.via_plantilla(self.PERFIL, "Desarrollador de Automatizaciones", "n8n y python", tipo="empleo")
        self.assertIn("Isacc", p); self.assertNotIn("USD", p); self.assertNotIn("inglés", p.lower().replace("español nativo", ""))
        self.assertIsNone(VOSEO.search(p), p)

    def test_proyecto_con_rango_sin_voseo(self):
        p = proponer.via_plantilla(self.PERFIL, "Bot de WhatsApp para cotizaciones", "automatización whatsapp", tipo="proyecto")
        self.assertIn("Isacc", p); self.assertIn("USD", p); self.assertIsNone(VOSEO.search(p), p)

    def test_prompt_sin_nivel_de_ingles_ni_voseo(self):
        self.assertNotIn("inglés básico", proponer.REGLAS_TRIAGE); self.assertIn("Español nativo.", proponer.REGLAS_TRIAGE)


class Pipeline(unittest.TestCase):  # 005 — T4 (DRY_RUN=1 en este proceso → log_pipeline NO escribe)
    G = {"id": "X:1", "title": "t", "company": "C", "url": "u", "source": "X"}

    def test_record(self):
        r = proponer.pipeline_record(self.G, "proyecto", "n8n")
        self.assertEqual((r["id"], r["sig"], r["tipo"], r["via"], r["fuente"]), ("X:1", "c|t", "proyecto", "n8n", "X"))
        self.assertTrue(r["ts"].startswith("20"))

    def test_log_dry_run_no_escribe(self):
        import tempfile
        f = Path(tempfile.mkdtemp()) / "p.jsonl"
        self.assertFalse(proponer.log_pipeline(proponer.pipeline_record(self.G, "proyecto", "n8n"), f))
        self.assertFalse(f.exists())

    def test_log_escribe_sin_dry_run(self):
        import tempfile, json
        f = Path(tempfile.mkdtemp()) / "p.jsonl"
        orig = proponer.DRY_RUN; proponer.DRY_RUN = False
        try:
            self.assertTrue(proponer.log_pipeline(proponer.pipeline_record(self.G, "empleo", "telegram"), f))
        finally:
            proponer.DRY_RUN = orig
        self.assertEqual(json.loads(f.read_text().splitlines()[0])["id"], "X:1")


class Metricas(unittest.TestCase):  # 005 — T4 metricas.py (funciones puras + tmpdir)
    def test_enviadas_por_semana(self):
        import tempfile, json, metricas
        f = Path(tempfile.mkdtemp()) / "p.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in [
            {"ts": "2026-09-14T10:00:00", "via": "n8n"}, {"ts": "2026-09-15T10:00:00", "via": "telegram"},
            {"ts": "2026-09-15T11:00:00", "via": "", "estado": "prefiltro", "motivo": "ingles"},        # 007+: no es envío
            {"ts": "2026-09-21T10:00:00", "via": "n8n"}]) + "\n")
        m = metricas.enviadas_por_semana(f)
        self.assertEqual(m["2026-W38"], {"nuevos": 3, "enviadas": 2, "n8n": 1, "telegram": 1}); self.assertEqual(m["2026-W39"]["enviadas"], 1)
        self.assertEqual(metricas.enviadas_por_semana(f.parent / "nada.jsonl"), {})

    def test_decisiones_por_semana(self):
        import metricas
        rows = [{"decision": "aprobada", "ts": "2026-09-18T04:00:00"}, {"decision": "descartada", "ts": "2026-09-18T05:00:00"},
                {"decision": "vencida", "createdAt": "2026-09-19T05:00:00Z"}]
        self.assertEqual(metricas.decisiones_por_semana(rows)["2026-W38"], {"aprobada": 1, "descartada": 1, "vencida": 1})


class Resumen(unittest.TestCase):  # 007 — T5 (funciones puras)
    FILAS = [{"ts": "2026-09-18T09:01:00", "estado": "entregado", "titulo": "Dev n8n", "fuente": "GetOnBrd", "motivo": "ok"},
             {"ts": "2026-09-18T09:02:00", "estado": "prefiltro", "titulo": "Senior X", "fuente": "WWR", "motivo": "seniority"},
             {"ts": "2026-09-18T09:03:00", "estado": "prefiltro", "titulo": "Y", "fuente": "WWR", "motivo": "ingles"},
             {"ts": "2026-09-18T12:00:00", "estado": "triage_no_apto", "titulo": "Z", "fuente": "Jobicy", "motivo": "stack ajeno"},
             {"ts": "2026-09-17T21:00:00", "estado": "entregado", "titulo": "ayer", "fuente": "GetOnBrd", "motivo": ""}]

    def test_filas_del_dia(self):
        import tempfile, json, resumen
        f = Path(tempfile.mkdtemp()) / "p.jsonl"; f.write_text("".join(json.dumps(r) + "\n" for r in self.FILAS))
        self.assertEqual(len(resumen.filas_del_dia(f, "2026-09-18")), 4)
        self.assertEqual(resumen.filas_del_dia(f.parent / "no.jsonl", "2026-09-18"), [])

    def test_resumir(self):
        import resumen
        txt = resumen.resumir(self.FILAS[:4], {"aprobada": 1})
        self.assertIn("nuevos: 4 · aptos enviados: 1 · descartados por triage: 1", txt)
        self.assertIn("senior/lead/manager 1", txt); self.assertIn("en inglés 1", txt); self.assertIn("✅ Dev n8n", txt); self.assertIn("aprobadas 1", txt)


class Huecos(unittest.TestCase):  # 008 — T3 (D2/D3)
    def test_parse_huecos(self):
        v = proponer.parse_claude_output('{"tipo":"empleo","apto":true,"motivo":"junior","texto":"t","huecos":["Rails"," React ",""]}')
        self.assertEqual(v["huecos"], ["rails", "react"])
        v = proponer.parse_claude_output('{"tipo":"empleo","apto":true,"motivo":"m","texto":"t"}')
        self.assertEqual(v["huecos"], [])                                    # sin campo → []
        v = proponer.parse_claude_output('{"tipo":"empleo","apto":true,"motivo":"m","texto":"t","huecos":"rails"}')
        self.assertEqual(v["huecos"], [])                                    # inválido → [], no rompe

    def test_contar_huecos_umbral(self):
        import metricas
        filas = [{"ts": "2026-09-18T10:00:00", "estado": "entregado", "huecos": ["rails", "react"]},
                 {"ts": "2026-09-17T10:00:00", "estado": "triage_no_apto", "huecos": ["rails"]},
                 {"ts": "2026-09-16T10:00:00", "estado": "entregado_plantilla", "huecos": ["Rails"]},
                 {"ts": "2026-09-15T10:00:00", "estado": "prefiltro", "huecos": ["java"]},          # prefiltro no cuenta
                 {"ts": "2026-07-01T10:00:00", "estado": "entregado", "huecos": ["rails"]},         # viejo no cuenta
                 {"ts": "2026-09-18T11:00:00", "estado": "entregado"}]                             # sin campo
        c = metricas.contar_huecos(filas, "2026-09-18", dias=30, umbral=3)
        self.assertEqual(c, {"rails": 3, "react": 1})
        self.assertTrue(c["rails"] >= 3 and c["react"] < 3)

    def test_pipeline_record_huecos(self):
        r = proponer.pipeline_record({"id": "x", "title": "t"}, "empleo", "n8n", huecos=["a", "b", "c", "d", "e", "f"])
        self.assertEqual(r["huecos"], ["a", "b", "c", "d", "e"])


class Destino(unittest.TestCase):  # 008 — T4 (D4)
    def test_prod(self):
        self.assertEqual(proponer.destino({"CANAL": "prod", "TELEGRAM_CHAT_ID": "111"}), ("prod", "111"))
        self.assertEqual(proponer.destino({"TELEGRAM_CHAT_ID": "111"}), ("prod", "111"))          # default prod

    def test_test_con_id(self):
        self.assertEqual(proponer.destino({"CANAL": "test", "TELEGRAM_CHAT_ID": "111", "TELEGRAM_CHAT_ID_TEST": "222"}), ("test", "222"))

    def test_test_sin_id_es_dry(self):
        self.assertEqual(proponer.destino({"CANAL": "test", "TELEGRAM_CHAT_ID": "111", "TELEGRAM_CHAT_ID_TEST": ""}), ("dry", ""))

    def test_dry_run_gana(self):
        self.assertEqual(proponer.destino({"CANAL": "prod", "DRY_RUN": "1", "TELEGRAM_CHAT_ID": "111"}), ("dry", ""))
        self.assertEqual(proponer.destino({"CANAL": "dry", "TELEGRAM_CHAT_ID": "111"}), ("dry", ""))


class HeaderWebhook(unittest.TestCase):  # 008 — T5a (D5): request capturado, sin red
    def test_con_token(self):
        req = proponer.armar_request_n8n("t", "u", "p", chat="1", token="abc")
        self.assertEqual(req.get_header("X-gigs-token"), "abc")
        self.assertIn(b'"chat_id": "1"', req.data)

    def test_sin_token(self):
        req = proponer.armar_request_n8n("t", "u", "p", chat="1", token="")
        self.assertFalse(req.has_header("X-gigs-token"))


class Pendientes(unittest.TestCase):  # 009 — T2 (fixture sintética, sin ids reales)
    FX = Path(__file__).resolve().parent / "fixtures" / "pendientes-4.json"

    def test_cuenta_4_con_antiguedad(self):
        import metricas, json
        from datetime import datetime, timezone
        p = metricas.contar_pendientes(json.load(open(self.FX)), ahora=datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc))
        self.assertEqual(p["n"], 4)
        self.assertEqual(p["items"][0]["titulo"], "Caso sintético A"); self.assertAlmostEqual(p["items"][0]["horas"], 14.0, places=1)
        self.assertEqual(p["items"][-1]["titulo"], "(sin título)")

    def test_api_no_disponible_es_sd_no_cero(self):
        import metricas, resumen
        self.assertIsNone(metricas.contar_pendientes(None))
        self.assertIn("Pendientes de tu tap: s/d", resumen.resumir([], {"aprobada": 1}, None))
        self.assertNotIn("Pendientes de tu tap: 0", resumen.resumir([], {"aprobada": 1}, None))

    def test_resumen_lista_pendientes(self):
        import metricas, resumen, json
        p = metricas.contar_pendientes(json.load(open(self.FX)))
        txt = resumen.resumir([], {"aprobada": 1, "vencida": 2}, p)
        self.assertIn("Pendientes de tu tap: 4", txt); self.assertIn("⏳ Caso sintético A", txt); self.assertIn("vencidas 2", txt)


if __name__ == "__main__":
    unittest.main()
