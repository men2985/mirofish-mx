"""
Creador de ciudadanos de "la ciudad" (fork MX).

Modelo:
  - `municipios`: catálogo nacional (INEGI Censo 2020 + Metrópolis de México 2020 + AMAI NSE), cargado una vez sin IA.
  - `ciudadanos`: población GENÉRICA, cada uno pertenece a su MUNICIPIO y se reutiliza entre tenants y territorios.
  - `ciudades`: un TERRITORIO = municipio central + alcance ('municipio' | 'zm' = metrópoli oficial | 'radio' en km).
    Se crea solo, en segundos y sin IA (crear_territorio); es lo que llamará el registro del tenant (F4).
  - Poblar es BAJO DEMANDA (asegurar_poblacion): para una muestra de N ciudadanos en un territorio se reparte N por el
    peso de cada municipio (población de 18+) y solo se crean los que falten en cada uno.

El creador:
  - Toma los rasgos duros de cuotas oficiales del municipio (edad x sexo, NSE, escolaridad condicionada al NSE,
    religión, lengua indígena), repartidas por resto mayor.
  - Pide al modelo (DeepSeek sin razonamiento) solo nombre, colonia/localidad de una lista real, ocupación, hogar,
    redes, MBTI y una persona de 120-170 palabras.
  - NUNCA recibe la semilla ni la idea de un tenant; se le prohíbe mencionar negocios, marcas, productos o servicios.

Comandos (dentro del contenedor del worker):
  uv run python -m app.services.citizen_creator catalogo --json /tmp/catalogo_municipios.json [--ajustes /tmp/x.json]
  uv run python -m app.services.citizen_creator territorio --cve 20067 --alcance zm|municipio|radio [--km 40]
  uv run python -m app.services.citizen_creator poblar --territorio <slug> --n 120
  uv run python -m app.services.citizen_creator resumen --territorio <slug>
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from ..utils.db import connect
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

log = get_logger('mirofish.ciudad')

BATCH = 10
PARALLEL = 5
AGE_RANGES = {'18-24': (18, 24), '25-34': (25, 34), '35-44': (35, 44), '45-54': (45, 54), '55-64': (55, 64),
              '65+': (65, 88)}
NSE_ORDER = ['A/B', 'C+', 'C', 'C-', 'D+', 'D', 'E']
# Escolaridad: datos finos (4 niveles, microdatos) cuando existen; si no, los 3 del ITER nacional
EDU_FINE = ['sin_escolaridad', 'basica', 'media_superior', 'superior']
EDU_COARSE = ['sin_escolaridad', 'basica', 'posbasica']
EDU_LABEL = {'sin_escolaridad': 'sin escolaridad', 'basica': 'primaria o secundaria',
             'media_superior': 'preparatoria o bachillerato', 'superior': 'licenciatura o más',
             'posbasica': 'preparatoria o más (elige bachillerato o licenciatura según su nivel socioeconómico)'}
REL_LABEL = {'catolica': 'católica', 'protestante_evangelica': 'cristiana evangélica o protestante',
             'otra': 'otra religión', 'sin_religion': 'sin religión'}


# ---------------- cuotas ----------------
def _largest_remainder(weights: Dict[str, float], n: int) -> Dict[str, int]:
    total = sum(weights.values()) or 1
    raw = {k: n * v / total for k, v in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def _draw(rng: random.Random, dist: Dict[str, float]) -> str:
    keys = [k for k, v in dist.items() if v and v > 0]
    return rng.choices(keys, weights=[dist[k] for k in keys])[0]


def _edu_given_nse(rng: random.Random, edu: Dict[str, float], nse: str) -> str:
    # Escolaridad condicionada al NSE: la marginal oficial se inclina hacia más estudios en NSE alto y menos en bajo
    order = EDU_FINE if 'superior' in edu else EDU_COARSE
    rank = NSE_ORDER.index(nse) if nse in NSE_ORDER else 3
    tilt = (3 - rank) * 0.35
    mid = (len(order) - 1) / 2
    weights = {k: max(0.001, (edu.get(k) or 0) * math.exp(tilt * (i - mid))) for i, k in enumerate(order)}
    return _draw(rng, weights)


def _escolaridad(demo: Dict[str, Any]) -> Dict[str, float]:
    return demo.get('escolaridad_25mas') or demo['escolaridad']


def plan_slots(demo: Dict[str, Any], n: int, rng: random.Random) -> List[Dict[str, Any]]:
    cells = {}
    for g in demo['edad_sexo']:
        cells[f"{g['grupo']}|hombre"] = g['hombres']
        cells[f"{g['grupo']}|mujer"] = g['mujeres']
    slots = []
    for key, count in _largest_remainder(cells, n).items():
        grupo, sexo = key.split('|')
        lo, hi = AGE_RANGES[grupo]
        for _ in range(count):
            nse = _draw(rng, demo['nse_amai'])
            slots.append({
                'edad': rng.randint(lo, hi), 'genero': sexo, 'nse': nse,
                'escolaridad': EDU_LABEL[_edu_given_nse(rng, _escolaridad(demo), nse)],
                'religion': REL_LABEL.get(_draw(rng, demo['religion']), 'otra religión'),
                'lengua_indigena': rng.random() * 100 < float(demo.get('hablantes_lengua_indigena_pct') or 0),
            })
    rng.shuffle(slots)
    return slots


def _lugares(demo: Dict[str, Any]) -> List[str]:
    """Colonias reales (si se cargaron) o, si no, las localidades más pobladas del municipio."""
    col = demo.get('colonias') or []
    names = [c['nombre'] if isinstance(c, dict) else str(c) for c in col]
    return names or list(demo.get('localidades') or [])


# ---------------- modelo ----------------
SYSTEM = """Eres el creador de ciudadanos de una ciudad digital que reproduce municipios reales de México.
Creas personas comunes, creíbles y variadas, a partir de rasgos que YA vienen fijados (edad, sexo, nivel socioeconómico
AMAI, escolaridad, religión). Tú solo completas lo demás, coherente con esos rasgos y con la vida real del lugar.

