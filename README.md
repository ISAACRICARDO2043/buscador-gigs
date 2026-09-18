# buscador-gigs — radar de ofertas de automatización con triage por LLM

> **EN:** A job-radar for automation freelancers: polls 6 public job-board APIs, filters by skills/geo/language, deduplicates,
> runs a triage + cover-letter step with `claude -p` (strict JSON), and delivers only the suitable ones to Telegram through n8n
> for one-tap approval. Runs 24/7 on a Linux box with a systemd user timer. Pure Python stdlib, no scraping behind logins.

Herramienta de lead-gen propia: encuentra gigs de **automatización / integraciones / bots / scraping** cobrables desde LATAM,
descarta lo que no encaja y te deja en Telegram una carta lista para aprobar. Sin Selenium, sin cuentas en los job boards,
sin API keys de pago: solo APIs/RSS públicos y `claude -p` (Claude Code) para el triage.

## Qué hace
1. Consulta **Remotive, RemoteOK, WeWorkRemotely, GetOnBrd, Jobicy y Himalayas** (APIs y feeds públicos).
2. Filtra por señales fuertes/débiles (n8n, Zapier, scraping, WhatsApp, webhooks, RPA…), geo (worldwide/LATAM; bloquea "USA only",
   presencial/híbrido), idioma (marca ⚠️ lo que exige inglés) y afinidad aprendida (los gigs que te interesaron antes suben).
3. Deduplica por id y por firma `(empresa, título)` en `.seen.json`.
4. Para los mejores nuevos, **`claude -p` decide y redacta**: `{"tipo":"empleo|proyecto","apto":true|false,"motivo":"…","texto":"…"}`.
   `apto=false` (Senior/Lead/QA, inglés C1, stack ajeno) → no se envía nada.
5. Lo apto sale por **n8n → Telegram** (webhook) con la carta/propuesta; el resto de los nuevos llega como aviso compacto.

## Arquitectura
```
 Remotive  RemoteOK  WWR  GetOnBrd  Jobicy  Himalayas        (APIs / RSS públicos)
     └───────┴────────┴──────┴────────┴────────┘
                    buscar.py
        filtro skills → geo → idioma → afinidad → dedup (.seen.json)
                         │  top-N nuevos
                    proponer.py
        claude -p  (JSON estricto: tipo · apto · motivo · texto)   ──►  .triage.jsonl
                         │ apto
              n8n (webhook → Telegram)  ──fallback──►  Telegram directo
                         │
                 Telegram: carta lista para aprobar
```

## Cómo correrlo
```bash
cp .env.example .env            # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, N8N_WEBHOOK_URL (opcional)
cp perfil.md.example perfil.md  # o escribí perfil.md a mano: quién sos, casos concretos, cómo trabajás
DRY_RUN=1 bash run-buscar.sh    # corrida en seco: busca y filtra, no envía nada ni guarda estado
bash run-buscar.sh              # corrida real
python3 proponer.py "https://www.getonbrd.com/jobs/<slug>"   # triage + carta para un gig puntual
python3 -m unittest -q tests.test_puro                         # tests de funciones puras (sin red)
```
Requisitos: Python ≥ 3.10 (solo stdlib) y, para el triage, [Claude Code](https://claude.com/claude-code) logueado (`claude -p`).
Sin `claude` disponible cae a una propuesta por plantilla y avisa.

## Decisiones de diseño
- **Solo APIs públicas, sin login.** Nada de navegadores automatizados ni cuentas en los boards: cero riesgo de bloqueo, cero credenciales de terceros.
- **El modelo no decide el precio.** El rango orientativo sale de `precios.py`; el LLM solo puede citarlo, nunca inventar un número.
- **`DRY_RUN=1` es ley.** Sin envíos, sin escribir estado, sin invocar `claude`. Los gates corren siempre en seco; el único envío real es el gate de producción declarado.
- **Triage con salida estructurada.** `claude -p --output-format json --json-schema …`, un turno, sin herramientas, timeout 120 s. Salida no-JSON = fallback, nunca excepción.
- **Tests de funciones puras** (`tests/test_puro.py`): filtros geo, clasificación de arquetipo, parser del triage, DRY_RUN sin subprocess, "claude ausente" sin excepción, "no apto ⇒ no se entrega".
- **Gate con control negativo.** Cada gate se prueba también con el fallo real (token falso, test roto, `.env` roto) para confirmar que se pone rojo.

## Estado
En producción: corre 5 veces al día en un servidor Linux con un **systemd user timer** (sin root, sin Docker), triage con `claude -p`,
entrega por n8n a Telegram. Próximo: botones Aprobar/Descartar en el mensaje y registro del pipeline.

## Autor
**Isacc Ricardo Peña Simancas** — automatización de procesos e integraciones (n8n, Python, bots de Telegram/WhatsApp, agentes con LLM), Santiago de Chile, 100% remoto.
Contacto: isaacri2043@gmail.com · GitHub: [ISAACRICARDO2043](https://github.com/ISAACRICARDO2043) · Licencia MIT.
