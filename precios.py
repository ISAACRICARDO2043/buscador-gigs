#!/usr/bin/env python3
"""precios.py — termómetro de precios REAL para tu nicho, sacado de GetOnBrd.

Junta los rangos de salario (USD) publicados en gigs de automatización y los resume
por seniority. NO es opinión ni adivinanza: son los números que las empresas publican.

Ojo: son SUELDOS MENSUALES (empleo remoto LATAM). Te sirven de ancla para saber cuánto
vale tu skill; el precio por proyecto freelance lo derivás de ahí (ver nota al final).

Uso:  ./precios.py
"""
import urllib.request, urllib.parse, json, sys, statistics

UA = "gig-pricing/1.0"
QUERIES = ["automatizacion", "n8n", "zapier", "make.com", "scraping", "chatbot",
           "whatsapp", "integracion api", "rpa", "no-code", "web scraping"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def seniority_labels():
    try:
        data = fetch("https://www.getonbrd.com/api/v0/seniorities")
        return {int(d["id"]): d["attributes"].get("name", f"nivel {d['id']}")
                for d in data.get("data", [])}
    except Exception:
        return {1: "Sin experiencia", 2: "Junior", 3: "Semi Senior",
                4: "Senior", 5: "Senior/Lead"}


def main():
    labels = seniority_labels()
    by_sen = {}           # seniority_id -> [midpoints]
    seen = set()
    n_con = n_tot = 0
    for q in QUERIES:
        try:
            url = "https://www.getonbrd.com/api/v0/search/jobs?" + urllib.parse.urlencode(
                {"query": q, "per_page": 30})
            data = fetch(url)
        except Exception as e:
            print(f"[{q}] error: {e}", file=sys.stderr)
            continue
        for j in data.get("data", []):
            jid = j.get("id")
            if jid in seen:
                continue
            seen.add(jid)
            a = j.get("attributes", {})
            n_tot += 1
            mn, mx = a.get("min_salary"), a.get("max_salary")
            if not (mn or mx):
                continue
            n_con += 1
            mid = (mn + mx) / 2 if (mn and mx) else (mn or mx)
            sid = (a.get("seniority") or {}).get("data", {}).get("id")
            by_sen.setdefault(sid, []).append(mid)

    print(f"\n📊 TERMÓMETRO DE PRECIOS — automatización remota (fuente: GetOnBrd, LATAM)")
    print(f"   {n_con} de {n_tot} gigs publican rango ({round(100*n_con/max(n_tot,1))}%). Valores USD/mes.\n")
    print(f"   {'Seniority':<16}{'gigs':>5}{'mín':>9}{'mediana':>10}{'máx':>9}")
    print("   " + "-" * 48)
    allmids = []
    for sid in sorted(by_sen, key=lambda x: (x is None, x)):
        mids = by_sen[sid]
        allmids += mids
        lab = labels.get(sid, "Sin dato") if sid is not None else "Sin dato"
        print(f"   {lab:<16}{len(mids):>5}{min(mids):>9,.0f}{statistics.median(mids):>10,.0f}{max(mids):>9,.0f}")
    if allmids:
        med = statistics.median(allmids)
        hr = med / 160  # ~160 h hábiles/mes
        print("\n   💡 Mediana general: USD %.0f/mes  →  ~USD %.0f/hora si lo pasás a freelance." % (med, hr))
        print("      Para proyecto cerrado: estimá horas × tu hora, y sumá margen (20-40%).")
    print("\n   Nota: empleo mensual ≠ proyecto freelance. Usá esto como ANCLA de cuánto vale tu")
    print("   skill, no como cotización directa. En el 1er contacto NO des precio: pedí alcance.\n")


if __name__ == "__main__":
    main()
