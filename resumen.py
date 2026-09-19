#!/usr/bin/env python3
"""resumen.py — 1 mensaje diario a Telegram con lo que hizo el radar (007, T5). Solo stdlib.

  python3 resumen.py            # resumen de HOY (America/Santiago) y lo envía por Telegram
  DRY_RUN=1 python3 resumen.py  # imprime, no envía
  python3 resumen.py 2026-09-18 # otro día
Lee .pipeline.jsonl (o PIPELINE_FILE). Decisiones del usuario (Aprobar/Descartar): Data Table gigs_pipeline vía N8N_API_* o M700_SSH (opcional).
"""
import json, os, sys, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
PIPELINE = Path(os.environ.get("PIPELINE_FILE") or (HERE / ".pipeline.jsonl"))
TZ = ZoneInfo("America/Santiago")
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
MOTIVO_TXT = {"ingles": "en inglés", "seniority": "senior/lead/manager", "rol": "rol fuera de perfil",
              "sin_hit": "sin keywords", "presencial": "presencial fuera de Santiago"}


def _load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def filas_del_dia(path, dia):
    """Filas de .pipeline.jsonl cuyo ts (hora local de Santiago) cae en `dia` (YYYY-MM-DD). Pura salvo lectura."""
    out = []
    if not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("ts") or "")[:10] == dia:
            out.append(r)
    return out


def resumir(filas, decisiones=None):
    """Texto del resumen a partir de las filas del día (+ decisiones del usuario opcionales). Pura."""
    estados = Counter(r.get("estado", "entregado") for r in filas)
    motivos = Counter(r.get("motivo", "") for r in filas if r.get("estado") == "prefiltro")
    aptos = [r for r in filas if r.get("estado") in ("entregado", "entregado_plantilla")]
    lineas = [f"📊 Radar de gigs — {filas[0]['ts'][:10] if filas else 'hoy'}",
              f"nuevos: {len(filas)} · aptos enviados: {len(aptos)} · descartados por triage: {estados.get('triage_no_apto', 0)}"]
    if motivos:
        lineas.append("prefiltro: " + ", ".join(f"{MOTIVO_TXT.get(m, m)} {n}" for m, n in motivos.most_common()))
    for r in aptos[:5]:
        lineas.append(f"✅ {r.get('titulo', '')[:70]} ({r.get('fuente', '')})")
    if decisiones is not None:
        lineas.append(f"tus decisiones: aprobadas {decisiones.get('aprobada', 0)} · descartadas {decisiones.get('descartada', 0)} · vencidas {decisiones.get('vencida', 0)}")
    return "\n".join(lineas)


def decisiones_del_dia(dia):
    """Counter de decisiones de la Data Table para el día, o None si no hay acceso configurado."""
    try:
        import metricas
        rows = None
        if os.environ.get("N8N_API_URL") and os.environ.get("N8N_API_KEY"):
            rows = metricas._rows_api(os.environ["N8N_API_URL"].rstrip("/"), os.environ["N8N_API_KEY"])
        elif os.environ.get("M700_SSH"):
            rows = metricas._rows_ssh(os.environ["M700_SSH"])
        if rows is None:
            return None
        return Counter(r.get("decision", "?") for r in rows if (r.get("ts") or r.get("createdAt") or "")[:10] == dia)
    except Exception as e:
        print(f"[resumen] decisiones no disponibles: {e}", file=sys.stderr)
        return None


def enviar(texto):
    import proponer                                   # D4 (008): mismo destino prod|test|dry que el radar
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    modo, chat = proponer.destino(os.environ)
    if modo == "dry" or not (tok and chat):
        print(f"[dry-run] telegram resumen ({len(texto)} chars)" if modo == "dry" else "(sin TELEGRAM_*: no se envía)", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=20) as r:
        return r.status == 200


def main():
    _load_env()
    dia = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime("%Y-%m-%d")
    filas = filas_del_dia(PIPELINE, dia)
    texto = resumir(filas, decisiones_del_dia(dia))
    print(texto)
    enviar(texto)


if __name__ == "__main__":
    main()
