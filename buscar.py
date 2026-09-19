#!/usr/bin/env python3
"""buscador-gigs — gigs de automatización COBRABLES desde Chile (worldwide/LATAM, USD/EUR).
Fuentes: Remotive, RemoteOK, WeWorkRemotely. Filtra por skills + ubicación, dedup, avisa por Telegram.

Config por env (.env):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> avisos (opcional para probar)
  GIG_KEYWORDS_EXTRA                    -> señales fuertes extra (coma-separado)
"""
import os, sys, json, html, re, time, socket, urllib.request, urllib.parse
from pathlib import Path
import proponer  # auto-redactar y entregar propuestas de los mejores gigs
import filtros   # prefiltro determinista (007)

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / ".seen.json"
INTERESES_FILE = HERE / ".intereses.json"   # lo escribe proponer.py (👍 implícito)
UA = "gig-finder/1.0 (personal job search)"

from filtros import STRONG, WEAK, _has, prefiltro  # 007: keywords y prefiltro viven en filtros.py

# Bloquear lo PRESENCIAL/híbrido (el usuario trabaja 100% remoto desde LATAM; ver GEO_OK).
ONSITE_BLOCK = ["on-site", "on site", "onsite", "in-office", "in office", "in the office",
                "hybrid role", "hybrid position", "hybrid work", "hybrid schedule",
                "must relocate", "relocation required", "willing to relocate",
                "trabajo presencial", "modalidad presencial", "presencial"]

# Detectar gigs que EXIGEN inglés (el usuario no lo habla con fluidez → marcarlos ⚠️).
ENGLISH_REQUIRED = ["english required", "fluent english", "english is a must",
                    "advanced english", "english proficiency", "must speak english",
                    "inglés indispensable", "ingles indispensable", "inglés avanzado",
                    "ingles avanzado", "nivel de inglés", "nivel de ingles",
                    "english b2", "english c1", "proficient in english",
                    # 005 (medido: 7/123 gigs sin marcar con estas frases, todos GetOnBrd)
                    "requires applying in english", "applying in english", "apply in english",
                    "c1 english", "b2 english", "inglés b2", "ingles b2", "inglés c1", "ingles c1",
                    "fluent in english", "professional english", "english fluency",
                    "advanced level of english"]


def elegible_auto(g):
    """D24: un gig que EXIGE inglés y no es LATAM/español-friendly no consume cupo de
    auto-propuesta (va como aviso compacto con ⚠️)."""
    return not (g.get("en") and not g.get("esp"))
# Señales de que el gig acepta español / es LATAM-friendly.
SPANISH_FRIENDLY = ["español", "spanish", "latam", "latin america", "latinoamérica",
                    "latinoamerica", "bilingual", "bilingüe", "hispano"]

# Geo: ACEPTAR remoto worldwide/LATAM/Chile; BLOQUEAR restricción a países no-LATAM.
GEO_OK = ["worldwide", "anywhere", "global", "latam", "latin america", "americas",
          "south america", "north and south america", "chile", "remote, latam"]
GEO_BLOCK = ["usa only", "us only", "u.s. only", "us-only", "united states only",
             "us based", "based in the us", "must be located in the u", "emea only", "emea based",
             "europe only", "european union", "uk only", "united kingdom only",
             "canada only", "india only", "brazil only", "brasil only", "germany only",
             "france only", "australia only", "philippines only", "spain only",
             "mexico only", "argentina only", "colombia only", "usa-only"]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")  # LEY DEL CANAL REAL
HOST = socket.gethostname()


def geo_status(loc, title="", desc=""):
    """El TÍTULO manda: si restringe a un país no-LATAM ('USA Only') bloquea aunque
    el campo de ubicación diga 'Anywhere'. También bloquea lo presencial/híbrido
    (solo 100% remoto). Luego ubicación worldwide/LATAM = ok."""
    title_l = (title or "").lower()
    desc_l = (desc or "").lower()
    if any(k in title_l for k in GEO_BLOCK):
        return "block"
    if any(k in (title_l + " " + desc_l) for k in ONSITE_BLOCK):  # no presencial/híbrido
        return "block"
    loc_l = (loc or "").lower()
    if any(k in loc_l for k in GEO_OK):
        return "ok"
    if any(k in (loc_l + " " + desc_l) for k in GEO_BLOCK):
        return "block"
    return "unknown"


TRANSLATE = os.environ.get("TRANSLATE", "1").strip() not in ("0", "false", "no", "")
_TR_CACHE = {}


