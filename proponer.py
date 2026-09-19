#!/usr/bin/env python3
"""proponer.py — redacta una propuesta personalizada en español para un gig.

Uso:
  ./proponer.py "https://www.getonbrd.com/jobs/xxxx"     # URL: la descarga
  ./proponer.py "Necesito un bot de WhatsApp que..."      # texto del gig directo
  pbpaste | ./proponer.py                                  # texto pegado por stdin

Motor (003, D12): `claude -p` hace TRIAGE + redacción (JSON estricto: tipo/apto/motivo/texto).
  - apto=false -> no se entrega (queda en .triage.jsonl).
  - claude ausente/expirado/no-JSON -> plantilla + aviso (D13). DRY_RUN=1 no invoca claude.
El perfil sale de perfil.md. Cero credenciales de plataformas de empleo.
"""
import os, sys, re, json, shutil, subprocess, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
PERFIL = HERE / "perfil.md"
INTERESES = HERE / ".intereses.json"   # señal de aprendizaje: gigs que te interesaron
UA = "gig-proposer/1.0"


def _load_env():
    """Carga buscador-gigs/.env al entorno (sin dependencias) para que el script
    encuentre las keys solo. Lo ya presente en el entorno manda (no se pisa)."""
    f = HERE / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# Vocabulario compartido con buscar.py (mismos términos crudos que sus "hits")
# para que el ranking aprendido haga match. Si lo tocás, tocá ambos.
KW_VOCAB = ["n8n", "zapier", "make.com", "integromat", "automation", "automate",
            "chatbot", "whatsapp", "scraping", "scraper", "web scraping", "webhook",
            "no-code", "nocode", "low-code", "airtable", "api integration", "rpa",
            "process automation", "data pipeline", "automatización", "automatizar",
            "integración", "workflow", "bot", "pipeline"]

# Rangos orientativos por arquetipo (USD). Fuente: precios-mercado.md. (proj_min, proj_max).
PRICE_RANGES = {
    "bot-whatsapp": (400, 1800),
    "scraper": (250, 1200),
    "integracion": (1000, 5000),
    "automation-workflow": (150, 600),
}


def pick_archetype(text):
    t = (text or "").lower()
    w = lambda pat: re.search(r"\b" + pat + r"\b", t) is not None  # palabra entera: "both" no es "bot"
    if w("whatsapp") or w("bots?") or w("chatbots?"):
        return "bot-whatsapp"
    if w(r"scrap\w*"):
        return "scraper"
    if (w("apis?") and (w(r"integraci[oó]n(es)?") or w(r"integrations?"))) or "multi-system" in t:
        return "integracion"
    return "automation-workflow"


def price_hint(text):
    """Rango orientativo para anclar la propuesta, según el arquetipo del gig."""
    lo, hi = PRICE_RANGES[pick_archetype(text)]
    return f"USD {lo:,}–{hi:,} por proyecto (orientativo, a cerrar tras ver el alcance)"

TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "sonnet").strip()   # modelo de `claude -p` (D12)
TRIAGE_FILE = HERE / ".triage.jsonl"                                 # veredictos, gitignored
PIPELINE_FILE = Path(os.environ.get("PIPELINE_FILE") or (HERE / ".pipeline.jsonl"))   # entregas (005, D21a), gitignored
N8N_WEBHOOK = os.environ.get("N8N_WEBHOOK_URL", "").strip()  # capa de entrega/aprobación
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()  # entrega directa (fallback de n8n)
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TG_CHAT_TEST = os.environ.get("TELEGRAM_CHAT_ID_TEST", "").strip()
N8N_TOKEN = os.environ.get("N8N_WEBHOOK_TOKEN", "").strip()   # D5: header X-Gigs-Token si no está vacío (nunca se imprime)


