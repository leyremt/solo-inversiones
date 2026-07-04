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

import os, json, sys, time, datetime, urllib.request, urllib.parse, urllib.error

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
Para cada empresa indica:
- "ticker": su ticker bursátil principal.
- "yh": su símbolo EXACTO en Yahoo Finance. EE. UU.: el ticker tal cual (p. ej. NVDA).
  Europa: ticker + sufijo de mercado (p. ej. IBE.MC, BAS.DE, LGEN.L, ENR.DE, BMPS.MI, NESN.SW).
- "exchange": el mercado (NASDAQ, NYSE, Madrid, XETRA, Milán, Londres, SIX...).
- "ctx": motivo breve. NUNCA incluyas nombres de personas del chat; el contexto debe ser anónimo.
Clasifícala en una de estas temáticas EXACTAS: {THEMES}.

Devuelve EXCLUSIVAMENTE un array JSON, sin texto adicional, con este formato:
[{{"name":"Iberdrola","ticker":"IBE","yh":"IBE.MC","exchange":"Madrid","theme":"Infraestructura","ctx":"motivo breve citado en el chat"}}]

Mensajes:
{convo}
"""
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    res = http_json("https://api.anthropic.com/v1/messages", data=body, headers=headers)
    text = "".join(b.get("text", "") for b in res.get("content", [])).strip()
    if not text:
        print("Respuesta de Claude sin texto:", json.dumps(res)[:800])
        return []
    # Quedarnos solo con el array JSON, aunque venga con texto o vallas alrededor.
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e != -1 and e > s:
        text = text[s:e + 1]
    try:
        data = json.loads(text)
    except Exception:
        # Rescatar un array truncado: cortar hasta el último objeto completo.
        cut = text.rfind("}")
        if cut == -1:
            print("No se pudo parsear (sin objetos):", text[:800]); return []
        try:
            data = json.loads(text[:cut + 1] + "]")
        except Exception as e:
            print("No se pudo parsear la respuesta de Claude:", e, "\n", text[:800])
            return []
    return data.get("companies", []) if isinstance(data, dict) else data


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


# ---------- Precios (Yahoo Finance, con Finnhub de respaldo) ----------
YH_SUFFIX = {
    "MADRID": ".MC", "BME": ".MC", "BOLSA DE MADRID": ".MC",
    "MILAN": ".MI", "MILANO": ".MI", "BORSA": ".MI", "BIT": ".MI",
    "XETRA": ".DE", "FRANKFURT": ".DE", "FRA": ".DE", "ETR": ".DE", "GER": ".DE", "DEUTSCHE": ".DE",
    "SIX": ".SW", "SWISS": ".SW", "ZURICH": ".SW", "VTX": ".SW",
    "PARIS": ".PA", "EPA": ".PA",
    "AMSTERDAM": ".AS",
    "LONDON": ".L", "LSE": ".L", "LON": ".L",
    "LISBON": ".LS", "BRUSSELS": ".BR", "STOCKHOLM": ".ST",
    "OSLO": ".OL", "COPENHAGEN": ".CO", "HELSINKI": ".HE", "VIENNA": ".VI",
}
US_HINTS = ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "OTC", "US")

def yahoo_symbols(c):
    """Lista de símbolos candidatos a probar en Yahoo, en orden de preferencia."""
    tk = (c.get("ticker") or "").upper().strip()
    ex = (c.get("exchange") or "").upper()
    cands = []
    if c.get("yh"): cands.append(c["yh"])          # símbolo que ya funcionó
    cands.append(tk)                                # tal cual (p. ej. DWS.DE ya con sufijo)
    cands.append(tk.replace(".", "-"))             # clases USA: BRK.B -> BRK-B
    if c.get("fh"): cands.append(c["fh"])           # ADR/OTC en EE. UU.
    if "." not in tk:
        suf = next((v for k, v in YH_SUFFIX.items() if k in ex), None)
        if suf:
            cands.append(tk + suf)
        if not any(h in ex for h in US_HINTS):     # europea probable: probar sufijos comunes
            for v in (".MC", ".MI", ".DE", ".SW", ".PA", ".AS", ".L"):
                cands.append(tk + v)
    seen, out = set(), []
    for s in cands:
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out

def yahoo_quote(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?interval=1d&range=2d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    res = (d.get("chart") or {}).get("result")
    if not res:
        return None
    meta = res[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    pct = ((price / prev - 1) * 100) if isinstance(prev, (int, float)) and prev else 0.0
    return {"price": round(price, 2), "pct": round(pct, 2),
            "sym": sym, "cur": meta.get("currency", "")}

def finnhub_quote(sym):
    if not FINNHUB_KEY or not sym:
        return None
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(sym)}&token={FINNHUB_KEY}"
        q = http_json(url, timeout=15)
        if isinstance(q.get("c"), (int, float)) and q["c"] > 0:
            return {"price": round(q["c"], 2), "pct": round(q.get("dp") or 0, 2),
                    "sym": sym, "cur": "USD"}
    except Exception:
        pass
    return None

def refresh_prices(companies):
    ok = 0
    for tk, c in companies.items():
        q = None
        for sym in yahoo_symbols(c):
            try:
                q = yahoo_quote(sym)
            except Exception:
                q = None
            if q:
                break
            time.sleep(0.05)
        if not q:
            q = finnhub_quote(c.get("fh")) or finnhub_quote(tk)
        if q:
            c["price"] = q["price"]; c["change_pct"] = q["pct"]
            c["currency"] = q["cur"]; c["yh"] = q["sym"]; ok += 1
        else:
            c["price"] = None; c["change_pct"] = None
    print(f"Precios resueltos: {ok}/{len(companies)}")


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
                "who": "El grupo",
                "ctx": c.get("ctx", "Mencionada en el grupo."),
                "first_seen": TODAY,
                "last_mention": TODAY,
                "count": 1,
                "source": "telegram",
            }
            if c.get("yh"):
                companies[tk]["yh"] = str(c["yh"]).strip()
            added.append(tk)

    refresh_prices(companies)

    db["companies"] = companies
    db["updated"] = TODAY
    db["price_asof"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    json.dump(db, open(DATA, "w"), ensure_ascii=False, indent=1)
    json.dump({"offset": new_offset}, open(STATE, "w"))

    print(f"Nuevas: {added or '—'}")
    print(f"Re-mencionadas: {updated or '—'}")
    print(f"Total en la web: {len(companies)} · offset={new_offset}")


if __name__ == "__main__":
    main()