def translate_es(text):
    """Traduce a español usando el endpoint público de Google (sin API key).
    sl=auto → si ya está en español, queda igual. Si falla, devuelve el original."""
    text = (text or "").strip()
    if not text or not TRANSLATE:
        return text
    if text in _TR_CACHE:
        return _TR_CACHE[text]
    try:
        q = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": text})
        req = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + q,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        _TR_CACHE[text] = out or text
        return _TR_CACHE[text]
    except Exception as e:
        print(f"[translate] error: {e}", file=sys.stderr)
        return text


def needs_english(text):
    return any(k in (text or "").lower() for k in ENGLISH_REQUIRED)


def spanish_friendly(text, source=""):
    if source == "GetOnBrd":  # board chileno, gigs en español por defecto
        return True
    return any(k in (text or "").lower() for k in SPANISH_FRIENDLY)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def find_hits(text):
    t = (text or "").lower()
    return [k for k in STRONG if _has(k, t)], [k for k in WEAK if _has(k, t)]


def qualifies(s, w):
    return len(s) >= 1 or len(w) >= 2


def gig(source, jid, title, company, url, location, hits, geo_desc=""):
    blob = f"{title} {geo_desc} {location}"
    return {"id": f"{source}:{jid}", "title": title, "company": company, "url": url,
            "source": source, "location": (location or "?").strip() or "?",
            "geo": geo_status(location, title, geo_desc), "hits": hits,
            "desc": (geo_desc or "")[:4000],  # para auto-redactar la propuesta
            "esp": spanish_friendly(blob, source), "en": needs_english(blob),
            "lang": "",  # idioma del aviso si la fuente lo da (GetOnBrd); "" = heurística en filtros
            "countries": [], "modality": ""}  # 010: país/modalidad duros de la fuente (GetOnBrd) para contrato_cl


def sig_of(g):
    """Firma estable por (empresa, título) para deduplicar la misma vacante
    publicada con distintas ubicaciones (ej. 360Dialog x3, Nabu Casa x5)."""
    c = re.sub(r"\s+", " ", (g["company"] or "").strip().lower())
    t = re.sub(r"\s+", " ", (g["title"] or "").strip().lower())
    return f"{c}|{t}"


def from_remotive():
    try:
        data = fetch_json("https://remotive.com/api/remote-jobs?search=automation&limit=80")
        out = []
        for j in data.get("jobs", []):
            blob = f"{j.get('title','')} {j.get('category','')} {j.get('description','')}"
            s, w = find_hits(blob)
            if not qualifies(s, w):
                continue
            g = gig("Remotive", j.get("id"), j.get("title"), j.get("company_name"),
                    j.get("url"), j.get("candidate_required_location", ""), s + w,
                    geo_desc=j.get("description", ""))
            if g["geo"] != "block":
                out.append(g)
        return out
    except Exception as e:
        print(f"[remotive] error: {e}", file=sys.stderr)
        return []


def from_remoteok():
    try:
        data = [j for j in fetch_json("https://remoteok.com/api") if isinstance(j, dict) and "id" in j]
        out = []
        for j in data:
            s, w = find_hits(f"{j.get('position','')} {' '.join(j.get('tags',[]))} {j.get('description','')}")
            if not qualifies(s, w):
                continue
            g = gig("RemoteOK", j.get("id"), j.get("position"), j.get("company"),
                    j.get("url"), j.get("location", ""), s + w,
                    geo_desc=j.get("description", ""))
            if g["geo"] != "block":
                out.append(g)
        return out
    except Exception as e:
        print(f"[remoteok] error: {e}", file=sys.stderr)
        return []


def _rss_items(xml):
    """Parseo mínimo de RSS con regex (sin parser XML → sin XXE/billion-laughs)."""
    for m in re.finditer(r"<item\b[^>]*>(.*?)</item>", xml, re.S | re.I):
        block = m.group(1)
        def field(tag):
            mm = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.S | re.I)
            if not mm:
                return ""
            v = mm.group(1).strip()
            cd = re.match(r"<!\[CDATA\[(.*?)\]\]>\s*$", v, re.S)
            return (cd.group(1) if cd else v).strip()
        yield field("title"), field("link"), field("description"), field("region")


