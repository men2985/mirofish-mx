"""
Créditos prepago (F4, fork MX, 2026-11-22), con Mercado Pago como pasarela.

- Cuentas (tabla users, la mantiene el servicio auth): 'bypass' no paga ni registra; 'credit' paga con saldo_usd.
- Costo real: tokens x tarifa de `llm_pricing` (precio de caché y horario pico de
  DeepSeek) o precio por imagen. Cobro = costo real x multiplicador de `system_config` (credit_multiplier_ia). Cada movimiento queda en credit_usage_log con el multiplicador del momento.
- Se cobra por TRABAJO completo (grafo, perfiles, simulación, informe): se cobra entero aunque el saldo
  quede negativo, y el siguiente trabajo se bloquea hasta recargar (is_out_of_credits).

Medición: un `Meter` por trabajo (worker) o por petición (API) vive en un contextvar; create_chat_completion y la
ilustración registran ahí su uso. Para que los hilos de los ThreadPoolExecutor hereden el medidor se instala
`install_context_propagation()` (copia el contexto en cada submit).
"""
from __future__ import annotations

import contextvars
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from ..utils.db import connect
from ..utils.logger import get_logger

log = get_logger('mirofish.creditos')

CURRENT_METER: contextvars.ContextVar = contextvars.ContextVar('mirofish_meter', default=None)


# ---------------- medidor ----------------
class Meter:
    def __init__(self, label: str = ''):
        self.label = label
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, rec: Dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(rec)

    def extend(self, recs: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.calls.extend(recs)

    def cost_usd(self) -> float:
        return round(sum(cost_of(r) for r in list(self.calls)), 6)

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Dict[str, float]] = {}
        for r in list(self.calls):
            m = out.setdefault(r.get('model', '?'), {'llamadas': 0, 'entrada': 0, 'cache': 0, 'salida': 0,
                                                     'imagenes': 0, 'costo_usd': 0.0})
            m['llamadas'] += 1
            m['entrada'] += int(r.get('in') or 0)
            m['cache'] += int(r.get('hit') or 0)
            m['salida'] += int(r.get('out') or 0)
            m['imagenes'] += int(r.get('images') or 0)
            m['costo_usd'] = round(m['costo_usd'] + cost_of(r), 6)
        return out


def start_meter(label: str = '') -> tuple:
    meter = Meter(label)
    return meter, CURRENT_METER.set(meter)


def _usage_get(usage: Any, key: str, default=0):
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(key, default)
    return getattr(usage, key, default)


def record_chat_usage(model: str, usage: Any) -> None:
    """Lo llama create_chat_completion tras cada respuesta (DeepSeek u OpenAI compatible)."""
    meter = CURRENT_METER.get()
    if meter is None or usage is None:
        return
    hit = _usage_get(usage, 'prompt_cache_hit_tokens', None)
    if hit is None:
        details = _usage_get(usage, 'prompt_tokens_details', None)
        hit = _usage_get(details, 'cached_tokens', 0) if details is not None else 0
    meter.add({'model': model, 'in': int(_usage_get(usage, 'prompt_tokens', 0) or 0), 'hit': int(hit or 0),
               'out': int(_usage_get(usage, 'completion_tokens', 0) or 0), 'ts': time.time()})


def record_image(model: str, quality: str, n: int = 1) -> None:
    meter = CURRENT_METER.get()
    if meter is not None:
        meter.add({'model': model, 'images': n, 'quality': quality, 'ts': time.time()})


# Los hilos de un ThreadPoolExecutor no heredan contextvars: se copia el contexto en cada submit
_patched = False


