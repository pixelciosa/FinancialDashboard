"""
Narrativa IA del Reporte de Riesgo (fase R2), con Gemma.

La IA no calcula ni decide nada: recibe un bloque de HECHOS ya calculados (risk.py y
financials.py) y solo redacta cuatro campos: actualización, catalizadores, riesgos y conclusión.

Cada campo pasa por un validador antes de mostrarse:
- Cifras: todo número del texto tiene que estar en los HECHOS (con tolerancia de redondeo).
- Contenido: sin precios objetivo ni recomendaciones de compra/venta.
- Idioma: el de la interfaz.
Un campo que no pasa, o que el modelo no devolvió, queda en None con su motivo en
`unavailable`; la UI muestra "No disponible en el modelo actual". Nunca texto de relleno.

Se regenera solo si cambia `inputs_hash` del reporte de riesgo o pasan 30 días.
"""
import json
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import financials as fin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'data', 'narrative')
SCHEMA_VERSION = 1
MAX_AGE_DAYS = 30
MODEL_TIMEOUT_S = 60
FIELDS = ('update', 'catalysts', 'risks', 'bottom_line')
MAX_ITEM_CHARS = 260
MAX_TEXT_CHARS = 700

# Una generación a la vez: el plan gratuito de NVIDIA limita el ritmo.
EXECUTOR = ThreadPoolExecutor(max_workers=1)
_INFLIGHT = set()
_INFLIGHT_LOCK = threading.Lock()


# ── Números: extracción y verificación ──────────────────────────────────────

_ISO_DATE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_PERIOD = re.compile(r"\b(?:Q[1-4]\s?(?:FY)?'?\d{2,4}|FY\s?'?\d{2,4}|H[12]\s?\d{4})\b", re.I)
# Número con o sin unidad pegada (1.5x, $6.86B, 200d); no dentro de palabras (COVID19).
_NUM = re.compile(r'(?<![\w.])[-+−]?\d+(?:[.,]\d+)*')


def parse_number(tok):
    """'1.234,5' / '1,234.5' / '25,1' / '-3.4' → float. Coma decimal si tiene 1-2 dígitos detrás."""
    t = tok.replace('−', '-').replace('+', '')
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
    elif ',' in t:
        head, _, tail = t.rpartition(',')
        t = (head.replace(',', '') + '.' + tail) if len(tail) <= 2 and t.count(',') == 1 else t.replace(',', '')
    elif t.count('.') > 1:
        t = t.replace('.', '')
    try:
        return float(t)
    except ValueError:
        return None


def extract_numbers(text):
    """Números del texto, sin fechas ISO ni etiquetas de período (Q3 2026, FY26)."""
    if not text:
        return []
    years = [float(m.group(1)) for m in _ISO_DATE.finditer(text)]
    clean = _PERIOD.sub(' ', _ISO_DATE.sub(' ', text))
    nums = [parse_number(m.group(0)) for m in _NUM.finditer(clean)]
    return [n for n in nums if n is not None] + years


def is_grounded(n, allowed, rel=0.015, abs_tol=0.051):
    """Un número está respaldado si coincide con alguno de los HECHOS (redondeo incluido).
    Los enteros chicos (0 a 10) se aceptan: 'tres riesgos', 'dos trimestres'."""
    if float(n).is_integer() and 0 <= abs(n) <= 10:
        return True
    for a in allowed:
        if abs(n - a) <= max(abs_tol, rel * abs(a)) or abs(abs(n) - abs(a)) <= max(abs_tol, rel * abs(a)):
            return True
    return False


# ── Contenido prohibido e idioma ────────────────────────────────────────────

FORBIDDEN = [
    r'\bprice target\b', r'\btarget price\b', r'\bprecio objetivo\b', r'\bobjetivo de precio\b',
    r'\bstrong buy\b', r'\bbuy\b(?!-)', r'\bsell\b(?!-)', r'\bcompra fuerte\b',   # 'sell-off'/'buy-back' sí
    r'\b(?:recomendamos|recomiendo|recommend(?:ed|s)?)\b',
    r'\b(?:comprar|vender)\b', r'\bfair value of\b', r'\bvalor justo de \$',
]
_FORBIDDEN_RE = re.compile('|'.join(FORBIDDEN), re.I)

_ES = {'el', 'la', 'los', 'las', 'de', 'del', 'que', 'y', 'en', 'con', 'por', 'para', 'una', 'un', 'su', 'es', 'se', 'más'}
_EN = {'the', 'and', 'of', 'to', 'in', 'with', 'for', 'is', 'its', 'are', 'on', 'by', 'a', 'an', 'this', 'that'}


def language_of(text):
    words = re.findall(r'[a-záéíóúñ]+', text.lower())
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    if es + en < 3:
        return None          # muy corto para decidir
    return 'es' if es > en else 'en'