def from_wwr():
    try:
        xml = fetch_text("https://weworkremotely.com/categories/remote-programming-jobs.rss")
        out = []
        for title, link, desc, region in _rss_items(xml):
            if not title:
                continue
            s, w = find_hits(f"{title} {desc}")
            if not qualifies(s, w):
                continue
            company, _, role = title.partition(":")
            loc = region or (" ".join(re.findall(r"(worldwide|anywhere|latin america|latam|americas|usa only|us only|europe|brazil|uk)", (title + " " + desc).lower())) or "")
            g = gig("WeWorkRemotely", link or title, (role.strip() or title), company.strip(), link, loc, s + w,
                    geo_desc=desc)
            if g["geo"] != "block":
                out.append(g)
        return out
    except Exception as e:
        print(f"[wwr] error: {e}", file=sys.stderr)
        return []


# GetOnBrd: board chileno, gigs en español. Buscamos por términos de automatización.
# 010 (A3): + roles puente
GETONBRD_QUERIES_PUENTE = ["soporte técnico", "implementación", "onboarding", "qa manual", "analista de datos", "help desk", "customer success"]
GETONBRD_QUERIES = ["automatizacion", "n8n", "zapier", "make.com", "scraping",
                    "chatbot", "whatsapp", "integracion api", "rpa", "no-code"]


def from_getonbrd():
    out, local_seen = [], set()
    for q in GETONBRD_QUERIES + GETONBRD_QUERIES_PUENTE:
        try:
            url = "https://www.getonbrd.com/api/v0/search/jobs?" + urllib.parse.urlencode(
                {"query": q, "per_page": 20})
            data = fetch_json(url)
        except Exception as e:
            print(f"[getonbrd:{q}] error: {e}", file=sys.stderr)
            continue
        for j in data.get("data", []):
            jid = j.get("id")
            if not jid or jid in local_seen:
                continue
            local_seen.add(jid)
            a = j.get("attributes", {})
            url_pub = (j.get("links") or {}).get("public_url", "")
            modality = a.get("remote_modality") or ("" if a.get("remote") else "no_remote")
            santiago = url_pub.rstrip("/").endswith("-santiago")   # H5 (007): presencial/híbrido EN Santiago sí sirve
            if (modality in ("hybrid", "no_remote") or not a.get("remote")) and not santiago:
                continue  # presencial/híbrido fuera de Santiago: no
            title = a.get("title", "")
            # 005: los requisitos (C1 English…) viven en functions/desirable, no solo en description
            desc = re.sub(r"<[^>]+>", " ", " ".join((a.get(k) or "") for k in ("description", "functions", "desirable")))
            s, w = find_hits(f"{title} {desc} {q}")
            # GetOnBrd ya filtró por la query → se acepta aunque find_hits no matchee; el prefiltro (sin_hit) lo mide después (H3)
            loc = {"fully_remote": "100% remoto", "remote_local": "remoto (local)", "hybrid": "híbrido",
                   "remote_zone": "remoto (zona)", "no_remote": "presencial"}.get(modality, "remoto" if a.get("remote") else "?")
            if santiago:
                loc += " · Santiago"
            g = gig("GetOnBrd", jid, title, "", url_pub, loc, (s + w) or [q], geo_desc=desc)
            g["lang"] = "" if a.get("lang") in (None, "", "lang_not_specified") else a.get("lang")
            g["countries"] = list(a.get("countries") or [])          # 010 (A2): dato duro para contrato_cl
            g["modality"] = modality
            if a.get("lang") == "en":  # 005: aviso publicado en inglés = "Requires applying in English" (badge de la UI)
                g["en"], g["esp"] = True, False
            # board LATAM/español: si la geo no se reconoce, tratarla como ok (no bloqueado)
            if g["geo"] == "unknown" or (santiago and modality in ("hybrid", "no_remote")):
                g["geo"] = "ok"
            if g["geo"] != "block":
                out.append(g)
    return out


JOBICY_TAGS = ["automation", "n8n", "scraping", "chatbot", "api", "no-code", "rpa",
               "technical-support", "customer-success", "qa", "data-analyst"]   # 010 (A3): puente


