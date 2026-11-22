"""
La ciudad dentro de la preparación de una simulación (fork MX).

Una simulación con territorio tiene dos tipos de agentes:
  - ACTORES DE LA SEMILLA: salen del grafo del proyecto como siempre (el negocio, su equipo, competencia, medios).
    Son privados del tenant y no se reutilizan.
  - CIUDADANOS: población genérica del territorio (tabla `ciudadanos`), reutilizada entre ideas y tenants.

Para los ciudadanos:
  1. asegurar_poblacion: la muestra se reparte por el peso de cada municipio; solo se crean los que falten.
  2. selección: en cada municipio se toma su cuota al azar entre los existentes.
  3. capa por idea (tabla `ciudadano_idea`, privada): interés, conocimiento, actitud inicial, objeciones, presupuesto,
     cómo se enteraría y su postura en una frase. Un llamado por lote de 15 (DeepSeek sin razonamiento): es lo único
     que se paga por ciudadano en cada idea.
  4. Se arman directamente su perfil OASIS (sin IA ni búsquedas en el grafo) y una entidad sintética "Ciudadano" para
     que la configuración de actividad los trate igual que a los actores de la semilla (agent_id = posición).
"""
from __future__ import annotations

import json
import random
import re
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from ..utils.db import connect
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .citizen_creator import _largest_remainder, _territorio, asegurar_poblacion
from .oasis_profile_generator import OasisAgentProfile
from .zep_entity_reader import EntityNode

log = get_logger('mirofish.ciudad.preparacion')

CAPA_BATCH = 15
CAPA_PARALLEL = 4
IDEA_MAX_CHARS = 2500

CAPA_SYSTEM = """Eres el analista de una ciudad digital. Recibes una PROPUESTA (una idea, oferta o servicio que se quiere
validar) y una lista de ciudadanos reales del lugar, cada uno con su vida descrita. Para cada ciudadano estimas, con
realismo y sin complacencia, cómo se relaciona con ESA propuesta antes de que alguien se la promueva:
- "interes": 0-10, cuánto le importa el tema de la propuesta en su vida actual.
- "conoce_tema": "nada" | "poco" | "algo" | "mucho".
- "actitud_inicial": -2 (rechazo firme) … 0 (indiferente) … +2 (muy a favor), según sus valores, religión, dinero,
  experiencias y en quién confía. Usa todo el rango: en una ciudad real hay escépticos, indiferentes y entusiastas.
- "objeciones": 0-3 frases cortas con SUS objeciones probables, en su manera de hablar.
- "presupuesto_mensual_mxn": cuánto podría gastar al mes en algo así (0 si no podría o no querría), coherente con su NSE.
- "como_se_enteraria": por dónde le llegaría (grupo de WhatsApp de la colonia, Facebook, la misa, una vecina, etc.).
- "postura": 1-2 frases en primera persona, en su voz, de lo que diría si alguien le cuenta de la propuesta.
No cambies nada de la persona. Responde SOLO con un objeto json con la clave "capas" (una entrada por ciudadano, con su
"id" exacto)."""

CAPA_EXAMPLE = {"capas": [{
    "id": "cdn_ejemplo", "interes": 3, "conoce_tema": "poco", "actitud_inicial": -1,
    "objeciones": ["Eso es puro cuento para sacar dinero", "El padre dice que no hay que meterse en esas cosas"],
    "presupuesto_mensual_mxn": 0, "como_se_enteraria": "un mensaje reenviado en el grupo de WhatsApp de la cuadra",
    "postura": "Mire, yo con mi doctor del Seguro y mis tés de la abuela tengo. No pago por esas cosas."}]}


def _slug_user(nombre: str) -> str:
    base = ''.join(ch for ch in unicodedata.normalize('NFD', nombre.lower()) if unicodedata.category(ch) != 'Mn')
    base = re.sub(r'[^a-z]+', '_', base).strip('_')[:20] or 'vecino'
    return f'{base}_{random.randint(10, 999)}'