def destino(env):
    """D4 (008): canal de salida. Devuelve (modo, chat_id) con modo ∈ prod|test|dry. Pura.
    DRY_RUN=1 gana → dry. CANAL=test sin TELEGRAM_CHAT_ID_TEST → dry (se avisa por stderr)."""
    if (env.get("DRY_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return "dry", ""
    canal = (env.get("CANAL") or "prod").strip().lower()
    if canal == "dry":
        return "dry", ""
    if canal == "test":
        chat = (env.get("TELEGRAM_CHAT_ID_TEST") or "").strip()
        if not chat:
            print("[canal test sin destino → dry]", file=sys.stderr)
            return "dry", ""
        return "test", chat
    return "prod", (env.get("TELEGRAM_CHAT_ID") or "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")  # LEY DEL CANAL REAL: sin red ni escritura


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style|nav|footer|header)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def gig_from_input(arg):
    """Devuelve (titulo, texto) del gig a partir de URL o texto pegado."""
    if arg.startswith("http"):
        if "getonbrd.com/jobs/" in arg:
            slug = arg.rstrip("/").split("/jobs/")[-1].split("?")[0]
            try:
                d = json.loads(fetch(f"https://www.getonbrd.com/api/v0/jobs/{slug}"))
                a = d.get("data", {}).get("attributes", {})
                titulo = a.get("title", "") or slug
                texto = strip_html(a.get("description", ""))
                return titulo, (texto or titulo)
            except Exception as e:
                print(f"[getonbrd] no pude usar la API ({e}), bajo el HTML…", file=sys.stderr)
        html = fetch(arg)
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        titulo = strip_html(m.group(1)) if m else arg
        titulo = re.split(r"\s[|–]\s", titulo)[0].strip()  # sacar " | Get on Board" y similares
        return titulo, strip_html(html)[:4000]
    # texto pegado directo
    txt = arg.strip()
    titulo = txt.split("\n", 1)[0][:80] if txt else "(gig sin título)"
    return titulo, txt


def load_perfil():
    try:
        return PERFIL.read_text()
    except Exception:
        return "(perfil.md no encontrado — completá tu perfil)"


TRIAGE_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"tipo": {"type": "string", "enum": ["empleo", "proyecto"]},
                                "apto": {"type": "boolean"},
                                "motivo": {"type": "string"},
                                "texto": {"type": "string"},
                                "huecos": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                                "pais_empleador": {"type": "string"},
                                "contratista_ok": {"type": "boolean"}},
                 "required": ["tipo", "apto", "motivo", "texto", "huecos", "pais_empleador", "contratista_ok"]}

REGLAS_TRIAGE = (
    "Eres el asistente de un desarrollador freelance de automatización e IA aplicada (junior a semi-senior, "
    "habla español, vive en Santiago de Chile). Te paso SU PERFIL, EJEMPLOS ETIQUETADOS por él y UN GIG. "
    "Devuelve SOLO un JSON con: tipo ('empleo' si es un puesto/vacante, 'proyecto' si es un encargo "
    "puntual), apto (true/false), motivo (1 línea), texto, huecos (lista, máx 5, minúsculas: herramientas/tecnologías "
    "que el aviso pide y NO están en el perfil; [] si no hay), pais_empleador (país del empleador o 'desconocido') y "
    "contratista_ok (true si el aviso admite contractor/freelance/independiente, Deel, pago en USD o 'cualquier país').\n"
    "REGLA CONTRATO_CL (bloqueo duro): empleador chileno que exige contrato local chileno, presencialidad o residencia en "
    "Chile → apto=false, motivo que empiece con 'contrato_cl'. Empleador desconocido NO bloquea. Si PAÍS (dato de la fuente) "
    "viene informado, gana sobre tu inferencia.\n"
    "ROLES PUENTE (aptos si no piden inglés ni seniority): soporte técnico, implementación/onboarding de SaaS, QA manual, "
    "analista de datos/automatización jr, operaciones con IA.\n"
    "MODALIDAD: si pais_empleador ≠ Chile, la carta incluye la línea 'Modalidad:' del perfil tal cual; si es Chile, no.\n"
    "REGLA DE INGLÉS (bloqueo duro): apto=false si el aviso está en inglés, exige inglés (fluido/avanzado/C1/B2) o "
    "postular en inglés, SALVO que la descripción declare español/LATAM hispano.\n"
    "REGLA JUNIOR: si el aviso es junior/trainee/práctica/'sin experiencia'/semi-junior, un stack que él no tiene NO "
    "descalifica: apto=true y ese stack va en huecos (la carta no afirma experiencia en un hueco: dice qué hizo y que "
    "aprende rápido con evidencia en sus repos). En avisos no-junior, un stack central ajeno sí descalifica, y también va en huecos.\n"
    "apto=false también si: seniority Senior/Lead/Principal/Manager/Head; rol fuera de perfil (QA, iOS/Android, "
    "data science/ML, marketing, ventas, soporte, DBA, diseño, DevOps/SRE); presencial o híbrido FUERA de Santiago de "
    "Chile (en Santiago sí sirve); o nada que ver con automatización, integraciones, bots, scraping, agentes con LLM o "
    "desarrollo web fullstack. En ese caso texto=''.\n"
    "Usa los EJEMPLOS ETIQUETADOS como criterio: lo que él marcó apta/no_apta pesa más que tu intuición.\n"
    "apto=true → texto en español neutro (NUNCA voseo: ni 'vos' ni conjugaciones rioplatenses terminadas en -ás/-és/-ís; "
    "usa 'usted' o formas neutras), sin encabezados ni notas, firmado con el nombre EXACTO del perfil ('cómo me presento'):\n"
    "  - empleo: carta de EXACTAMENTE 6 líneas: saludo + quién soy y ciudad; por qué encaja (la skill exacta que piden "
    "y dónde la usé según el perfil); UN caso concreto del perfil con resultado; stack y forma de trabajar; GitHub "
    "github.com/ISAACRICARDO2043; cierre 'Quedo atento a una conversación. Español nativo.'. SIN precio. "
    "No menciones nivel de inglés.\n"
    "  - proyecto: propuesta de 6 a 9 líneas al cliente (no sabes su nombre), foco en el resultado, cierra con una "
    "pregunta concreta, y menciona UNA vez el RANGO ORIENTATIVO tal cual, aclarando que el precio se cierra al conocer "
    "el alcance. PROHIBIDO un número único o distinto al rango.\n"
    "PROHIBIDO inventar experiencia, clientes o proyectos que no estén en el perfil.")