def from_jobicy():
    """Jobicy: board 100% remoto. jobGeo ES la restricción de región (Anywhere/LATAM/USA…)."""
    out, local = [], set()
    for tag in JOBICY_TAGS:
        try:
            data = fetch_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}")
        except Exception as e:
            print(f"[jobicy:{tag}] error: {e}", file=sys.stderr)
            continue
        for j in data.get("jobs", []):
            jid = j.get("id")
            if not jid or jid in local:
                continue
            local.add(jid)
            title = j.get("jobTitle", "")
            desc = re.sub(r"<[^>]+>", " ", j.get("jobDescription", "") or j.get("jobExcerpt", "") or "")
            s, w = find_hits(f"{title} {desc} {tag}")
            geo = j.get("jobGeo", "") or ""
            g = gig("Jobicy", jid, title, j.get("companyName", ""), j.get("url", ""),
                    geo, (s + w) or [tag], geo_desc=desc)
            # jobGeo restringe la región: si no es worldwide/LATAM, fuera (salvo que ya sea ok)
            if g["geo"] != "ok" and not any(k in geo.lower() for k in GEO_OK):
                g["geo"] = "block"
            if g["geo"] != "block":
                out.append(g)
    return out


def from_himalayas():
    """Himalayas: board remoto. locationRestrictions ES la restricción (lista de países)."""
    try:
        data = fetch_json("https://himalayas.app/jobs/api?limit=100")
    except Exception as e:
        print(f"[himalayas] error: {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = re.sub(r"<[^>]+>", " ", j.get("description", "") or j.get("excerpt", "") or "")
        s, w = find_hits(f"{title} {desc}")
        if not qualifies(s, w):
            continue
        locs = j.get("locationRestrictions") or []
        loc_str = ", ".join(locs) if locs else "Worldwide"
        url = j.get("guid") or j.get("applicationLink", "")
        g = gig("Himalayas", url or title, title, j.get("companyName", ""), url, loc_str, s + w, geo_desc=desc)
        # si hay restricción de país y ninguna es worldwide/LATAM, fuera
        if locs and not any(any(k in l.lower() for k in GEO_OK) for l in locs):
            g["geo"] = "block"
        if g["geo"] != "block":
            out.append(g)
    return out


def load_seen():
    """Devuelve (ids, sigs). Acepta el formato viejo (lista de ids) y el nuevo
    ({"ids":[...], "sigs":[...]}) para no re-alertar lo ya visto al migrar."""
    try:
        data = json.loads(SEEN_FILE.read_text())
    except Exception:
        return set(), set()
    if isinstance(data, dict):
        return set(data.get("ids", [])), set(data.get("sigs", []))
    return set(data), set()


def tg_send(text):
    if not TG_TOKEN:
        return False
    modo, chat = proponer.destino(os.environ)   # D4 (008): prod|test|dry
    if modo == "dry" or not chat:
        print(f"[dry-run] telegram {text.splitlines()[0][:80]}", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"[telegram] error: {e}", file=sys.stderr)
        return False


def proponer_gig(g, perfil):
    """Triage + entrega de UN gig. Devuelve 'entregado' | 'descartado' | 'plantilla' | 'fallo'.
    apto=false → no se entrega. redactar()=None (claude ausente/falló/DRY_RUN) → plantilla (D13)."""
    v = proponer.redactar(perfil, g["title"], g.get("desc", ""), g["source"], pais=g.get("countries") or None)
    if v is not None and not v["apto"]:
        proponer.log_pipeline(proponer.pipeline_record(g, v["tipo"], "", estado="triage_no_apto", motivo=v["motivo"], huecos=v.get("huecos"),
                                                       pais=v.get("pais_empleador"), contratista=v.get("contratista_ok"), puente=filtros.es_puente(g["title"])))
        return "descartado"
    if v is not None:
        texto, res = v["texto"], "entregado"
    else:
        pais_g = (g.get("countries") or [""])[0] or "desconocido"
        texto, res = proponer.via_plantilla(perfil, g["title"], g.get("desc", ""), tipo="empleo" if g["source"] != "prueba" else "proyecto", pais=pais_g), "plantilla"
    via = proponer.deliver(g["title"], g["url"], texto, g["company"], g["source"])
    if not via:
        proponer.log_pipeline(proponer.pipeline_record(g, v["tipo"] if v else "plantilla", "", estado="fallo_entrega", motivo="deliver=False"))
        return "fallo"
    proponer.log_pipeline(proponer.pipeline_record(g, v["tipo"] if v else "plantilla", via,
                                                   estado="entregado" if v else "entregado_plantilla", motivo=(v or {}).get("motivo", ""),
                                                   huecos=(v or {}).get("huecos"), pais=(v or {}).get("pais_empleador"),
                                                   contratista=(v or {}).get("contratista_ok"), puente=filtros.es_puente(g["title"])))
    return res


def interest_weights():
    """Aprende de los gigs que te interesaron (proponer.py): cuenta cuántas veces
    apareció cada keyword. Devuelve {keyword: peso}. Vacío si todavía no hay señal."""
    try:
        data = json.loads(INTERESES_FILE.read_text())
    except Exception:
        return {}
    w = {}
    for r in data:
        for k in r.get("keywords", []):
            w[k] = w.get(k, 0) + 1
    return w


def main():
    gigs = (from_remotive() + from_remoteok() + from_wwr() + from_getonbrd()
            + from_jobicy() + from_himalayas())
    weights = interest_weights()
    for g in gigs:  # score de afinidad con lo que te interesó antes
        g["afin"] = sum(weights.get(h, 0) for h in g["hits"])
    # prioridad: acepta español > no exige inglés > afín a tus intereses > geo > señal fuerte
    gigs.sort(key=lambda g: (-g["esp"], g["en"], -g["afin"], -(g["geo"] == "ok"),
                             -sum(1 for h in g["hits"] if h in STRONG)))
    seen_ids, seen_sigs = load_seen()
    new, sig_de = [], {}
    for g in gigs:
        if g["id"] in seen_ids:
            continue
        sig = sig_of(g)
        if sig in seen_sigs:
            seen_ids.add(g["id"])  # misma vacante ya vista en otra ubicación: marcar, no alertar
            continue
        new.append(g)
        seen_sigs.add(sig)
        sig_de[g["id"]] = sig
    triage_max = int(os.environ.get("TRIAGE_MAX", "15") or "15")   # 007: tope de llamadas a claude -p por corrida
    perfil = proponer.load_perfil()
    print(f"{len(gigs)} gigs cobrables (worldwide/LATAM/Chile), {len(new)} nuevos — prefiltro + triage (máx {triage_max}).\n")
    aptos = descartados = prefiltrados = pendientes = 0
    por_motivo = {}
    aviso_claude = False
    triados = 0
    try:
        for g in new:
            ok, motivo = prefiltro(g)                       # 007 opción A: determinista antes del LLM
            if not ok:
                prefiltrados += 1
                por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
                proponer.log_pipeline(proponer.pipeline_record(g, "", "", estado="prefiltro", motivo=motivo, puente=filtros.es_puente(g["title"]),
                                                               pais=(g.get("countries") or [""])[0] or ""))
                seen_ids.add(g["id"])
                continue
            if triados >= triage_max:
                pendientes += 1                              # excede el tope: NO se marca visto, entra en la próxima corrida
                seen_sigs.discard(sig_de[g["id"]])
                continue
            triados += 1
            try:
                print(f"💼 {g['title']} — {g['company'] or g['source']} | {g['location']} | {', '.join(g['hits'][:4])}\n   {g['url']}")
            except BrokenPipeError:
                pass
            res = proponer_gig(g, perfil)
            if res == "entregado":
                aptos += 1
            elif res == "descartado":
                descartados += 1
            elif res == "plantilla" and not proponer.claude_disponible() and not aviso_claude:
                aviso_claude = True   # D13: un aviso por corrida
                tg_send(f"⚠️ claude -p no disponible en {HOST}: renová el login (propuestas por plantilla).")
            seen_ids.add(g["id"])
            time.sleep(1)
    finally:
        # guardar SIEMPRE el estado, aunque falle stdout (BrokenPipe) o un envío — salvo en DRY_RUN
        if DRY_RUN:
            print(f"[dry-run] .seen.json intacto ({len(new)} nuevos no marcados)", file=sys.stderr)
        else:
            SEEN_FILE.write_text(json.dumps({"ids": sorted(seen_ids), "sigs": sorted(seen_sigs)}))
    resumen_motivos = ", ".join(f"{k}={v}" for k, v in sorted(por_motivo.items())) or "-"
    print(f"\n📤 {aptos} apto / {descartados} descartados por triage / {prefiltrados} por prefiltro ({resumen_motivos})"
          + (f" / {pendientes} quedan para la próxima corrida" if pendientes else "")
          + (" — [dry-run] NO enviada(s)." if DRY_RUN else " — aptos enviados para aprobar."))
    if not (TG_TOKEN and TG_CHAT) and not proponer.N8N_WEBHOOK:
        print("(Telegram/n8n OFF — configurá TELEGRAM_* o N8N_WEBHOOK_URL en el .env)")


if __name__ == "__main__":
    main()