Reglas estrictas:
- Son ciudadanos GENÉRICOS: PROHIBIDO mencionar negocios, marcas, productos, terapias, servicios, consultorios,
  campañas o ideas concretas. Nada de "le interesa X producto". Sí puedes decir hábitos generales (va al mercado, ve
  noticias en Facebook, reza el rosario, juega fútbol los domingos, desconfía de los vendedores por WhatsApp).
- Respeta exactamente los rasgos fijados; la ocupación y el lugar deben cuadrar con el NSE, la edad y la escolaridad.
- "colonia" SE ELIGE de la lista de lugares dada, escrita exactamente igual (colonia o localidad del municipio).
- Nombres y apellidos mexicanos realistas y variados (no repitas apellidos dentro del lote). Si "lengua_indigena" es
  true, la persona habla además una lengua indígena de su región y eso se nota en su historia.
- "persona": 120-170 palabras en tercera persona: rutina, trabajo, hogar, dinero (cuánto gana o de qué vive, en qué
  gasta), salud general, en quién confía, cómo se informa, carácter y una tensión o preocupación actual. Sin listas.
- Responde SOLO con un objeto json con la clave "ciudadanos" (una entrada por rasgo recibido, mismo "slot")."""

EXAMPLE = {"ciudadanos": [{
    "slot": 1, "nombre": "Rosa Elena Cruz Méndez", "colonia": "Reforma", "ocupacion": "secretaria en una notaría",
    "hogar": "casada, dos hijos adolescentes, casa propia pagada con crédito Infonavit",
    "redes": ["WhatsApp", "Facebook"], "mbti": "ISFJ",
    "persona": "Rosa Elena trabaja de lunes a viernes en una notaría del centro… (120-170 palabras)"}]}


def _ask_batch(llm: LLMClient, mun: Dict[str, Any], slots: List[Dict[str, Any]], lugares: List[str]) -> List[Dict]:
    rasgos = [{'slot': i + 1, **s} for i, s in enumerate(slots)]
    user = (f"Municipio: {mun['nombre']}, {mun['estado']}, México"
            + (f" (parte de la zona {mun['zona_nombre']})" if mun.get('zona_nombre') else '') + ".\n"
            f"Lugares válidos para \"colonia\": {', '.join(lugares)}.\n\n"
            f"Rasgos fijados ({len(rasgos)} personas):\n{json.dumps(rasgos, ensure_ascii=False)}\n\n"
            f"Ejemplo del formato json de salida (solo el formato; el contenido es tuyo):\n"
            f"{json.dumps(EXAMPLE, ensure_ascii=False)}")
    data = llm.chat_json([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
                         temperature=0.9, max_tokens=8000)
    items = data.get('ciudadanos') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError('respuesta sin lista "ciudadanos"')
    return items


def _validate(item: Dict, slot: Dict, lugares: List[str], rng: random.Random) -> Dict[str, Any]:
    persona = str(item.get('persona') or '').strip()
    if len(persona.split()) < 60:
        raise ValueError('persona demasiado corta')
    colonia = str(item.get('colonia') or '').strip()
    if colonia not in lugares:
        colonia = rng.choice(lugares) if lugares else None
    redes = item.get('redes') if isinstance(item.get('redes'), list) else []
    return {
        'id': f"cdn_{uuid.uuid4().hex[:12]}",
        'nombre': str(item.get('nombre') or '').strip()[:120],
        'edad': slot['edad'], 'genero': slot['genero'], 'nse': slot['nse'],
        'escolaridad': slot['escolaridad'], 'religion': slot['religion'],
        'colonia': colonia,
        'ocupacion': str(item.get('ocupacion') or '').strip()[:200],
        'hogar': str(item.get('hogar') or '').strip()[:300],
        'redes': [str(r)[:40] for r in redes][:6],
        'personalidad': {'mbti': str(item.get('mbti') or '')[:4].upper(),
                         'lengua_indigena': bool(slot.get('lengua_indigena'))},
        'persona': persona,
    }


# ---------------- catálogo y territorios ----------------
def cargar_catalogo(path: str, ajustes: Optional[str] = None) -> Dict[str, int]:
    data = json.load(open(path, encoding='utf-8'))
    extra = {}
    if ajustes:
        # Datos finos de un municipio (p. ej. Oaxaca de Juárez: colonias reales y escolaridad de microdatos)
        a = json.load(open(ajustes, encoding='utf-8'))
        extra[a['cve']] = a['demografia']
    with connect(transaction=True) as c:
        for m in data['municipios']:
            demo = dict(m['demografia'])
            demo.update(extra.get(m['cve'], {}))
            z = m.get('zona') or {}
            c.execute(
                "INSERT INTO municipios (cve, nombre, estado, lat, lon, poblacion, pob_18mas, zona_id, zona_tipo, "
                "zona_nombre, demografia) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (cve) DO UPDATE SET "
                "nombre=EXCLUDED.nombre, estado=EXCLUDED.estado, lat=EXCLUDED.lat, lon=EXCLUDED.lon, "
                "poblacion=EXCLUDED.poblacion, pob_18mas=EXCLUDED.pob_18mas, zona_id=EXCLUDED.zona_id, "
                "zona_tipo=EXCLUDED.zona_tipo, zona_nombre=EXCLUDED.zona_nombre, demografia=EXCLUDED.demografia",
                (m['cve'], m['nombre'], m['estado'], m['lat'], m['lon'], m['poblacion'], m['pob_18mas'],
                 z.get('id'), z.get('tipo'), z.get('nombre'), Jsonb(demo)),
            )
    return {'municipios': len(data['municipios']), 'ajustados': len(extra)}


def _km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def crear_territorio(cve: str, alcance: str = 'zm', km: Optional[int] = None) -> Dict[str, Any]:
    """Crea (o devuelve) el territorio de una ciudad. Sin IA: solo datos oficiales. Lo usará el registro del tenant."""
    with connect() as c:
        central = c.execute("SELECT * FROM municipios WHERE cve = %s", (cve,)).fetchone()
        if not central:
            raise ValueError(f'municipio {cve} no está en el catálogo')
        if alcance == 'zm' and central['zona_id']:
            muns = c.execute("SELECT cve, nombre, pob_18mas FROM municipios WHERE zona_id = %s",
                             (central['zona_id'],)).fetchall()
            nombre = f"{central['nombre']} y su zona metropolitana"
        elif alcance == 'radio':
            km = int(km or 40)
            todos = c.execute("SELECT cve, nombre, pob_18mas, lat, lon FROM municipios WHERE lat IS NOT NULL "
                              "AND abs(lat - %s) < 1.5 AND abs(lon - %s) < 1.5",
                              (central['lat'], central['lon'])).fetchall()
            muns = [m for m in todos if _km((central['lat'], central['lon']), (m['lat'], m['lon'])) <= km]
            nombre = f"{central['nombre']} y {km} km a la redonda"
        else:
            alcance = 'municipio'
            muns = [central]
            nombre = central['nombre']
        slug = {'municipio': cve, 'zm': f'{cve}-zm', 'radio': f'{cve}-r{km}'}[alcance]
        lista = [{'cve': m['cve'], 'nombre': m['nombre'], 'pob_18mas': m['pob_18mas']} for m in muns]
        r = c.execute(
            "INSERT INTO ciudades (slug, nombre, estado, pais, clave_inegi, municipio_central, alcance, radio_km, "
            "municipios) VALUES (%s,%s,%s,'México',%s,%s,%s,%s,%s) ON CONFLICT (slug) DO UPDATE SET "
            "nombre=EXCLUDED.nombre, municipios=EXCLUDED.municipios RETURNING id, slug, nombre",
            (slug, nombre, central['estado'], cve, cve, alcance, km if alcance == 'radio' else None, Jsonb(lista)),
        ).fetchone()
    return {**r, 'municipios': len(lista), 'pob_18mas': sum(m['pob_18mas'] for m in lista)}


def _territorio(slug: str) -> Dict[str, Any]:
    with connect() as c:
        r = c.execute("SELECT * FROM ciudades WHERE slug = %s", (slug,)).fetchone()
    if not r or not r['municipios']:
        raise ValueError(f'territorio {slug} no existe (créalo con crear_territorio)')
    return r


# ---------------- poblar bajo demanda ----------------
def _insert(rows: List[Dict[str, Any]], municipio_cve: str) -> None:
    with connect(transaction=True) as c:
        for r in rows:
            c.execute(
                "INSERT INTO ciudadanos (id, municipio_cve, nombre, edad, genero, colonia, ocupacion, nse, escolaridad, "
                "religion, hogar, redes, personalidad, persona) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r['id'], municipio_cve, r['nombre'], r['edad'], r['genero'], r['colonia'], r['ocupacion'], r['nse'],
                 r['escolaridad'], r['religion'], r['hogar'], Jsonb(r['redes']), Jsonb(r['personalidad']),
                 r['persona']),
            )


def asegurar_poblacion(slug: str, n: int, seed: Optional[int] = None) -> Dict[str, Any]:
    """Garantiza que el territorio tenga ciudadanos para una muestra de n: reparte n por el peso de cada municipio y
    crea SOLO los que falten en cada uno (los existentes se reutilizan)."""
    terr = _territorio(slug)
    pesos = {m['cve']: m['pob_18mas'] for m in terr['municipios']}
    meta = _largest_remainder(pesos, n)
    with connect() as c:
        tiene = {r['municipio_cve']: r['n'] for r in c.execute(
            "SELECT municipio_cve, count(*) AS n FROM ciudadanos WHERE municipio_cve = ANY(%s) GROUP BY 1",
            (list(pesos),)).fetchall()}
        muns = {r['cve']: r for r in c.execute("SELECT * FROM municipios WHERE cve = ANY(%s)",
                                               ([k for k, v in meta.items() if v > tiene.get(k, 0)],)).fetchall()}
    faltan = {k: v - tiene.get(k, 0) for k, v in meta.items() if v > tiene.get(k, 0)}
    rng = random.Random(seed if seed is not None else uuid.uuid4().int)
    llm = LLMClient()  # sin razonamiento (decisión 11-22)
    trabajos = []
    for cve, k in faltan.items():
        mun = muns[cve]
        slots = plan_slots(mun['demografia'], k, rng)
        lugares = _lugares(mun['demografia'])
        trabajos += [(mun, slots[i:i + BATCH], lugares) for i in range(0, len(slots), BATCH)]

    creados, descartados = Counter(), 0

    def work(mun, batch, lugares):
        local_rng = random.Random(rng.random())
        for attempt in range(3):
            try:
                items = _ask_batch(llm, mun, batch, lugares)
                by_slot = {int(it.get('slot', 0)): it for it in items if isinstance(it, dict)}
                rows, bad = [], 0
                for i, slot in enumerate(batch):
                    try:
                        rows.append(_validate(by_slot.get(i + 1) or {}, slot, lugares, local_rng))
                    except ValueError:
                        bad += 1
                return mun['cve'], rows, bad
            except Exception as exc:  # noqa: BLE001
                log.warning(f"lote de {mun['nombre']} falló (intento {attempt + 1}): {exc}")
        return mun['cve'], [], len(batch)

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        for fut in as_completed([pool.submit(work, *t) for t in trabajos]):
            cve, rows, bad = fut.result()
            if rows:
                _insert(rows, cve)
            creados[cve] += len(rows)
            descartados += bad
            log.info(f"{terr['nombre']}: {sum(creados.values())} ciudadanos nuevos, {descartados} descartados")
    return {'territorio': slug, 'muestra': n, 'reutilizados': sum(min(tiene.get(k, 0), v) for k, v in meta.items()),
            'creados': sum(creados.values()), 'descartados': descartados,
            'municipios_con_faltantes': len(faltan)}


def resumen(slug: str) -> Dict[str, Any]:
    terr = _territorio(slug)
    cves = [m['cve'] for m in terr['municipios']]
    with connect() as c:
        rows = c.execute("SELECT municipio_cve, edad, genero, nse, escolaridad, religion FROM ciudadanos "
                         "WHERE municipio_cve = ANY(%s)", (cves,)).fetchall()

    def bucket(e):
        return next(k for k, (lo, hi) in AGE_RANGES.items() if lo <= e <= hi or (k == '65+' and e >= 65))
    nombres = {m['cve']: m['nombre'] for m in terr['municipios']}
    return {
        'territorio': terr['nombre'], 'municipios': len(cves), 'ciudadanos': len(rows),
        'por_municipio': {nombres[k]: v for k, v in Counter(r['municipio_cve'] for r in rows).most_common()},
        'edad': dict(sorted(Counter(bucket(r['edad']) for r in rows).items())),
        'genero': dict(Counter(r['genero'] for r in rows)),
        'nse': dict(Counter(r['nse'] for r in rows)),
        'religion': dict(Counter(r['religion'] for r in rows)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('catalogo'); a.add_argument('--json', required=True); a.add_argument('--ajustes')
    t = sub.add_parser('territorio'); t.add_argument('--cve', required=True)
    t.add_argument('--alcance', default='zm', choices=['municipio', 'zm', 'radio']); t.add_argument('--km', type=int)
    p = sub.add_parser('poblar'); p.add_argument('--territorio', required=True); p.add_argument('--n', type=int, default=120)
    p.add_argument('--seed', type=int)
    r = sub.add_parser('resumen'); r.add_argument('--territorio', required=True)
    args = ap.parse_args(argv)
    if args.cmd == 'catalogo':
        out = cargar_catalogo(args.json, args.ajustes)
    elif args.cmd == 'territorio':
        out = crear_territorio(args.cve, args.alcance, args.km)
    elif args.cmd == 'poblar':
        out = asegurar_poblacion(args.territorio, args.n, args.seed)
    else:
        out = resumen(args.territorio)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    sys.exit(main())