def seleccionar(slug: str, n: int, rng: random.Random) -> List[Dict[str, Any]]:
    """Asegura la población y devuelve n ciudadanos del territorio respetando el peso de cada municipio."""
    asegurar_poblacion(slug, n)
    terr = _territorio(slug)
    meta = _largest_remainder({m['cve']: m['pob_18mas'] for m in terr['municipios']}, n)
    elegidos: List[Dict[str, Any]] = []
    with connect() as c:
        for cve, k in meta.items():
            if k <= 0:
                continue
            rows = c.execute(
                "SELECT c.*, m.nombre AS municipio FROM ciudadanos c JOIN municipios m ON m.cve = c.municipio_cve "
                "WHERE c.municipio_cve = %s", (cve,)).fetchall()
            elegidos += rng.sample(rows, min(k, len(rows)))
    rng.shuffle(elegidos)
    return elegidos


def _capas_lote(llm: LLMClient, idea: str, lote: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    ciudadanos = [{
        'id': c['id'], 'edad': c['edad'], 'genero': c['genero'], 'nse': c['nse'], 'escolaridad': c['escolaridad'],
        'religion': c['religion'], 'municipio': c['municipio'], 'colonia': c['colonia'], 'ocupacion': c['ocupacion'],
        'persona': c['persona'],
    } for c in lote]
    user = (f"PROPUESTA A VALIDAR:\n{idea}\n\nCIUDADANOS ({len(ciudadanos)}):\n"
            f"{json.dumps(ciudadanos, ensure_ascii=False)}\n\n"
            f"Ejemplo del formato json de salida (solo el formato):\n{json.dumps(CAPA_EXAMPLE, ensure_ascii=False)}")
    for attempt in range(3):
        try:
            data = llm.chat_json([{'role': 'system', 'content': CAPA_SYSTEM}, {'role': 'user', 'content': user}],
                                 temperature=0.7, max_tokens=8000)
            capas = {str(x.get('id')): x for x in (data.get('capas') or []) if isinstance(x, dict)}
            if capas:
                return capas
        except Exception as exc:  # noqa: BLE001
            log.warning(f'capa por idea: lote falló (intento {attempt + 1}): {exc}')
    return {}


def _capa_limpia(c: Dict[str, Any]) -> Dict[str, Any]:
    def _int(v, lo, hi, default):
        try:
            return max(lo, min(hi, int(round(float(v)))))
        except (TypeError, ValueError):
            return default
    obj = c.get('objeciones') if isinstance(c.get('objeciones'), list) else []
    return {
        'interes': _int(c.get('interes'), 0, 10, 3),
        'conoce_tema': str(c.get('conoce_tema') or 'poco')[:10],
        'actitud_inicial': _int(c.get('actitud_inicial'), -2, 2, 0),
        'objeciones': [str(o)[:200] for o in obj][:3],
        'presupuesto_mensual_mxn': _int(c.get('presupuesto_mensual_mxn'), 0, 200000, 0),
        'como_se_enteraria': str(c.get('como_se_enteraria') or '')[:200],
        'postura': str(c.get('postura') or '')[:400],
    }


def generar_capas(ciudadanos: List[Dict[str, Any]], idea: str, simulation_id: str,
                  progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Dict[str, Any]]:
    llm = LLMClient()  # sin razonamiento
    lotes = [ciudadanos[i:i + CAPA_BATCH] for i in range(0, len(ciudadanos), CAPA_BATCH)]
    capas: Dict[str, Dict[str, Any]] = {}
    hechos = 0
    with ThreadPoolExecutor(max_workers=CAPA_PARALLEL) as pool:
        for res in pool.map(lambda l: _capas_lote(llm, idea, l), lotes):
            capas.update({k: _capa_limpia(v) for k, v in res.items()})
            hechos += 1
            if progress:
                progress(hechos, len(lotes))
    faltan = [c['id'] for c in ciudadanos if c['id'] not in capas]
    for cid in faltan:  # sin capa: neutral, para no perder al ciudadano
        capas[cid] = _capa_limpia({})
    if faltan:
        log.warning(f'{len(faltan)} ciudadanos sin capa por idea (quedan neutrales)')
    with connect(transaction=True) as c:
        for cid, capa in capas.items():
            c.execute("INSERT INTO ciudadano_idea (ciudadano_id, simulation_id, capa) VALUES (%s,%s,%s) "
                      "ON CONFLICT (ciudadano_id, simulation_id) DO UPDATE SET capa = EXCLUDED.capa",
                      (cid, simulation_id, Jsonb(capa)))
    return capas


def _persona_con_capa(c: Dict[str, Any], capa: Dict[str, Any]) -> str:
    actitud = {-2: 'la rechaza con firmeza', -1: 'desconfía', 0: 'le es indiferente', 1: 'le llama la atención',
               2: 'le entusiasma'}[capa['actitud_inicial']]
    obj = '; '.join(capa['objeciones']) if capa['objeciones'] else 'ninguna clara'
    return (f"{c['persona']}\n\nAnte la propuesta que empieza a circular en su ciudad: de entrada {actitud} "
            f"(interés {capa['interes']}/10, conoce el tema: {capa['conoce_tema']}). Sus objeciones: {obj}. "
            f"Podría gastar en algo así unos {capa['presupuesto_mensual_mxn']} pesos al mes. Probablemente se "
            f"enteraría por {capa['como_se_enteraria'] or 'conocidos'}. Lo que diría: \"{capa['postura']}\"")


def construir_agentes(ciudadanos: List[Dict[str, Any]], capas: Dict[str, Dict[str, Any]],
                      start_idx: int) -> Tuple[List[OasisAgentProfile], List[EntityNode]]:
    perfiles, entidades = [], []
    for i, c in enumerate(ciudadanos):
        capa = capas[c['id']]
        idx = start_idx + i
        lugar = (f"{c['colonia']}, {c['municipio']}" if c.get('colonia') and c['colonia'] != c['municipio']
                 else c['municipio'])
        perfiles.append(OasisAgentProfile(
            user_id=idx, user_name=_slug_user(c['nombre']), name=c['nombre'],
            bio=f"{c['ocupacion']}. {lugar}."[:200],
            persona=_persona_con_capa(c, capa),
            age=c['edad'], gender={'hombre': 'male', 'mujer': 'female'}.get(c['genero'], 'other'),
            mbti=(c.get('personalidad') or {}).get('mbti') or None, country='México',
            profession=c['ocupacion'],
            interested_topics=[t for t in [c['municipio'], c.get('colonia')] if t],
            source_entity_uuid=c['id'], source_entity_type='Ciudadano',
        ))
        entidades.append(EntityNode(
            uuid=c['id'], name=c['nombre'], labels=['Entity', 'Ciudadano'],
            summary=(f"Ciudadano de {lugar}, {c['edad']} años, {c['ocupacion']}, NSE {c['nse']}. "
                     f"Actitud inicial ante la propuesta: {capa['actitud_inicial']} (interés {capa['interes']}/10)."),
            attributes={'nse': c['nse'], 'municipio': c['municipio'], 'actitud_inicial': capa['actitud_inicial'],
                        'interes': capa['interes']},
        ))
    return perfiles, entidades


def preparar_ciudadanos(slug: str, n: int, simulation_id: str, idea: str, start_idx: int,
                        progress: Optional[Callable[[str, int], None]] = None) -> Tuple[List[OasisAgentProfile], List[EntityNode], Dict[str, Any]]:
    rng = random.Random(uuid.uuid4().int)
    if progress:
        progress('Asegurando la población del territorio', 0)
    elegidos = seleccionar(slug, n, rng)
    if progress:
        progress(f'{len(elegidos)} ciudadanos seleccionados; generando su postura ante la idea', 30)
    capas = generar_capas(elegidos, idea[:IDEA_MAX_CHARS], simulation_id,
                          (lambda h, t: progress(f'Postura ante la idea: lote {h}/{t}', 30 + int(60 * h / t)))
                          if progress else None)
    perfiles, entidades = construir_agentes(elegidos, capas, start_idx)
    actitudes = [capas[c['id']]['actitud_inicial'] for c in elegidos]
    resumen = {'territorio': slug, 'ciudadanos': len(elegidos),
               'actitud_inicial': {str(a): actitudes.count(a) for a in range(-2, 3)}}
    if progress:
        progress('Ciudadanos listos', 100)
    return perfiles, entidades, resumen
