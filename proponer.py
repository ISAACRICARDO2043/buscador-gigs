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
GIGS_TEST = os.environ.get("GIGS_TEST", "").strip().lower() in ("1", "true", "yes")
N8N_WEBHOOK = os.environ.get("N8N_WEBHOOK_URL", "").strip()  # capa de entrega/aprobación
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()  # entrega directa (fallback de n8n)
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TG_CHAT_TEST = os.environ.get("TELEGRAM_CHAT_ID_TEST", "").strip()
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
                                "texto": {"type": "string"}},
                 "required": ["tipo", "apto", "motivo", "texto"]}

REGLAS_TRIAGE = (
    "Sos el asistente de un freelancer de automatización. Te paso SU PERFIL y UN GIG. "
    "Devolvé SOLO un JSON con: tipo ('empleo' si es un puesto/vacante, 'proyecto' si es un encargo "
    "puntual), apto (true/false), motivo (1 línea) y texto.\n"
    "apto=false si: exige inglés fluido/avanzado/C1/B2 o postular en inglés; seniority Senior, Lead, "
    "Manager o QA; stack ajeno al perfil (Salesforce, Dynamics, Outsystems, SAP, ML/data science, "
    "iOS/Android nativo, .NET/Java/C++ puro); presencial/híbrido; o nada que ver con automatización, "
    "integraciones, bots, scraping o agentes con LLM. En ese caso texto=''.\n"
    "apto=true → texto en español neutro, tuteo, sin encabezados ni notas:\n"
    "  - empleo: carta de EXACTAMENTE 6 líneas: saludo+quién soy y ciudad; por qué encaja (la skill "
    "exacta que piden y dónde la usé según el perfil); UN caso concreto del perfil con resultado; "
    "stack y forma de trabajar; GitHub github.com/ISAACRICARDO2043; cierre ('Quedo atento a una "
    "conversación. Español nativo; inglés básico.'). SIN precio.\n"
    "  - proyecto: propuesta de 6 a 9 líneas al cliente (no sabés su nombre), foco en el resultado, "
    "cierra con una pregunta concreta, y menciona UNA vez el RANGO ORIENTATIVO tal cual, aclarando "
    "que el precio se cierra al conocer el alcance. PROHIBIDO un número único o distinto al rango.\n"
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
    return {"tipo": obj["tipo"], "apto": obj["apto"], "motivo": str(obj["motivo"]),
            "texto": str(obj["texto"])}


def via_claude(perfil, titulo, texto, fuente=""):
    """Triage + redacción con `claude -p` (sin herramientas, 1 turno, timeout 120 s).
    None si DRY_RUN, claude ausente, timeout, error o salida no-JSON (→ fallback D13)."""
    if DRY_RUN:
        print(f"[dry-run] claude {titulo[:80]}", file=sys.stderr)
        return None
    if not claude_disponible():
        return None
    rango = price_hint(titulo + " " + texto)
    prompt = (f"=== PERFIL ===\n{perfil}\n\n=== GIG ===\nFuente: {fuente}\nTítulo: {titulo}\n\n"
              f"{texto[:4000]}\n\n=== RANGO ORIENTATIVO (solo si tipo=proyecto) ===\n{rango}\n\n"
              f"=== REGLAS ===\n{REGLAS_TRIAGE}\n\nRespondé solo el JSON.")
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


def pipeline_record(g, tipo, via):
    """Fila del registro de entregas (D21a): qué propuesta salió, por dónde. Pura."""
    return {"ts": datetime.now().isoformat(timespec="seconds"), "id": g.get("id", ""),
            "sig": f"{(g.get('company') or '').strip().lower()}|{(g.get('title') or '').strip().lower()}",
            "titulo": (g.get("title") or "")[:120], "url": g.get("url", ""), "fuente": g.get("source", ""),
            "tipo": tipo, "via": via}


def log_pipeline(rec, path=None):
    """Append de una fila al .pipeline.jsonl. DRY_RUN=1 no escribe."""
    if DRY_RUN:
        print(f"[dry-run] pipeline {rec.get('titulo', '')[:80]}", file=sys.stderr)
        return False
    p = Path(path) if path else PIPELINE_FILE
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def redactar(perfil, titulo, texto, fuente=""):
    """Veredicto de triage + texto. dict {tipo, apto, motivo, texto} con motor claude, o None
    (DRY_RUN / claude no disponible / falló) para que el caller caiga a la plantilla (D13)."""
    v = via_claude(perfil, titulo, texto, fuente)
    registrar_triage(titulo, fuente, v, "claude" if v else "plantilla")
    return v


SKILLS = {"n8n": "n8n", "zapier": "Zapier", "make": "Make", "scrap": "web scraping",
          "whatsapp": "bots de WhatsApp", "bot": "bots", "api": "integración de APIs",
          "automat": "automatización de procesos", "rpa": "RPA", "webhook": "webhooks"}


def via_plantilla(perfil, titulo, texto):
    t = (titulo + " " + texto).lower()
    matched = sorted({v for k, v in SKILLS.items() if k in t})
    skills_txt = ", ".join(matched) if matched else "automatización de procesos"
    nombre = re.search(r"cómo me presento:\s*(.+)", perfil or "")
    nombre = (nombre.group(1).strip().strip("<>") if nombre else "")
    rango = price_hint(titulo + " " + texto)
    saludo = f"¡Hola! Soy {nombre}, me dedico a {skills_txt}." if nombre else f"¡Hola! Me dedico a {skills_txt}."
    firma = f"\n\n— {nombre}" if nombre else ""
    return (f"{saludo}\n\n"
            f"Leí lo que necesitás (\"{titulo[:70]}\") y es justo lo que hago: te puedo armar "
            f"una solución que te saque ese trabajo manual de encima y te ahorre horas cada semana.\n\n"
            f"Trabajo rápido y te muestro avances concretos, no promesas. Puedo arrancar con una "
            f"versión funcionando chica para que veas resultado antes de seguir.\n\n"
            f"Como referencia, proyectos así suelen ir en {rango}.\n\n"
            f"¿Me contás un poco más del proceso actual y con qué herramientas trabajás hoy? "
            f"Así te paso un plan concreto y el precio cerrado.{firma}")


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


def send_to_n8n(titulo, url, propuesta, empresa="", fuente=""):
    """Manda la propuesta a la capa n8n (Telegram + aprobación + registro en Sheets).
    Devuelve False si no hay webhook o si n8n no respondió 2xx (ej. workflow inactivo)."""
    if not N8N_WEBHOOK:
        return False
    if DRY_RUN:
        print(f"[dry-run] n8n {titulo[:80]}", file=sys.stderr)
        return False
    chat = TG_CHAT_TEST if (GIGS_TEST and TG_CHAT_TEST) else TG_CHAT   # D22: el chat viaja en el body, nunca en el workflow
    body = json.dumps({"titulo": titulo, "url": url, "propuesta": propuesta,
                       "empresa": empresa, "fuente": fuente, "chat_id": chat,
                       "ts": datetime.now().isoformat(timespec="seconds")}).encode()
    req = urllib.request.Request(N8N_WEBHOOK, data=body,
                                 headers={"Content-Type": "application/json"})
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
    if DRY_RUN:
        print(f"[dry-run] telegram {titulo[:80]}", file=sys.stderr)
        return False
    text = f"💼 *Propuesta lista*\n*{titulo}*\n{url}\n\n{propuesta}"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text,
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
        propuesta = via_plantilla(perfil, titulo, texto)
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