def check_text(text, allowed, lang, max_chars):
    """Devuelve (texto válido | None, motivo | None)."""
    if not isinstance(text, str) or not text.strip() or text.strip().upper() in ('N/A', 'NULL', 'NONE'):
        return None, 'empty'
    text = text.strip()
    if len(text) > max_chars:
        return None, 'too_long'
    if _FORBIDDEN_RE.search(text):
        return None, 'forbidden_content'
    detected = language_of(text)
    if detected and detected != lang:
        return None, 'wrong_language'
    bad = [n for n in extract_numbers(text) if not is_grounded(n, allowed)]
    if bad:
        return None, 'ungrounded_numbers'
    return text, None


def validate(raw, allowed, lang):
    """raw: dict devuelto por el modelo. Devuelve (fields, unavailable)."""
    fields, unavailable = {}, {}
    if not isinstance(raw, dict):
        return {f: None for f in FIELDS}, {f: 'invalid_json' for f in FIELDS}
    for f in ('update', 'bottom_line'):
        fields[f], why = check_text(raw.get(f), allowed, lang, MAX_TEXT_CHARS)
        if why:
            unavailable[f] = why
    for f in ('catalysts', 'risks'):
        items = raw.get(f)
        if not isinstance(items, list) or not items:
            fields[f], unavailable[f] = None, 'empty'
            continue
        ok, reasons = [], []
        for it in items[:3]:
            txt, why = check_text(it, allowed, lang, MAX_ITEM_CHARS)
            (ok.append(txt) if txt else reasons.append(why))
        # Si algún ítem se descarta se descarta la lista: una lista a medias engaña
        if reasons:
            fields[f], unavailable[f] = None, reasons[0]
        else:
            fields[f] = ok
    return fields, unavailable


# ── HECHOS y prompt ─────────────────────────────────────────────────────────

def _money(v, scale):
    return None if v is None else f'{v / scale:.2f}{"B" if scale == 1e9 else "M"}'


def build_facts(report, fin_payload=None):
    """Texto con los datos calculados. Es lo único que ve el modelo y la base del validador."""
    fp = fin_payload or {}
    L = [f"TICKER: {report['ticker']} ({report.get('name')}) · kind {report['kind']} · {report.get('type') or ''}",
         f"RISK SCORE: {report['risk_score']}/100 (0 = lowest risk) · level {report['level']} · "
         f"valuation {report.get('valuation_label') or 'n/a'} · data coverage {round(report['coverage'] * 100)}%"]
    for p in report['pillars']:
        parts = []
        for m in p['metrics']:
            if m.get('excluded'):
                parts.append(f"{m['key']}=not applicable ({m.get('note')})")
                continue
            if m['missing']:
                parts.append(f"{m['key']}=no data")
                continue
            v = m['value']
            if m['key'] == 'fcf_positive':
                v = 'positive' if (v or 0) >= 0 else 'negative'
            elif isinstance(v, float):
                v = round(v, 2)
            note = f", {m['note']}" if m.get('note') else ''
            unit = {'%': '%', 'x': 'x'}.get(m.get('unit'), '')
            parts.append(f"{m['key']}={v}{unit} ({m['points']}/{m['max']} risk points, {m['tier']}{note})")
        L.append(f"PILLAR {p['key']} {p['points']}/{p['max']}: " + '; '.join(parts))
    mk = report.get('market') or {}
    if mk.get('n_days', 0) >= 30:
        L.append(f"MARKET (1y daily): volatility {mk['volatility_pct']}% · max drawdown {mk['max_drawdown_pct']}% · "
                 f"from 52w high {mk['drawdown_from_high_pct']}% · RSI14 {mk.get('rsi14')} · "
                 f"above 200d average {mk.get('above_sma200')} · 200d average rising {mk.get('sma200_rising')}")
    p12 = report.get('prices_12m') or []
    if len(p12) >= 2:
        chg = round((p12[-1]['close'] / p12[0]['close'] - 1) * 100, 1)
        L.append(f"PRICE: month-end close {p12[0]['month']} {round(p12[0]['close'], 2)} → {p12[-1]['month']} "
                 f"{round(p12[-1]['close'], 2)} ({chg:+}%)")
    scale = 1e6 if fp.get('revUnit') == '$M' else 1e9
    cur = fp.get('currency') or 'USD'
    for q in (fp.get('quarters') or [])[-4:]:
        rev = None if q.get('rev') is None else f"{q['rev']:.2f}{'M' if scale == 1e6 else 'B'} {cur}"
        bits = [f"revenue {rev}" if rev else None,
                f"yoy {q['rev_yoy']:+}%" if q.get('rev_yoy') is not None else None,
                f"gross margin {q['gm']}%" if q.get('gm') is not None else None,
                f"net margin {q['nm']}%" if q.get('nm') is not None else None,
                f"adj EPS {q['eps_adj']} vs estimate {q['eps_est']} (surprise {q['surprise_pct']:+}%, {q['beat']})"
                if q.get('surprise_pct') is not None else None]
        L.append(f"QUARTER {q['label']}: " + ', '.join(b for b in bits if b))
    for e in (report.get('events') or [])[-6:]:
        val = '' if e.get('value') is None else f" {e['value']}{'%' if e['type'] == 'earnings' else ''}"
        L.append(f"EVENT {e['date']}: {e['type']}{val}")
    if fp.get('next_earnings_date'):
        L.append(f"NEXT EARNINGS: {fp['next_earnings_date']}")
    return '\n'.join(L)


