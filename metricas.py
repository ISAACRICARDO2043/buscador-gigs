#!/usr/bin/env python3
"""metricas.py — métrica de negocio del radar (005, D21). Solo stdlib.

  python3 metricas.py                 # enviadas por semana desde .pipeline.jsonl (o PIPELINE_FILE)
  N8N_API_URL=… N8N_API_KEY=… python3 metricas.py   # + aprobadas/descartadas/vencidas desde la Data Table gigs_pipeline
  M700_SSH=user@host python3 metricas.py            # idem, leyendo la Data Table vía ssh; en el host la key se toma
                                                    # de N8N_ENV_FILE (default ~/.config/n8n-api.env, línea N8N_API_KEY=)
Nunca imprime la key. Exit 0 siempre que el jsonl sea legible.
"""
import json, os, subprocess, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = Path(os.environ.get("PIPELINE_FILE") or (HERE / ".pipeline.jsonl"))


def semana(ts):
    y, w, _ = datetime.fromisoformat(ts[:19]).isocalendar()
    return f"{y}-W{w:02d}"


def enviadas_por_semana(path=PIPELINE):
    """{semana: {"enviadas": n, "n8n": n, "telegram": n}} desde el jsonl. Pura salvo la lectura."""
    out = defaultdict(Counter)
    if not Path(path).exists():
        return {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        s = semana(r["ts"])
        out[s]["nuevos"] += 1
        if (r.get("motivo") or "").startswith("contrato_cl"):
            out[s]["contrato_cl"] += 1                                                    # 010 (A6)
        if r.get("puente") and (r.get("estado") or "entregado").startswith("entregado"):
            out[s]["puente"] += 1
        if not (r.get("estado") or "entregado").startswith("entregado"):   # 007+: el pipeline también registra prefiltro/no aptos
            continue
        out[s]["enviadas"] += 1
        out[s][r.get("via", "?")] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def decisiones_por_semana(rows):
    """{semana: Counter(decision)} desde las filas de la Data Table (campos decision, ts). Pura."""
    out = defaultdict(Counter)
    for r in rows:
        ts = r.get("ts") or r.get("createdAt") or ""
        if not ts:
            continue
        out[semana(ts)][r.get("decision", "?")] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def contar_huecos(filas, hoy, dias=30, umbral=3):
    """{herramienta: n} de las filas entregado*/triage_no_apto de los últimos `dias` (D3, 008). Pura.
    Filas sin campo `huecos` cuentan 0. `hoy` = 'YYYY-MM-DD'."""
    from datetime import date, timedelta
    desde = (date.fromisoformat(hoy) - timedelta(days=dias)).isoformat()
    c = Counter()
    for r in filas:
        if not (r.get("estado") or "entregado").startswith(("entregado", "triage_no_apto")):
            continue
        if (r.get("ts") or "")[:10] < desde:
            continue
        for h in r.get("huecos") or []:
            if isinstance(h, str) and h.strip():
                c[h.strip().lower()] += 1
    return {k: v for k, v in c.most_common()}


def huecos_informe(path=PIPELINE, hoy=None, dias=30, umbral=3):
    hoy = hoy or datetime.now().strftime("%Y-%m-%d")
    filas = []
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:
                filas.append(json.loads(line))
            except Exception:
                pass
    c = contar_huecos(filas, hoy, dias, umbral)
    print(f"# huecos (herramientas pedidas que no están en el perfil, últimos {dias} días)")
    if not c:
        print("  (sin huecos registrados)")
    for k, v in c.items():
        print(f"  {k}: {v}" + ("  → candidato a repo" if v >= umbral else ""))
    return c


WF_GIGS = os.environ.get("N8N_WORKFLOW_ID", "8QVZrP0UuDCNA13b")   # id del workflow de propuestas (no es secreto)


def contar_pendientes(ejecuciones, ahora=None):
    """Ejecuciones `waiting` del workflow → {"n": N, "items": [{id, titulo, horas}]} ordenadas por antigüedad. Pura.
    ejecuciones: lista de dicts {id, startedAt (ISO), titulo (opcional)}. None → s/d (API no disponible)."""
    if ejecuciones is None:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    items = []
    for e in ejecuciones:
        try:
            t0 = datetime.fromisoformat(str(e.get("startedAt", "")).replace("Z", "+00:00"))
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            horas = round((ahora - t0).total_seconds() / 3600, 1)
        except Exception:
            horas = None
        items.append({"id": str(e.get("id", "")), "titulo": (e.get("titulo") or "(sin título)")[:80], "horas": horas})
    items.sort(key=lambda x: -(x["horas"] or 0))
    return {"n": len(items), "items": items}


def _pendientes_api(base, key):
    """GET executions waiting del workflow + título desde el body del webhook (includeData). None si la API no responde."""
    h = {"X-N8N-API-KEY": key}
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/api/v1/executions?status=waiting&workflowId={WF_GIGS}&limit=100", headers=h), timeout=20))
    except Exception:
        return None
    out = []
    for e in d.get("data", []):
        titulo = ""
        try:
            full = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/api/v1/executions/{e['id']}?includeData=true", headers=h), timeout=20))
            run = (full.get("data") or {}).get("resultData", {}).get("runData", {})
            titulo = run["Recibe propuesta"][0]["data"]["main"][0][0]["json"]["body"].get("titulo", "")
        except Exception:
            pass
        out.append({"id": e["id"], "startedAt": e.get("startedAt", ""), "titulo": titulo})
    return out


def _pendientes_ssh(host):
    envf = os.environ.get("N8N_ENV_FILE", "~/.config/n8n-api.env")
    cmd = (f"set -a; . {envf}; set +a; N8N_API_URL=http://127.0.0.1:5678 python3 - <<'PY'\n"
           "import json,os,sys; sys.path.insert(0, os.path.expanduser('~/proyectos-cliente/buscador-gigs')); import metricas\n"
           "print(json.dumps(metricas._pendientes_api(os.environ['N8N_API_URL'], os.environ['N8N_API_KEY'])))\nPY")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, cmd], capture_output=True, text=True, timeout=90)
        return json.loads(r.stdout or "null")
    except Exception:
        return None


