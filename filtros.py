"""filtros.py — prefiltro determinista ANTES del triage con LLM (007, T3). Puro: sin red, sin env, sin efectos.

prefiltro(g) -> (ok, motivo). Motivos estables: "ingles" | "seniority" | "rol" | "sin_hit" | "presencial" | "" (pasa).
Reglas en orden (la primera que bloquea decide). Todo lo que decide está en las LISTAS de abajo, no en la lógica.
g = dict con: title, desc, location, lang (opcional: "en"/"es"/""), source (opcional).
"""
import os
import re

# ---- keywords del radar (única fuente; buscar.py las importa de acá) ----
STRONG = ["n8n", "zapier", "make.com", "integromat", "automation", "automate",
          "chatbot", "whatsapp", "scraping", "scraper", "web scraping", "webhook",
          "no-code", "nocode", "low-code", "airtable", "api integration",
          "rpa", "process automation", "data pipeline", "integration engineer",
          # señales en español (para fuentes hispanas como GetOnBrd)
          "automatización", "automatizar", "automatizacion", "raspado web",
          "integración api", "integracion api", "bot de whatsapp",
          # 007: perfil = automatización / IA aplicada / web fullstack junior-semisenior
          "llm", "openai", "claude", "agente de ia", "agentes de ia", "ai agent", "inteligencia artificial",
          "python", "django", "fastapi", "next.js", "nextjs", "react", "node", "postgresql", "api rest", "rest api",
          # 010 (A3): roles puente — contratista remoto en español
          "soporte técnico", "soporte tecnico", "technical support", "implementación", "implementacion", "onboarding",
          "qa manual", "tester", "analista de datos", "data analyst", "help desk", "mesa de ayuda", "customer success",
          "operaciones", "operations", "implementation specialist", "support engineer"]
STRONG += [k.strip().lower() for k in os.environ.get("GIG_KEYWORDS_EXTRA", "").split(",") if k.strip()]
WEAK = ["workflow", "bot", "integration", "pipeline", "automated",
        "flujo de trabajo", "integración", "integracion", "backend", "full-stack", "fullstack", "full stack"]

# ---- regla 1: inglés ----
# Stopwords muy frecuentes de cada idioma; el aviso es "en inglés" si las inglesas dominan claramente.
EN_STOP = ["the", "and", "with", "you", "will", "our", "for", "are", "this", "that", "have", "from", "your", "team", "role"]
ES_STOP = ["que", "para", "con", "los", "las", "una", "del", "por", "como", "nuestro", "nuestra", "equipo", "buscamos", "experiencia", "trabajo"]
# La DESCRIPCIÓN (no la ubicación) declara español/LATAM-hispano → no se bloquea aunque esté en inglés.
ES_DECLARA = ["español", "espanol", "spanish", "habla hispana", "hispano", "castellano", "bilingüe", "bilingue", "bilingual"]

# ---- regla 2: seniority (solo título). "Semi Senior" / "SSR" / "Semi-Senior" NO bloquean. ----
SENIORITY = r"\b(senior|sr\.?|lead|principal|staff|head|director|manager|gerente|jefe|cto|vp)\b"
SEMI = r"\b(semi[ -]?senior|ssr)\b"

# ---- regla 3: rol fuera de perfil (solo título) ----
# 010 (A3): QA manual/tester, soporte/help desk, customer success, onboarding/implementación y analista de datos
# son ROLES PUENTE y ya no se bloquean (seniority sí sigue bloqueando).
PUENTE = ["soporte", "support", "help desk", "mesa de ayuda", "customer success", "onboarding", "implementaci",
          "implementation", "qa", "tester", "testing", "analista de datos", "data analyst", "operaciones", "operations"]
ROL_FUERA = ["ios", "android", "mobile", "móvil", "movil", "react native", "flutter",
             "data scientist", "ml engineer", "machine learning", "forecasting", "data engineer",
             "marketing", "growth", "content", "contenido", "community", "branding", "copywriter", "seo",
             "ventas", "sales", "desarrollador de negocio", "sdr", "account executive", "comercial",
             "dba", "administrator", "administrador",
             "design", "diseñ", "designer", "ux", "ui/ux",
             "forward deployed", "product manager", "product owner",
             "devops", "sre", "platform engineer", "siem", "security engineer",
             "java", ".net", "c++", "golang", "php", "salesforce", "dynamics", "outsystems", "sap", "wordpress"]

# ---- regla 5: presencial/híbrido fuera de Santiago de Chile ----
PRESENCIAL = ["presencial", "híbrido", "hibrido", "hybrid", "on-site", "onsite", "on site", "in-office", "in office",
              "must relocate", "relocation required", "no_remote"]
SANTIAGO = ["santiago", "quilicura", "providencia", "las condes", "región metropolitana", "region metropolitana", "rm, chile"]


def _has(k, t):
    if k.isalpha() and len(k) <= 5:
        return re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t) is not None
    return k in t


def hits(text):
    t = (text or "").lower()
    return [k for k in STRONG if _has(k, t)], [k for k in WEAK if _has(k, t)]


def _words(t):
    return re.findall(r"[a-záéíóúñü]+", (t or "").lower())


def es_ingles(g):
    """Idioma del AVISO: campo lang si la fuente lo da; si no, stopwords sobre título+desc."""
    lang = (g.get("lang") or "").lower()
    if lang in ("en", "es"):
        return lang == "en"
    w = _words((g.get("title") or "") + " " + (g.get("desc") or ""))
    en = sum(1 for x in w if x in EN_STOP)
    es = sum(1 for x in w if x in ES_STOP)
    return en >= 3 and en > 2 * es


def declara_espanol(desc):
    d = (desc or "").lower()
    return any(k in d for k in ES_DECLARA)


def titulo_seniority(title):
    t = (title or "").lower()
    t = re.sub(SEMI, " ", t)          # "semi senior"/"ssr" se retira antes de buscar "senior"
    return re.search(SENIORITY, t) is not None


def titulo_rol_fuera(title):
    t = (title or "").lower()
    return next((k for k in ROL_FUERA if _has(k, t)), "")


def presencial_fuera_santiago(g):
    loc = ((g.get("location") or "") + " " + (g.get("modality") or "")).lower()
    txt = loc + " " + (g.get("title") or "").lower()
    if not any(k in txt for k in PRESENCIAL):
        return False
    ciudad = loc + " " + (g.get("desc") or "").lower()
    return not any(k in ciudad for k in SANTIAGO)


def es_puente(title):
    t = (title or "").lower()
    return any(k in t for k in PUENTE)


def contrato_cl(g):
    """010 (A1/A2): dato duro de la fuente. País del aviso = Chile y modalidad híbrida/presencial/'remoto local'
    (residencia en Chile) → contrato local chileno. fully_remote desde Chile NO bloquea acá (lo decide el triage)."""
    paises = [str(x).lower() for x in (g.get("countries") or [])]
    if "chile" not in paises:
        return False
    return (g.get("modality") or "") in ("hybrid", "no_remote", "remote_local")


def prefiltro(g):
    if es_ingles(g) and not declara_espanol(g.get("desc")):
        return False, "ingles"
    if contrato_cl(g):
        return False, "contrato_cl"
    if titulo_seniority(g.get("title")):
        return False, "seniority"
    if titulo_rol_fuera(g.get("title")):
        return False, "rol"
    s, w = hits((g.get("title") or "") + " " + (g.get("desc") or ""))
    if not s and not w:
        return False, "sin_hit"
    if presencial_fuera_santiago(g):
        return False, "presencial"
    return True, ""