def claude_disponible():
    return shutil.which("claude") is not None


def parse_claude_output(raw):
    """Devuelve el dict {tipo, apto, motivo, texto} o None. Tolera el envelope de
    `--output-format json` (structured_output / result), fences ```json y texto suelto."""
    if not raw or not raw.strip():
        return None
    obj = None
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None
    if not isinstance(obj, dict):
        return None
    if "structured_output" in obj or "result" in obj:          # envelope de claude -p
        inner = obj.get("structured_output")
        if not isinstance(inner, dict):
            inner = parse_claude_output(obj.get("result") or "")
        obj = inner
    if not isinstance(obj, dict) or not {"tipo", "apto", "motivo", "texto"} <= set(obj):
        return None
    if obj["tipo"] not in ("empleo", "proyecto") or not isinstance(obj["apto"], bool):
        return None
    h = obj.get("huecos")
    huecos = [str(x).strip().lower() for x in h if str(x).strip()][:5] if isinstance(h, list) else []   # D2: inválido → []
    pais = obj.get("pais_empleador")
    pais = pais.strip() if isinstance(pais, str) and pais.strip() else "desconocido"                     # 010: inválido → desconocido
    contr = obj.get("contratista_ok") is True                                                             # 010: inválido → False
    return {"tipo": obj["tipo"], "apto": obj["apto"], "motivo": str(obj["motivo"]),
            "texto": str(obj["texto"]), "huecos": huecos, "pais_empleador": pais, "contratista_ok": contr}


CASOS_FILE = HERE / "tests" / "fixtures" / "triage" / "casos.jsonl"   # ejemplos etiquetados por el usuario (007, T6)


def ejemplos_etiquetados(excluir_id=None, origen="isaac", max_desc=220):
    """Few-shot desde casos.jsonl (solo origen=isaac). excluir_id: leave-one-out para la evaluación. Puro salvo lectura."""
    out = []
    try:
        for line in CASOS_FILE.read_text().splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if c.get("origen") != origen or c.get("id") == excluir_id or c.get("excluido_eval"):
                continue
            d = re.sub(r"\s+", " ", c.get("desc") or "")[:max_desc]
            out.append(f"- [{c['etiqueta'].upper()}] {c['titulo']} ({c.get('fuente','')}, {c.get('ubicacion','') or 'remoto'})"
                       + (f": {d}…" if d else ""))
    except Exception:
        return ""
    return "\n".join(out)


