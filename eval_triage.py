#!/usr/bin/env python3
"""eval_triage.py — evalúa el triage `claude -p` contra las etiquetas del usuario (007, T6). INFORME, no gate.
Leave-one-out sobre los casos origen=isaac de tests/fixtures/triage/casos.jsonl: cada caso se evalúa SIN
aparecer en sus propios ejemplos. Escribe eval/triage-resultados.json e imprime matriz de confusión.
  python3 eval_triage.py          # 12 llamadas reales a claude -p (~2-4 min)
"""
import json, os, sys
from pathlib import Path

os.environ.setdefault("DRY_RUN", "0")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proponer  # noqa: E402

OUT = HERE / "eval" / "triage-resultados.json"
BASELINE = {"aciertos": 5, "total": 12, "precision_global": round(5 / 12, 2), "fuente": "REPORTE-006 / decisiones Aprobar-Descartar 17-18 sep"}
PREVIO_007 = {"aciertos": 10, "total": 12, "exactitud": 0.83, "nota": "incluía UpGuard y PaperStreet como aptas (excluidas por D1 en 008)"}


def evaluar():
    casos = [json.loads(l) for l in proponer.CASOS_FILE.read_text().splitlines() if l.strip()]
    casos = [c for c in casos if c.get("origen") == "isaac" and not c.get("excluido_eval")]   # D1: los excluidos no cuentan
    perfil = proponer.load_perfil()
    res, tp = [], 0
    tn = fp = fn = 0
    for c in casos:
        v = proponer.via_claude(perfil, c["titulo"], c.get("desc", ""), c.get("fuente", ""), excluir_id=c["id"])
        pred = None if v is None else ("apta" if v["apto"] else "no_apta")
        ok = pred == c["etiqueta"]
        if c["etiqueta"] == "apta":
            tp += ok; fn += (not ok)
        else:
            tn += ok; fp += (not ok)
        res.append({"id": c["id"], "etiqueta": c["etiqueta"], "prediccion": pred, "ok": ok,
                    "motivo": (v or {}).get("motivo", ""), "huecos": (v or {}).get("huecos", [])})
        print(f"{'✓' if ok else '✗'} {c['etiqueta']:8} → {pred or 'None':8} | {c['titulo'][:50]} | {(v or {}).get('motivo','')[:70]}")
    n = len(casos)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    acc = (tp + tn) / n if n else 0.0
    informe = {"n": n, "matriz": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "precision": round(prec, 2), "recall": round(rec, 2),
               "exactitud": round(acc, 2), "linea_base": BASELINE, "previo_007": PREVIO_007, "casos": res}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n")
    print(f"\nmatriz: TP={tp} FP={fp} FN={fn} TN={tn} · precisión={prec:.2f} · recall={rec:.2f} · exactitud={acc:.2f} ({tp+tn}/{n}) · línea base {BASELINE['aciertos']}/{BASELINE['total']}={BASELINE['precision_global']}")
    return informe


if __name__ == "__main__":
    evaluar()