def pendientes():
    """Pendientes vía API (N8N_API_*) o ssh (M700_SSH); None = s/d."""
    if os.environ.get("N8N_API_URL") and os.environ.get("N8N_API_KEY"):
        return contar_pendientes(_pendientes_api(os.environ["N8N_API_URL"].rstrip("/"), os.environ["N8N_API_KEY"]))
    if os.environ.get("M700_SSH"):
        return contar_pendientes(_pendientes_ssh(os.environ["M700_SSH"]))
    return None


def pendientes_informe(p=None):
    p = pendientes() if p is None else p
    print("# pendientes de tu tap (ejecuciones waiting del workflow)")
    if p is None:
        print("  pendientes: s/d (sin acceso a la API de n8n)")
        return
    print(f"  pendientes: {p['n']}")
    for it in p["items"][:10]:
        print(f"  - {it['titulo']} · {it['horas']} h")


def _rows_api(base, key):
    h = {"X-N8N-API-KEY": key}
    tabs = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/api/v1/data-tables?limit=250", headers=h), timeout=20))
    tid = next((t["id"] for t in tabs.get("data", []) if t["name"] == "gigs_pipeline"), None)
    if not tid:
        return []
    rows, cursor = [], None
    while True:
        url = f"{base}/api/v1/data-tables/{tid}/rows?limit=250" + (f"&cursor={cursor}" if cursor else "")
        d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20))
        rows += d.get("data", [])
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return rows


def _rows_ssh(host):
    envf = os.environ.get("N8N_ENV_FILE", "~/.config/n8n-api.env")
    cmd = (f"set -a; . {envf}; set +a; python3 - <<'PY'\n"
           "import json,os,urllib.request\nh={'X-N8N-API-KEY':os.environ['N8N_API_KEY']};b='http://127.0.0.1:5678'\n"
           "t=json.load(urllib.request.urlopen(urllib.request.Request(b+'/api/v1/data-tables?limit=250',headers=h)))\n"
           "i=next((x['id'] for x in t['data'] if x['name']=='gigs_pipeline'),None)\n"
           "print(json.dumps(json.load(urllib.request.urlopen(urllib.request.Request(b+f'/api/v1/data-tables/{i}/rows?limit=250',headers=h)))['data'] if i else []))\nPY")
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, cmd], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "[]")


def main():
    if "--huecos" in sys.argv:
        huecos_informe()
        return
    if "--pendientes" in sys.argv:
        f = os.environ.get("PENDIENTES_FIXTURE")           # gates: fixture sintética en vez de la API
        pendientes_informe(contar_pendientes(json.load(open(f))) if f else None)
        return
    env = enviadas_por_semana()
    print(f"# enviadas (desde {PIPELINE.name})")
    if not env:
        print("  (sin entregas registradas todavía)")
    for s, c in env.items():
        print(f"  {s}: nuevos={c.get('nuevos', 0)} enviadas={c.get('enviadas', 0)} n8n={c.get('n8n', 0)} telegram={c.get('telegram', 0)} contrato_cl={c.get('contrato_cl', 0)} puente={c.get('puente', 0)}")
    rows = None
    if os.environ.get("N8N_API_URL") and os.environ.get("N8N_API_KEY"):
        rows = _rows_api(os.environ["N8N_API_URL"].rstrip("/"), os.environ["N8N_API_KEY"])
    elif os.environ.get("M700_SSH"):
        rows = _rows_ssh(os.environ["M700_SSH"])
    if rows is not None:
        dec = decisiones_por_semana(rows)
        print("# decisiones (Data Table gigs_pipeline)")
        if not dec:
            print("  (sin decisiones todavía)")
        for s, c in dec.items():
            print(f"  {s}: aprobadas={c.get('aprobada', 0)} descartadas={c.get('descartada', 0)} vencidas={c.get('vencida', 0)}")
    else:
        print("# decisiones: sin N8N_API_URL/N8N_API_KEY ni M700_SSH en el entorno → no consultadas")


if __name__ == "__main__":
    main()