def armar_prompt(perfil, titulo, texto, fuente="", excluir_id=None, pais=None):
    rango = price_hint(titulo + " " + texto)
    ejemplos = ejemplos_etiquetados(excluir_id=excluir_id)
    pais_txt = f"PAÍS (dato de la fuente): {', '.join(pais)}\n" if pais else ""
    return (f"=== PERFIL ===\n{perfil}\n\n=== EJEMPLOS ETIQUETADOS POR EL USUARIO (APTA = postuló / NO_APTA = descartó) ===\n"
            f"{ejemplos or '(sin ejemplos)'}\n\n=== GIG ===\nFuente: {fuente}\n{pais_txt}Título: {titulo}\n\n"
            f"{texto[:4000]}\n\n=== RANGO ORIENTATIVO (solo si tipo=proyecto) ===\n{rango}\n\n"
            f"=== REGLAS ===\n{REGLAS_TRIAGE}\n\nResponde solo el JSON.")


def via_claude(perfil, titulo, texto, fuente="", excluir_id=None, pais=None):
    """Triage + redacción con `claude -p` (sin herramientas, 1 turno, timeout 120 s).
    None si DRY_RUN, claude ausente, timeout, error o salida no-JSON (→ fallback D13)."""
    if DRY_RUN:
        print(f"[dry-run] claude {titulo[:80]}", file=sys.stderr)
        return None
    if not claude_disponible():
        return None
    prompt = armar_prompt(perfil, titulo, texto, fuente, excluir_id, pais)
    try:
        r = subprocess.run(["claude", "-p", prompt, "--model", TRIAGE_MODEL,
                            "--output-format", "json", "--json-schema", json.dumps(TRIAGE_SCHEMA),
                            "--max-turns", "1", "--no-session-persistence",
                            "--disallowedTools", "Bash,Read,Edit,Write,WebFetch,WebSearch,Agent,Glob,Grep"],
                           timeout=120, capture_output=True, text=True)
    except Exception as e:
        print(f"[claude] error ({e}); uso la plantilla.", file=sys.stderr)
        return None
    out = parse_claude_output(r.stdout)
    if out is None:
        print(f"[claude] salida no-JSON (exit {r.returncode}): {(r.stderr or r.stdout)[:160]!r}", file=sys.stderr)
    return out


def registrar_triage(titulo, fuente, veredicto, motor):
    if DRY_RUN:
        return
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "titulo": titulo[:120], "fuente": fuente,
           "motor": motor, **({k: veredicto[k] for k in ("tipo", "apto", "motivo")} if veredicto else {})}
    with TRIAGE_FILE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pipeline_record(g, tipo, via, estado="entregado", motivo="", huecos=None, pais=None, contratista=None, puente=False):
    """Fila del registro (D21a + 007): TODO gig nuevo deja línea con estado y motivo. Pura.
    estado: entregado | entregado_plantilla | triage_no_apto | prefiltro | fallo_entrega."""
    return {"ts": datetime.now().isoformat(timespec="seconds"), "id": g.get("id", ""),
            "sig": f"{(g.get('company') or '').strip().lower()}|{(g.get('title') or '').strip().lower()}",
            "titulo": (g.get("title") or "")[:120], "url": g.get("url", ""), "fuente": g.get("source", ""),
            "tipo": tipo, "via": via, "estado": estado, "motivo": (motivo or "")[:200], "huecos": list(huecos or [])[:5],
            "pais_empleador": pais or "desconocido", "contratista_ok": bool(contratista), "puente": bool(puente)}


def log_pipeline(rec, path=None):
    """Append de una fila al .pipeline.jsonl. DRY_RUN=1 no escribe."""
    if DRY_RUN:
        print(f"[dry-run] pipeline {rec.get('estado', '')} {rec.get('motivo', '')[:30]} {rec.get('titulo', '')[:60]}", file=sys.stderr)
        dry = os.environ.get("PIPELINE_DRY_FILE", "").strip()   # 007: solo para gates; nunca el registro real
        if not dry:
            return False
        with Path(dry).open("a") as f:
            f.write(json.dumps({**rec, "dry_run": True}, ensure_ascii=False) + "\n")
        return False
    p = Path(path) if path else PIPELINE_FILE
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def pais_duro(countries, pais_llm="desconocido"):
    """011: el país del empleador sale del DATO DURO de la fuente (GetOnBrd `countries`) cuando existe; el LLM solo
    rellena si la fuente no lo trae. Pura."""
    paises = [str(x).strip() for x in (countries or []) if str(x).strip()]
    return paises[0] if paises else (pais_llm or "desconocido")