def install_context_propagation() -> None:
    global _patched
    if _patched:
        return
    original = ThreadPoolExecutor.submit

    def submit(self, fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return original(self, ctx.run, fn, *args, **kwargs)

    ThreadPoolExecutor.submit = submit
    _patched = True


# ---------------- tarifas ----------------
_PRICING: Dict[str, Dict[str, Any]] = {}
_PRICING_AT = 0.0


def _pricing() -> Dict[str, Dict[str, Any]]:
    global _PRICING, _PRICING_AT
    if time.time() - _PRICING_AT > 300:
        with connect() as c:
            rows = c.execute("SELECT * FROM llm_pricing").fetchall()
        _PRICING = {r['model']: r for r in rows}
        _PRICING_AT = time.time()
    return _PRICING


def _is_peak(p: Dict[str, Any], ts: float) -> bool:
    """Franjas UTC, días ISO y fechas exentas."""
    if p.get('price_input_peak_per_1m') is None or not p.get('peak_hours_utc'):
        return False
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    exentas = {x.strip() for x in (p.get('peak_exempt_dates') or '').split(',') if x.strip()}
    if dt.strftime('%Y-%m-%d') in exentas:
        return False
    dias = (p.get('peak_isodow') or '1-5').strip()
    if '-' in dias:
        lo, hi = (int(x) for x in dias.split('-'))
        if not lo <= dt.isoweekday() <= hi:
            return False
    for franja in (p.get('peak_hours_utc') or '').split(','):
        if '-' in franja:
            a, b = (int(x) for x in franja.split('-'))
            if a <= dt.hour < b:
                return True
    return False


def _model_key(model: str) -> str:
    m = (model or '').lower()
    if m.startswith('deepseek'):
        return 'deepseek-flash' if 'flash' in m or m == 'deepseek-chat' else m
    return m


def cost_of(rec: Dict[str, Any]) -> float:
    p = _pricing().get(_model_key(rec.get('model', '')))
    if not p:
        return 0.0
    f = lambda v: float(v) if v is not None else 0.0  # noqa: E731
    if p['pricing_type'] == 'per_image':
        q = (rec.get('quality') or 'medium').lower()
        unit = {'low': p['price_per_image_low'], 'high': p['price_per_image_high']}.get(q, p['price_per_image_medium'])
        return f(unit) * int(rec.get('images') or 1)
    peak = _is_peak(p, rec.get('ts') or time.time())
    p_in = f(p['price_input_peak_per_1m'] if peak else p['price_input_per_1m'])
    p_hit = f(p['price_cache_hit_peak_per_1m'] if peak else p['price_cache_hit_per_1m'])
    p_out = f(p['price_output_peak_per_1m'] if peak else p['price_output_per_1m'])
    tin, hit, tout = int(rec.get('in') or 0), int(rec.get('hit') or 0), int(rec.get('out') or 0)
    if p_hit > 0 and 0 < hit <= tin:
        inp = hit / 1e6 * p_hit + (tin - hit) / 1e6 * p_in
    else:
        inp = tin / 1e6 * p_in
    return inp + tout / 1e6 * p_out


# ---------------- configuración ----------------
def config(key: str, default: str) -> str:
    with connect() as c:
        r = c.execute("SELECT value FROM system_config WHERE key = %s", (key,)).fetchone()
    return r['value'] if r else default


def multiplier(tipo: str) -> float:
    key = 'credit_multiplier_imagen' if tipo == 'ilustracion_ia' else 'credit_multiplier_ia'
    try:
        return float(config(key, '8'))
    except ValueError:
        return 8.0


# ---------------- saldo ----------------
def account(email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    with connect() as c:
        return c.execute("SELECT email, rol, account_type, saldo_usd FROM users WHERE email = %s", (email,)).fetchone()


def is_out_of_credits(email: Optional[str]) -> bool:
    """Bypass nunca; credit con saldo <= 0 sí. Sin cuenta (proyectos viejos) = admin."""
    acct = account(email) if email else None
    if not acct or acct['account_type'] == 'bypass':  # el cobro depende solo del tipo de cuenta
        return False
    return Decimal(acct['saldo_usd'] or 0) <= 0


def charge(email: Optional[str], costo_real_usd: float, tipo: str, reference_id: Optional[str] = None,
           metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Descuenta costo real x multiplicador (atómico + auditoría). bypass/admin: no toca saldo ni registra.
    Idempotente por (tipo, reference_id)."""
    acct = account(email) if email else None
    if not acct or acct['account_type'] == 'bypass' or costo_real_usd <= 0:
        return None
    mult = multiplier(tipo)
    cobro = round(Decimal(str(costo_real_usd)) * Decimal(str(mult)), 4)
    with connect(transaction=True) as c:
        if reference_id and c.execute("SELECT 1 FROM credit_usage_log WHERE tipo = %s AND reference_id = %s",
                                      (tipo, reference_id)).fetchone():
            return None
        before = c.execute("SELECT saldo_usd FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()['saldo_usd']
        after = Decimal(before) - cobro
        c.execute("UPDATE users SET saldo_usd = %s WHERE email = %s", (after, email))
        c.execute("INSERT INTO credit_usage_log (user_email, tipo, costo_real_usd, multiplier_snapshot, cobro_usuario_usd, "
                  "balance_before_usd, balance_after_usd, reference_id, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (email, tipo, costo_real_usd, mult, cobro, before, after, reference_id, Jsonb(metadata or {})))
    log.info(f'Cobro {tipo} a {email}: costo real {costo_real_usd:.4f} USD x{mult} = {cobro} USD (saldo {after})')
    return {'cobro_usd': float(cobro), 'saldo_usd': float(after)}


def add_credits(email: str, usd: float, tipo: str = 'recarga', reference_id: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Suma saldo (recarga, reembolso, ajuste). Idempotente por (tipo, reference_id)."""
    with connect(transaction=True) as c:
        if reference_id and c.execute("SELECT 1 FROM credit_usage_log WHERE tipo = %s AND reference_id = %s",
                                      (tipo, reference_id)).fetchone():
            return False
        row = c.execute("SELECT saldo_usd FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()
        if not row:
            return False
        before = Decimal(row['saldo_usd'])
        after = before + Decimal(str(usd))
        c.execute("UPDATE users SET saldo_usd = %s WHERE email = %s", (after, email))
        c.execute("INSERT INTO credit_usage_log (user_email, tipo, costo_real_usd, multiplier_snapshot, cobro_usuario_usd, "
                  "balance_before_usd, balance_after_usd, reference_id, metadata) VALUES (%s,%s,0,1,%s,%s,%s,%s,%s)",
                  (email, tipo, -Decimal(str(usd)), before, after, reference_id, Jsonb(metadata or {})))
    log.info(f'Abono {tipo} a {email}: +{usd} USD (saldo {after})')
    return True


def charge_meter(email: Optional[str], meter: Meter, tipo: str, reference_id: Optional[str] = None,
                 extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cost = meter.cost_usd()
    meta = {'consumo': meter.summary(), **(extra or {})}
    if cost <= 0:
        return None
    return charge(email, cost, tipo, reference_id or str(uuid.uuid4()), meta)
