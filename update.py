#!/usr/bin/env python3
"""
SOLO INVERSIONES - Motor diario.

1. Lee los mensajes nuevos del grupo de Telegram (getUpdates con offset guardado).
2. Usa la API de Anthropic (Claude) para extraer empresas cotizadas mencionadas.
3. Valida el ticker con Finnhub y mezcla el resultado en data/companies.json.
4. Guarda el estado (offset) para no repetir mensajes.

Variables de entorno necesarias:
  TELEGRAM_TOKEN     token del bot de @BotFather
  ANTHROPIC_API_KEY  key de console.anthropic.com
  FINNHUB_KEY        (opcional) para validar tickers; si falta, se omite la validación

El commit/push de los cambios lo hace el workflow de GitHub Actions.
"""

import os, json, sys, datetime, urllib.request, urllib.parse, urllib.error

# ---- Config ----
CHAT_ID = -1004388607461            # grupo "SOLO INVERSIONES"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "companies.json")
STATE = os.path.join(ROOT, "state.json")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
THEMES = ["IA / Semis","Quantum","Espacio","Nuclear","Fintech",
          "Dividendos / REITs","Defensivas","Big Tech / Holdings",
          "Salud / Biotech","Infraestructura","Otros"]

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "").strip()
TODAY = datetime.date.today().isoformat()


def http_json(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- Telegram ----------
def get_new_messages(offset):
    url = (f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
           f"?offset={offset}&timeout=0&allowed_updates=%5B%22message%22%5D")
    res = http_json(url)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram error: {res}")
    msgs, new_offset = [], offset
    for upd in res.get("result", []):
        new_offset = max(new_offset, upd["update_id"] + 1)
        m = upd.get("message") or {}
        chat = m.get("chat") or {}
        if chat.get("id") != CHAT_ID:
            continue
        text = m.get("text")
        if not text:
            continue
        who = (m.get("from") or {}).get("first_name") \
              or (m.get("sender_chat") or {}).get("title") or "grupo"
        msgs.append({"who": who, "text": text})
    return msgs, new_offset


# ---------- Extracción con Claude ----------
def extract_companies(messages):
    if not messages:
        return []
    convo = "\n".join(f"- {m['who']}: {m['text']}" for m in messages)
    prompt = f"""Eres un analista. Abajo hay mensajes de un chat de inversión en español.
Extrae SOLO empresas COTIZADAS en bolsa mencionadas como ideas o comentadas como inversión.
Ignora: bromas, personas, medios (Motley Fool, MarketBeat), gestoras (Vanguard),
empresas privadas (p.ej. SpaceX), criptos y ETFs genéricos.
Para cada empresa devuelve su ticker bursátil real (prefiere el listado en EE. UU.; si solo
cotiza en Europa, da el símbolo del ADR/OTC en EE. UU. si existe).
Clasifícala en una de estas temáticas EXACTAS: {THEMES}.

Devuelve EXCLUSIVAMENTE un array JSON, sin texto adicional, con este formato:
[{{"name":"NVIDIA","ticker":"NVDA","exchange":"NASDAQ","theme":"IA / Semis","ctx":"motivo breve citado en el chat"}}]

Mensajes:
{convo}
"""
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    res = http_json("https://api.anthropic.com/v1/messages", data=body, headers=headers)
    text = "".join(b.get("text", "") for b in res.get("content", [])).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("["):text.rfind("]") + 1]
    try:
        data = json.loads(text)
        return data.get("companies", []) if isinstance(data, dict) else data
    except Exception as e:
        print("No se pudo parsear la respuesta de Claude:", e, "\n", text[:500])
        return []


# ---------- Validación Finnhub ----------
def valid_ticker(sym):
    if not FINNHUB_KEY:
        return True
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(sym)}&token={FINNHUB_KEY}"
        q = http_json(url, timeout=20)
        return isinstance(q.get("c"), (int, float)) and q["c"] > 0
    except Exception:
        return True  # ante la duda, no descartamos


# ---------- Main ----------
def main():
    if not TG_TOKEN or not ANTHROPIC_KEY:
        print("Faltan TELEGRAM_TOKEN o ANTHROPIC_API_KEY."); sys.exit(1)

    state = json.load(open(STATE)) if os.path.exists(STATE) else {"offset": 0}
    db = json.load(open(DATA))
    companies = db["companies"]

    messages, new_offset = get_new_messages(state.get("offset", 0))
    print(f"Mensajes nuevos del grupo: {len(messages)}")

    found = extract_companies(messages)
    added, updated = [], []
    for c in found:
        tk = (c.get("ticker") or "").upper().strip()
        if not tk:
            continue
        if not valid_ticker(tk):
            print(f"Ticker no validado por Finnhub, se omite: {tk}")
            continue
        if tk in companies:
            companies[tk]["count"] = companies[tk].get("count", 1) + 1
            companies[tk]["last_mention"] = TODAY
            updated.append(tk)
        else:
            theme = c.get("theme") if c.get("theme") in THEMES else "Otros"
            companies[tk] = {
                "ticker": tk,
                "exchange": c.get("exchange", ""),
                "name": c.get("name", tk),
                "theme": theme,
                "who": "grupo (Telegram)",
                "ctx": c.get("ctx", "Mencionada en el grupo."),
                "first_seen": TODAY,
                "last_mention": TODAY,
                "count": 1,
                "source": "telegram",
            }
            added.append(tk)

    db["companies"] = companies
    db["updated"] = TODAY
    json.dump(db, open(DATA, "w"), ensure_ascii=False, indent=1)
    json.dump({"offset": new_offset}, open(STATE, "w"))

    print(f"Nuevas: {added or '—'}")
    print(f"Re-mencionadas: {updated or '—'}")
    print(f"Total en la web: {len(companies)} · offset={new_offset}")


if __name__ == "__main__":
    main()