def redactar(perfil, titulo, texto, fuente="", pais=None):
    """Veredicto de triage + texto. dict {tipo, apto, motivo, texto, huecos, pais_empleador, contratista_ok} con motor
    claude, o None (DRY_RUN / claude no disponible / falló) para que el caller caiga a la plantilla (D13).
    pais (lista de la fuente) gana sobre pais_empleador del LLM."""
    v = via_claude(perfil, titulo, texto, fuente, pais=pais)
    if v is not None:
        v["pais_empleador"] = pais_duro(pais, v.get("pais_empleador"))
    registrar_triage(titulo, fuente, v, "claude" if v else "plantilla")
    return v


SKILLS = {"n8n": "n8n", "zapier": "Zapier", "make": "Make", "scrap": "web scraping",
          "whatsapp": "bots de WhatsApp", "bot": "bots", "api": "integración de APIs",
          "automat": "automatización de procesos", "rpa": "RPA", "webhook": "webhooks"}


def linea_modalidad(perfil, pais):
    """010 (A4): la línea 'Modalidad: …' del perfil, solo si el empleador NO es chileno. Pura."""
    if (pais or "").strip().lower() in ("chile", "cl"):
        return ""
    m = re.search(r"^modalidad:\s*(.+)$", perfil or "", re.I | re.M)
    return f"\n\nModalidad: {m.group(1).strip()}" if m else ""


def via_plantilla(perfil, titulo, texto, tipo="proyecto", pais="desconocido"):
    """Fallback sin LLM (D13). Español neutro, sin voseo. tipo='empleo' → carta sin precio;
    tipo='proyecto' → propuesta con el rango orientativo (LEY DEL PRECIO)."""
    t = (titulo + " " + texto).lower()
    matched = sorted({v for k, v in SKILLS.items() if k in t})
    skills_txt = ", ".join(matched) if matched else "automatización de procesos"
    nombre = re.search(r"cómo me presento:\s*(.+)", perfil or "")
    nombre = (nombre.group(1).strip().strip("<>") if nombre else "")
    saludo = f"Hola, soy {nombre}, me dedico a {skills_txt}." if nombre else f"Hola, me dedico a {skills_txt}."
    firma = f"\n\n— {nombre}" if nombre else ""
    if tipo == "empleo":
        return (f"{saludo}\n\n"
                f"Vi la oferta \"{titulo[:70]}\" y encaja con lo que hago: construyo sistemas completos y los dejo "
                f"funcionando en producción, con pruebas y documentación.\n\n"
                f"Trabajo con n8n, Python, Docker y agentes con LLM; mi código público está en "
                f"github.com/ISAACRICARDO2043 para que vean trabajo real.{linea_modalidad(perfil, pais)}\n\n"
                f"Quedo atento a una conversación. Español nativo.{firma}")
    rango = price_hint(titulo + " " + texto)
    return (f"{saludo}\n\n"
            f"Leí lo que necesita (\"{titulo[:70]}\") y es justo lo que hago: puedo armar una solución que le "
            f"quite ese trabajo manual de encima y le ahorre horas cada semana.\n\n"
            f"Trabajo rápido y muestro avances concretos, no promesas. Puedo empezar con una versión chica "
            f"funcionando para que vea resultados antes de seguir.\n\n"
            f"Como referencia, proyectos así suelen ir en {rango}.\n\n"
            f"¿Me cuenta un poco más del proceso actual y con qué herramientas trabaja hoy? "
            f"Así le paso un plan concreto y el precio cerrado.{firma}")


def log_interes(titulo, texto, arg):
    """Cada propuesta pedida = un 👍 implícito. Guardamos las keywords del gig para
    que buscar.py aprenda qué te interesa y suba al top los parecidos."""
    kws = [k for k in KW_VOCAB if k in (titulo + " " + texto).lower()]
    if DRY_RUN:
        print(f"[dry-run] interes {titulo[:80]}", file=sys.stderr)
        return kws
    rec = {"titulo": titulo[:120], "url": arg if arg.startswith("http") else "",
           "keywords": kws, "ts": datetime.now().isoformat(timespec="seconds")}
    try:
        data = json.loads(INTERESES.read_text()) if INTERESES.exists() else []
    except Exception:
        data = []
    data.append(rec)
    INTERESES.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return kws


