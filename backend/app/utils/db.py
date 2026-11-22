"""
Postgres del stack para el estado compartido entre la API y el worker (F3, fork MX, 2026-11-22).

Antes las tareas, la cola implícita (hilos) y los lectores de grafo vivían en la memoria de la API: un reinicio o un
deploy los perdía. Aquí viven en tablas; la API y el worker las ven igual.

Conexión: DATABASE_URL del .env montado (la misma base del servicio de login). Una conexión por operación (volumen
bajo: decenas de escrituras por minuto como máximo), en autocommit salvo donde se pide transacción.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

_schema_ready = False
_schema_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS trabajos (
    task_id         text PRIMARY KEY,
    task_type       text NOT NULL,
    status          text NOT NULL DEFAULT 'pending',
    progress        integer NOT NULL DEFAULT 0,
    message         text NOT NULL DEFAULT '',
    result          jsonb,
    error           text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    progress_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- cola: solo los trabajos con kind se ejecutan en el worker; el resto son tareas de seguimiento
    kind            text,
    params          jsonb,
    locale          text,
    en_cola         boolean NOT NULL DEFAULT false,
    worker          text,
    intentos        integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    heartbeat_at    timestamptz
);
CREATE INDEX IF NOT EXISTS trabajos_cola_idx ON trabajos (created_at) WHERE en_cola;
CREATE INDEX IF NOT EXISTS trabajos_tipo_idx ON trabajos (task_type, created_at DESC);

-- ---------- La ciudad (2026-11-22) ----------
-- ciudades: ciudad REAL con su demografía oficial (cuotas para poblarla) y fuentes
CREATE TABLE IF NOT EXISTS ciudades (
    id          serial PRIMARY KEY,
    slug        text UNIQUE NOT NULL,
    nombre      text NOT NULL,
    estado      text NOT NULL,
    pais        text NOT NULL DEFAULT 'México',
    clave_inegi text,
    demografia  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- ciudadanos: población GENÉRICA y compartida entre tenants. Nunca contiene datos de un tenant ni de una idea.
CREATE TABLE IF NOT EXISTS ciudadanos (
    id           text PRIMARY KEY,
    ciudad_id    integer NOT NULL REFERENCES ciudades(id),
    nombre       text NOT NULL,
    edad         integer NOT NULL,
    genero       text NOT NULL,
    colonia      text,
    ocupacion    text,
    nse          text,
    escolaridad  text,
    religion     text,
    hogar        text,
    redes        jsonb NOT NULL DEFAULT '[]'::jsonb,
    personalidad jsonb NOT NULL DEFAULT '{}'::jsonb,
    persona      text NOT NULL,
    version      integer NOT NULL DEFAULT 1,
    origen       text NOT NULL DEFAULT 'sintetico',
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ciudadanos_ciudad_idx ON ciudadanos (ciudad_id, edad, genero, nse);
-- ciudadano_idea: capa corta por idea (privada del tenant): interés, actitud, objeciones, presupuesto
CREATE TABLE IF NOT EXISTS ciudadano_idea (
    ciudadano_id  text NOT NULL REFERENCES ciudadanos(id),
    simulation_id text NOT NULL,
    tenant        text,
    capa          jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ciudadano_id, simulation_id)
);

-- municipios: catálogo nacional (INEGI 2020 + Metrópolis 2020 + AMAI), se carga una vez sin IA
CREATE TABLE IF NOT EXISTS municipios (
    cve         text PRIMARY KEY,
    nombre      text NOT NULL,
    estado      text NOT NULL,
    lat         double precision,
    lon         double precision,
    poblacion   integer NOT NULL,
    pob_18mas   integer NOT NULL,
    zona_id     text,
    zona_tipo   text,
    zona_nombre text,
    demografia  jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS municipios_zona_idx ON municipios (zona_id);
-- los ciudadanos pertenecen a su MUNICIPIO; una "ciudad" es un territorio = conjunto de municipios
ALTER TABLE ciudadanos ADD COLUMN IF NOT EXISTS municipio_cve text REFERENCES municipios(cve);
ALTER TABLE ciudadanos ALTER COLUMN ciudad_id DROP NOT NULL;  -- legado: el dueño real es el municipio
CREATE INDEX IF NOT EXISTS ciudadanos_municipio_idx ON ciudadanos (municipio_cve, edad, genero, nse);
ALTER TABLE ciudades ADD COLUMN IF NOT EXISTS municipio_central text;
ALTER TABLE ciudades ADD COLUMN IF NOT EXISTS alcance text;
ALTER TABLE ciudades ADD COLUMN IF NOT EXISTS radio_km integer;
ALTER TABLE ciudades ADD COLUMN IF NOT EXISTS municipios jsonb NOT NULL DEFAULT '[]'::jsonb;

-- ---------- Créditos (F4, pasarela Mercado Pago) ----------
-- Tarifas por modelo (incluido el horario pico de DeepSeek)
CREATE TABLE IF NOT EXISTS llm_pricing (
    provider                    text NOT NULL,
    model                       text NOT NULL,
    pricing_type                text NOT NULL,          -- tokens | per_image
    price_input_per_1m          numeric(12,6),
    price_cache_hit_per_1m      numeric(12,6),
    price_output_per_1m         numeric(12,6),
    price_input_peak_per_1m     numeric(12,6),
    price_cache_hit_peak_per_1m numeric(12,6),
    price_output_peak_per_1m    numeric(12,6),
    peak_hours_utc              text,
    peak_isodow                 text,
    peak_exempt_dates           text,
    price_per_image_low         numeric(10,6),
    price_per_image_medium      numeric(10,6),
    price_per_image_high        numeric(10,6),
    notes                       text,
    PRIMARY KEY (provider, model)
);
INSERT INTO llm_pricing (provider, model, pricing_type, price_input_per_1m, price_cache_hit_per_1m, price_output_per_1m,
                         price_input_peak_per_1m, price_cache_hit_peak_per_1m, price_output_peak_per_1m,
                         peak_hours_utc, peak_isodow, notes) VALUES
  ('deepseek', 'deepseek-flash', 'tokens', 0.15, 0.003, 0.60, 0.30, 0.006, 1.20, '01-04,06-10', '1-5',
   'Tarifa 2026-11-22. Base = valle; pico x2.'),
  ('openai', 'text-embedding-3-small', 'tokens', 0.02, NULL, 0, NULL, NULL, NULL, NULL, NULL,
   'Vectores del recomendador (F1) y del grafo propio (F2).')
ON CONFLICT (provider, model) DO NOTHING;
INSERT INTO llm_pricing (provider, model, pricing_type, price_per_image_low, price_per_image_medium, price_per_image_high,
                         notes) VALUES
  ('openai', 'gpt-image-2', 'per_image', 0.006, 0.053, 0.211, 'Tarifa 2026-11-22. Ilustración.')
ON CONFLICT (provider, model) DO NOTHING;

CREATE TABLE IF NOT EXISTS system_config (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO system_config (key, value) VALUES
  ('credit_multiplier_ia', '8'),         -- configurable en system_config
  ('credit_multiplier_imagen', '8'),
  -- Estimación previa al cobro (costo REAL en USD por unidad; medido 11-22 con DeepSeek sin razonamiento):
  -- preparación 0.014 USD / 22 agentes; simulación 0.0187 USD / (22 agentes x 10 rondas); informe con razonamiento
  ('estimado_perfil_usd', '0.0007'),
  ('estimado_ciudadano_usd', '0.0003'),
  ('estimado_fragmento_grafo_usd', '0.0015'),
  ('estimado_agente_ronda_usd', '0.00009'),
  ('estimado_informe_usd', '0.03'),
  ('estimado_segundos_agente_ronda', '0.15'),
  ('credit_packages_json',
   '[{"id":"pkg_10","mxn":200,"usd":10,"label":"$200 MXN"},{"id":"pkg_25","mxn":500,"usd":25,"label":"$500 MXN"},{"id":"pkg_35","mxn":700,"usd":35,"label":"$700 MXN"}]')
ON CONFLICT (key) DO NOTHING;

-- Recargas por Mercado Pago (Checkout Pro, pago único)
CREATE TABLE IF NOT EXISTS credit_payments (
    id             uuid PRIMARY KEY,
    user_email     text NOT NULL,
    package_id     text NOT NULL,
    monto_mxn      numeric(10,2) NOT NULL,
    credit_usd     numeric(10,2) NOT NULL,
    mp_preference  text,
    mp_payment_id  text UNIQUE,
    payment_method text,
    estado         text NOT NULL DEFAULT 'pendiente',   -- pendiente | completado | rechazado
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    paid_at        timestamptz
);
-- Auditoría de cada movimiento de saldo
CREATE TABLE IF NOT EXISTS credit_usage_log (
    id                  bigserial PRIMARY KEY,
    user_email          text NOT NULL,
    tipo                text NOT NULL,      -- grafo_ia | perfiles_ia | simulacion_ia | informe_ia | ilustracion_ia | api_ia | recarga | manual_adjust
    costo_real_usd      numeric(12,6) NOT NULL DEFAULT 0,
    multiplier_snapshot numeric(8,2),
    cobro_usuario_usd   numeric(12,4) NOT NULL,   -- negativo = suma al saldo
    balance_before_usd  numeric(12,4),
    balance_after_usd   numeric(12,4),
    reference_id        text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS credit_usage_log_ref_idx ON credit_usage_log (tipo, reference_id)
    WHERE reference_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS credit_usage_log_user_idx ON credit_usage_log (user_email, created_at DESC);

CREATE TABLE IF NOT EXISTS lectores_grafo (
    graph_id   text NOT NULL,
    reader_id  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (graph_id, reader_id)
);
"""


def _url() -> str:
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL no configurado (F3: va en el .env montado)')
    return url


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with psycopg.connect(_url(), autocommit=True, connect_timeout=10) as conn:
            # Evita que API y worker creen las tablas a la vez al arrancar juntos
            conn.execute("SELECT pg_advisory_lock(424242)")
            try:
                conn.execute(SCHEMA)
            finally:
                conn.execute("SELECT pg_advisory_unlock(424242)")
        _schema_ready = True


@contextmanager
def connect(transaction: bool = False):
    ensure_schema()
    conn = psycopg.connect(_url(), autocommit=not transaction, connect_timeout=10, row_factory=dict_row)
    try:
        if transaction:
            with conn.transaction():
                yield conn
        else:
            yield conn
    finally:
        conn.close()