LANG_NAME = {'es': 'Spanish (Argentina, neutral register)', 'en': 'English'}


def build_prompt(facts, lang):
    return f"""You write short, educational notes for an intermediate retail investor.
Write ALL text in {LANG_NAME.get(lang, 'Spanish')}.

Rules (a note that breaks any rule is discarded automatically):
1. Use ONLY the FACTS below. Do not add any number, date or fact that is not in FACTS.
2. You may round numbers, but every number you write must come from FACTS.
3. Never give a price target, a fair value, or a buy/sell/hold recommendation.
4. Describe what the data shows and why it matters; do not predict prices.
5. The risk score is computed already: higher = more risk. Do not invent your own score.
6. If the FACTS are not enough for a field, return null for that field.

FACTS:
{facts}

Return ONLY a JSON object, no markdown:
{{
  "update": "2-3 sentences on the latest quarter and price behaviour, or null",
  "catalysts": ["up to 3 short positive factors present in FACTS"] or null,
  "risks": ["up to 3 short risk factors present in FACTS"] or null,
  "bottom_line": "1-2 sentences summarising valuation and risk level in plain words, or null"
}}"""


def extract_json(text):
    if not text:
        return None
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    candidates = [m.group(1)] if m else []
    m2 = re.search(r'\{[\s\S]*\}', text)
    if m2:
        candidates.append(m2.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


# ── Generación y caché ──────────────────────────────────────────────────────

def _path(ticker, lang):
    return os.path.join(CACHE_DIR, f'{ticker.upper()}.{lang}.json')


def read_cache(ticker, lang):
    try:
        with open(_path(ticker, lang), encoding='utf-8') as f:
            n = json.load(f)
        return n if n.get('schema_version') == SCHEMA_VERSION else None
    except (OSError, ValueError):
        return None


def write_cache(ticker, lang, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=f'.{ticker.upper()}.', suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(ticker, lang))


def is_current(cached, inputs_hash, now=None):
    if not cached:
        return False
    now = now or datetime.now(timezone.utc)
    young = now - datetime.fromisoformat(cached['generated_at']) < timedelta(days=MAX_AGE_DAYS)
    return young and cached.get('inputs_hash') == inputs_hash


def generate(report, fin_payload, lang, call_model, model_name=None, now=None):
    """Arma HECHOS, llama al modelo y valida. Siempre devuelve un resultado completo."""
    now = now or datetime.now(timezone.utc)
    facts = build_facts(report, fin_payload)
    allowed = extract_numbers(facts)
    base = {'schema_version': SCHEMA_VERSION, 'ticker': report['ticker'], 'lang': lang,
            'inputs_hash': report['inputs_hash'], 'generated_at': now.isoformat(), 'model': model_name}
    if call_model is None:
        return {**base, 'fields': {f: None for f in FIELDS}, 'unavailable': {f: 'no_api_key' for f in FIELDS}}
    try:
        raw_text = call_model(build_prompt(facts, lang))
    except Exception:
        raw_text = None
    if not raw_text:
        return {**base, 'fields': {f: None for f in FIELDS}, 'unavailable': {f: 'model_error' for f in FIELDS}}
    fields, unavailable = validate(extract_json(raw_text), allowed, lang)
    return {**base, 'fields': fields, 'unavailable': unavailable}


def get_narrative(ticker, lang, report_fn, fin_fn, call_model, model_name=None, now=None):
    """Para el endpoint: devuelve lo vigente o lanza la generación en segundo plano.
    report_fn(ticker) → reporte de riesgo; fin_fn(ticker) → payload financiero (o {})."""
    ticker, lang = ticker.upper(), (lang if lang in LANG_NAME else 'es')
    report = report_fn(ticker)
    cached = read_cache(ticker, lang)
    if is_current(cached, report['inputs_hash'], now):
        return {'status': 'ready', **cached}

    key = (ticker, lang)
    with _INFLIGHT_LOCK:
        running = key in _INFLIGHT
        if not running:
            _INFLIGHT.add(key)
    if not running:
        def job():
            try:
                with fin.ticker_lock(f'narr:{ticker}:{lang}'):
                    result = generate(report, fin_fn(ticker), lang, call_model, model_name)
                    write_cache(ticker, lang, result)
            except Exception as e:
                print(f'[narrative] {ticker}/{lang}: {e}')
            finally:
                with _INFLIGHT_LOCK:
                    _INFLIGHT.discard(key)
        EXECUTOR.submit(job)
    # Mientras se genera, se muestra la versión anterior (marcada) si existe
    out = {'status': 'generating', 'ticker': ticker, 'lang': lang}
    if cached:
        out.update({'previous': cached})
    return out