def armar_request_n8n(titulo, url, propuesta, empresa="", fuente="", chat="", token=None):
    """Request al webhook (sin enviarlo). Header X-Gigs-Token solo si hay N8N_WEBHOOK_TOKEN (D5). Puro."""
    body = json.dumps({"titulo": titulo, "url": url, "propuesta": propuesta,
                       "empresa": empresa, "fuente": fuente, "chat_id": chat,
                       "ts": datetime.now().isoformat(timespec="seconds")}).encode()
    headers = {"Content-Type": "application/json"}
    tok = N8N_TOKEN if token is None else token
    if tok:
        headers["X-Gigs-Token"] = tok
    return urllib.request.Request(N8N_WEBHOOK or "http://127.0.0.1:5678/webhook/propuesta", data=body, headers=headers)


def send_to_n8n(titulo, url, propuesta, empresa="", fuente=""):
    """Manda la propuesta a la capa n8n (Telegram + aprobación + registro en Sheets).
    Devuelve False si no hay webhook o si n8n no respondió 2xx (ej. workflow inactivo)."""
    if not N8N_WEBHOOK:
        return False
    if DRY_RUN:
        print(f"[dry-run] n8n {titulo[:80]}", file=sys.stderr)
        return False
    modo, chat = destino(os.environ)                                     # D4: prod|test|dry (D22: el chat viaja en el body)
    if modo == "dry":
        print(f"[dry-run] n8n {titulo[:80]}", file=sys.stderr)
        return False
    req = armar_request_n8n(titulo, url, propuesta, empresa, fuente, chat)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"[n8n] webhook no disponible ({e}) → uso Telegram directo.", file=sys.stderr)
        return False


def send_to_telegram(titulo, url, propuesta):
    """Entrega directa a tu Telegram (sin n8n). Usa el bot del .env."""
    if not (TG_TOKEN and TG_CHAT):
        return False
    modo, chat = destino(os.environ)
    if modo == "dry":
        print(f"[dry-run] telegram {titulo[:80]}", file=sys.stderr)
        return False
    text = f"💼 *Propuesta lista*\n*{titulo}*\n{url}\n\n{propuesta}"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "parse_mode": "Markdown"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data), timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"[telegram] error: {e}", file=sys.stderr)
        return False


def deliver(titulo, url, propuesta, empresa="", fuente=""):
    """Entrega la propuesta: prueba n8n primero; si no está activo, Telegram directo."""
    if DRY_RUN:
        print(f"[dry-run] deliver {titulo[:80]}", file=sys.stderr)
        return "dry-run"
    if N8N_WEBHOOK and send_to_n8n(titulo, url, propuesta, empresa, fuente):
        return "n8n"
    if send_to_telegram(titulo, url, propuesta):
        return "telegram"
    return None


def main():
    arg = " ".join(sys.argv[1:]).strip()
    if not arg and not sys.stdin.isatty():
        arg = sys.stdin.read().strip()
    if not arg:
        print(__doc__)
        sys.exit(1)
    titulo, texto = gig_from_input(arg)
    perfil = load_perfil()
    kws = log_interes(titulo, texto, arg)
    v = redactar(perfil, titulo, texto)
    if v:
        motor = f"claude -p ({TRIAGE_MODEL})"
        print(f"\n# Propuesta para: {titulo}\n# (motor: {motor}) · tipo={v['tipo']} · apto={v['apto']} · {v['motivo']}\n")
        if not v["apto"]:
            print("# ⛔ descartada por triage: no se entrega.\n")
            return
        propuesta = v["texto"]
    else:
        motor = "plantilla" + ("" if claude_disponible() else " — claude -p no disponible en esta máquina")
        propuesta = via_plantilla(perfil, titulo, texto, tipo=(v or {}).get("tipo") or "proyecto")
        print(f"\n# Propuesta para: {titulo}\n# (motor: {motor})\n")
    print(propuesta)
    print(f"\n# 👍 interés registrado ({', '.join(kws) or 'sin keywords'}) → el buscador "
          f"subirá al top los gigs parecidos.")
    url = arg if arg.startswith("http") else ""
    via = deliver(titulo, url, propuesta)
    if via and via != "dry-run":
        log_pipeline(pipeline_record({"id": "", "title": titulo, "url": url, "source": "manual"},
                                     v["tipo"] if v else "plantilla", via))
    msg = {"n8n": "# 📤 enviada a n8n (Telegram + aprobación).",
           "telegram": "# 📤 enviada a tu Telegram (directo).",
           "dry-run": "# [dry-run] no se envió nada (DRY_RUN=1).",
           None: "# 📄 solo en terminal (configurá Telegram o n8n para recibirla)."}[via]
    print(msg)
    print()


if __name__ == "__main__":
    main()
