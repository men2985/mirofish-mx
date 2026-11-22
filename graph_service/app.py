"""
Servicio de grafo de MiroFish (fork MX): reemplazo local de Zep Cloud.

Graphiti (Apache-2.0) + FalkorDB, en su propio contenedor, hablado por HTTP desde el backend por la red interna de Docker.
Expone solo las operaciones que MiroFish usa de Zep: crear/borrar grafo, fijar ontología, agregar episodios (asíncrono, con
estado por episodio), listar nodos y aristas, leer un nodo y sus aristas, y buscar (aristas o nodos).

Aislamiento: cada grafo es un group_id de Graphiti; con FalkorDB, Graphiti guarda cada group_id en su propia base de grafos.

Modelos: extracción con DeepSeek (API compatible con OpenAI, modo JSON), vectores con OpenAI text-embedding-3-small.
Las búsquedas usan RRF (sin reordenador por modelo), así no se paga una llamada extra por búsqueda.

Estado de los episodios en memoria: si el servicio se reinicia a mitad de una ingesta, esos episodios quedan como error.
"""
import asyncio
import contextvars
import time
import json
import logging
import os
import re
import uuid as uuidlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, create_model

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF, NODE_HYBRID_SEARCH_RRF
from graphiti_core.utils.bulk_utils import RawEpisode
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from dotenv import load_dotenv  # dependencia de graphiti-core

# En Swarm los secretos van en un .env montado (igual que el backend), no como variables del servicio
load_dotenv(os.environ.get('GRAPH_ENV_FILE', '/app/.env'), override=False)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('graph_service')

FALKOR_HOST = os.environ.get('FALKORDB_HOST', 'falkordb')
FALKOR_PORT = int(os.environ.get('FALKORDB_PORT', '6379'))
DATA_DIR = os.environ.get('GRAPH_DATA_DIR', '/data')
BULK_SIZE = int(os.environ.get('GRAPH_BULK_SIZE', '8'))
MAX_COROUTINES = int(os.environ.get('GRAPH_MAX_COROUTINES', '10'))
MAX_CONNECTIONS = int(os.environ.get('FALKORDB_MAX_CONNECTIONS', '64'))

LLM_API_KEY = os.environ['LLM_API_KEY']
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')
LLM_MODEL = os.environ.get('LLM_MODEL_NAME', 'deepseek-flash')
EMB_API_KEY = os.environ.get('OPENAI_EMBEDDINGS_API_KEY') or os.environ['ILLUSTRATION_OPENAI_API_KEY']
EMB_MODEL = os.environ.get('OPENAI_EMBEDDINGS_MODEL', 'text-embedding-3-small')

# Atributos que no pueden usarse en tipos de Graphiti (chocan con campos propios de nodos y aristas)
PROTECTED = {'uuid', 'name', 'group_id', 'labels', 'created_at', 'summary', 'attributes', 'name_embedding',
             'fact', 'fact_embedding', 'episodes', 'source_node_uuid', 'target_node_uuid', 'valid_at', 'invalid_at',
             'expired_at', 'reference_time'}

app = FastAPI(title='MiroFish graph service')
graphiti: Optional[Graphiti] = None
base_driver: Optional[FalkorDriver] = None
queue: 'asyncio.Queue[Tuple[str, List[Tuple[str, str]]]]' = asyncio.Queue()
episodes: Dict[str, Dict[str, Any]] = {}
ontologies: Dict[str, Dict[str, Any]] = {}
# Consumo de IA por grafo para los créditos de MiroFish (F4): el backend lo lee al terminar la construcción
CURRENT_GID: contextvars.ContextVar = contextvars.ContextVar('graph_gid', default=None)
USAGE: Dict[str, List[Dict[str, Any]]] = {}


def _note_usage(model, prompt=0, hit=0, completion=0):
    gid = CURRENT_GID.get()
    if gid:
        USAGE.setdefault(gid, []).append({'model': str(model or ''), 'in': int(prompt or 0), 'hit': int(hit or 0),
                                          'out': int(completion or 0), 'ts': time.time()})


def _note_response(resp, fallback_model):
    u = getattr(resp, 'usage', None)
    if u is None:
        return
    hit = getattr(u, 'prompt_cache_hit_tokens', None)
    if hit is None:
        d = getattr(u, 'prompt_tokens_details', None)
        hit = getattr(d, 'cached_tokens', 0) if d is not None else 0
    _note_usage(getattr(resp, 'model', None) or fallback_model, getattr(u, 'prompt_tokens', 0), hit,
                getattr(u, 'completion_tokens', 0))


node_graph: Dict[str, str] = {}  # uuid de nodo -> grafo (se llena al listar/buscar/leer)


# ---------- parche: atributos anidados ----------
# FalkorDB (como Neo4j) solo acepta valores primitivos o listas de primitivos. El modelo a veces genera atributos anidados
# (dict o listas de dict) y la escritura en lote falla con "Property values can only be of primitive types". Antes de
# escribir, lo anidado se guarda como texto JSON. Idea tomada del fork tt-a1i/MiroFish-local (graphiti issue #683).
def _flatten_value(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
        return [x for x in v if x is not None]
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def _apply_flatten_patch():
    from graphiti_core.utils import bulk_utils
    original = bulk_utils.add_nodes_and_edges_bulk_tx

    async def patched(*args, **kwargs):
        for seq in list(args) + list(kwargs.values()):
            if isinstance(seq, list):
                for item in seq:
                    attrs = getattr(item, 'attributes', None)
                    if isinstance(attrs, dict) and attrs:
                        item.attributes = {k: _flatten_value(val) for k, val in attrs.items()}
        return await original(*args, **kwargs)

    bulk_utils.add_nodes_and_edges_bulk_tx = patched


_apply_flatten_patch()


# ---------- cliente LLM tolerante ----------
class TolerantJSONClient(OpenAIGenericClient):
    """DeepSeek a veces devuelve el JSON seguido de texto extra ("Extra data") o envuelto en ```json.
    Graphiti pasa cada respuesta por _strip_code_fences antes de json.loads: aquí además se conserva solo el primer
    valor JSON completo. Si el JSON viene cortado, se devuelve tal cual y Graphiti reintenta."""

    THINKING_OFF = os.environ.get('GRAPH_LLM_THINKING', 'disabled') == 'disabled'
    DATA_ONLY_NOTE = ('\n\nIMPORTANTE: responde únicamente con un objeto JSON que contenga los DATOS pedidos (una '
                      'instancia que cumpla el formato). Nunca devuelvas el esquema JSON ni claves como "$defs", '
                      '"properties" o "type" que describan el formato.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original_create = self.client.chat.completions.create

        async def create(*a, **kw):
            # Deepseek-flash razona por defecto y ese razonamiento se cobra
            # como salida sin aportar nada a una extracción estructurada. Se apaga.
            if self.THINKING_OFF:
                extra = dict(kw.pop('extra_body', None) or {})
                extra.setdefault('thinking', {'type': 'disabled'})
                kw['extra_body'] = extra
            # DeepSeek recomienda dar un ejemplo y no el esquema; Graphiti manda el esquema y el modelo a veces lo imita
            msgs = [dict(m) for m in kw.get('messages', [])]
            for m in msgs:
                if m.get('role') == 'system':
                    m['content'] = (m.get('content') or '') + self.DATA_ONLY_NOTE
                    break
            else:
                msgs.insert(0, {'role': 'system', 'content': self.DATA_ONLY_NOTE.strip()})
            kw['messages'] = msgs
            resp = await original_create(*a, **kw)
            _note_response(resp, kw.get('model'))
            return resp

        self.client.chat.completions.create = create

    def _strip_code_fences(self, text):  # noqa: D401
        cleaned = super()._strip_code_fences(text) if hasattr(super(), '_strip_code_fences') else text
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned or '').strip()
        start = min([i for i in (cleaned.find('{'), cleaned.find('[')) if i >= 0], default=-1)
        if start < 0:
            return cleaned
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            return cleaned
        # DeepSeek a veces imita la plantilla del esquema pedido. Dos casos:
        #  a) {"properties": {<datos reales>}} -> se desenvuelven los datos;
        #  b) la plantilla vacía ($defs, properties con "type") -> error para que Graphiti reintente.
        schema_keys = {'$defs', 'properties', 'required', 'type', 'title', 'description', 'additionalProperties'}
        if isinstance(value, dict) and isinstance(value.get('properties'), dict) and set(value) <= schema_keys:
            props = value['properties']
            looks_like_schema = bool(props) and all(
                isinstance(v, dict) and ({'type', '$ref', 'items', 'anyOf', 'allOf'} & set(v)) for v in props.values())
            if looks_like_schema or '$defs' in value:
                raise ValueError('el modelo devolvió el esquema JSON en lugar de datos')
            value = props
        elif isinstance(value, dict) and '$defs' in value:
            raise ValueError('el modelo devolvió el esquema JSON en lugar de datos')
        return json.dumps(value, ensure_ascii=False)


# ---------- utilidades ----------
def _driver(gid: str):
    """FalkorDB guarda cada group_id en su propia base de grafos: se usa un driver apuntando a esa base."""
    return base_driver.clone(database=gid) if hasattr(base_driver, 'clone') else base_driver


def _safe_ident(name: str, fallback: str) -> str:
    ident = re.sub(r'\W', '_', name or '')
    if not ident or ident[0].isdigit():
        ident = f'{fallback}_{ident}'
    return ident


def _attr_fields(attrs: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields = {}
    for a in attrs or []:
        n = a.get('name') if isinstance(a, dict) else str(a)
        if not n:
            continue
        n = _safe_ident(n, 'attr')
        if n.lower() in PROTECTED:
            n = f'entity_{n}'
        desc = (a.get('description') if isinstance(a, dict) else '') or n
        fields[n] = (Optional[str], Field(default=None, description=desc))
    return fields


def _build_types(ontology: Dict[str, Any]):
    entity_types, edge_types, edge_map = {}, {}, {}
    for e in ontology.get('entity_types', []) or []:
        name = e.get('name')
        if not name or name == 'Entity':
            continue
        model = create_model(_safe_ident(name, 'Tipo'), __base__=BaseModel, **_attr_fields(e.get('attributes')))
        model.__doc__ = e.get('description') or f'A {name}.'
        entity_types[name] = model
    for r in ontology.get('edge_types', []) or []:
        name = r.get('name')
        if not name:
            continue
        model = create_model(_safe_ident(name, 'Rel'), __base__=BaseModel, **_attr_fields(r.get('attributes')))
        model.__doc__ = r.get('description') or f'{name} relationship.'
        edge_types[name] = model
        for st in r.get('source_targets', []) or []:
            key = (st.get('source') or 'Entity', st.get('target') or 'Entity')
            edge_map.setdefault(key, []).append(name)
    return entity_types, edge_types, (edge_map or None)


def _ontology_path(gid: str) -> str:
    return os.path.join(DATA_DIR, 'ontologies', f'{_safe_ident(gid, "g")}.json')


def _load_ontology(gid: str) -> Dict[str, Any]:
    if gid not in ontologies:
        try:
            with open(_ontology_path(gid), encoding='utf-8') as f:
                ontologies[gid] = json.load(f)
        except FileNotFoundError:
            ontologies[gid] = {}
    return ontologies[gid]


def _iso(v):
    return v.isoformat() if hasattr(v, 'isoformat') else v


def _node_json(n: EntityNode) -> Dict[str, Any]:
    return {'uuid': n.uuid, 'name': n.name, 'labels': list(n.labels or []), 'summary': n.summary or '',
            'attributes': n.attributes or {}, 'created_at': _iso(n.created_at), 'group_id': n.group_id}


def _edge_json(e: EntityEdge) -> Dict[str, Any]:
    return {'uuid': e.uuid, 'name': e.name, 'fact': e.fact, 'source_node_uuid': e.source_node_uuid,
            'target_node_uuid': e.target_node_uuid, 'attributes': e.attributes or {}, 'episodes': list(e.episodes or []),
            'created_at': _iso(e.created_at), 'valid_at': _iso(e.valid_at), 'invalid_at': _iso(e.invalid_at),
            'expired_at': _iso(e.expired_at), 'group_id': e.group_id}


# ---------- arranque y trabajador ----------
@app.on_event('startup')
async def startup():
    global graphiti, base_driver
    os.makedirs(os.path.join(DATA_DIR, 'ontologies'), exist_ok=True)
    # Grupo de conexiones con tope que ESPERA cuando se llena (antes: "Too many connections" con 20 consultas
    # concurrentes por lote y más de 100 clientes abiertos). Mismo cliente para todos los grupos (clone lo reutiliza).
    import redis.asyncio as aredis
    from falkordb.asyncio import FalkorDB
    # Mismas opciones que usa FalkorDB al crear su propia conexión (sin decode_responses las filas no traen sus campos)
    pool = aredis.BlockingConnectionPool(host=FALKOR_HOST, port=FALKOR_PORT, max_connections=MAX_CONNECTIONS, timeout=120,
                                         decode_responses=True, encoding='utf-8', protocol=2,
                                         # La deduplicación de Graphiti usa CPU y frena el bucle: abrir conexión puede tardar
                                         socket_connect_timeout=30, socket_keepalive=True,
                                         # redis-py 8 trae socket_timeout=5 s por defecto: insuficiente mientras Graphiti
                                         # deduplica (bucle ocupado) y daba "Timeout reading". FalkorDB lo anulaba en su cliente.
                                         socket_timeout=120)
    base_driver = FalkorDriver(falkor_db=FalkorDB(connection_pool=pool), database='mirofish')
    llm = TolerantJSONClient(
        config=LLMConfig(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, model=LLM_MODEL, small_model=LLM_MODEL,
                         temperature=0.2, max_tokens=8000),
        structured_output_mode='json_object', max_tokens=8000)
    embedder = _MeteredEmbedder(config=OpenAIEmbedderConfig(api_key=EMB_API_KEY, embedding_model=EMB_MODEL,
                                                          base_url='https://api.openai.com/v1'))
    # Reordenador solo para cumplir el constructor: las búsquedas usan RRF y no lo llaman
    reranker = OpenAIRerankerClient(config=LLMConfig(api_key=EMB_API_KEY, base_url='https://api.openai.com/v1'))
    graphiti = Graphiti(graph_driver=base_driver, llm_client=llm, embedder=embedder, cross_encoder=reranker,
                        max_coroutines=MAX_COROUTINES)
    await graphiti.build_indices_and_constraints()
    asyncio.create_task(_worker())
    log.info('Servicio de grafo listo (FalkorDB %s:%s, LLM %s, vectores %s)', FALKOR_HOST, FALKOR_PORT, LLM_MODEL, EMB_MODEL)


class _MeteredEmbedder(OpenAIEmbedder):
    """Embedder de Graphiti que además anota los tokens de vectores del grafo en curso."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original = self.client.embeddings.create

        async def create(*a, **kw):
            resp = await original(*a, **kw)
            _note_usage(kw.get('model') or EMB_MODEL, getattr(getattr(resp, 'usage', None), 'total_tokens', 0))
            return resp

        self.client.embeddings.create = create


async def _worker():
    while True:
        gid, items = await queue.get()
        # Agrupación (11-22): en una simulación llegan muchos episodios sueltos (uno por cada 5 acciones); extraerlos
        # de uno en uno dejaba al grafo minutos atrás de la conversación. Se juntan los que esperan del mismo grafo
        # (hasta 3 bloques) y se extraen en bloques de BULK_SIZE: casi el mismo tiempo que uno solo.
        items = list(items)
        otros = []
        while len(items) < BULK_SIZE * 3:
            try:
                g2, it2 = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            queue.task_done()
            if g2 == gid:
                items.extend(it2)
            else:
                otros.append((g2, it2))
        for o in otros:
            queue.put_nowait(o)
        CURRENT_GID.set(gid)  # las tareas de Graphiti creadas aquí heredan el grafo para anotar su consumo
        try:
            onto = _load_ontology(gid)
            entity_types, edge_types, edge_map = _build_types(onto)
            for i in range(0, len(items), BULK_SIZE):
                chunk = items[i:i + BULK_SIZE]
                # Sin uuid: en Graphiti, RawEpisode.uuid significa "episodio ya existente" (NodeNotFoundError si no está)
                raws = [RawEpisode(name=f'{gid}-{eid[:8]}', content=text, source=EpisodeType.text,
                                   source_description='MiroFish', reference_time=datetime.now(timezone.utc))
                        for eid, text in chunk]
                try:
                    try:
                        await graphiti.add_episode_bulk(raws, group_id=gid, entity_types=entity_types or None,
                                                        edge_types=edge_types or None, edge_type_map=edge_map)
                    except Exception as first_exc:  # noqa: BLE001
                        # Un reintento por lote: los fallos vistos son transitorios (respuesta mal formada del modelo,
                        # conexión que no abrió a tiempo mientras Graphiti calculaba)
                        log.warning('Reintentando lote de %s tras: %s', gid, str(first_exc)[:200])
                        await asyncio.sleep(5)
                        await graphiti.add_episode_bulk(raws, group_id=gid, entity_types=entity_types or None,
                                                        edge_types=edge_types or None, edge_type_map=edge_map)
                    for eid, _ in chunk:
                        episodes[eid].update(processed=True, finished_at=datetime.now(timezone.utc).isoformat())
                    log.info('Lote listo: %d episodios de %s (%d/%d)', len(chunk), gid, min(i + BULK_SIZE, len(items)), len(items))
                except Exception as exc:  # noqa: BLE001
                    # El lote falló dos veces: se procesa fragmento por fragmento (add_episode) para aislar el
                    # problemático; el camino individual usa otras instrucciones y suele salvar el resto del lote.
                    log.warning('Lote de %s falló dos veces (%s); procesando %d fragmentos uno por uno',
                                gid, str(exc)[:160], len(chunk))
                    for eid, text in chunk:
                        last_err = None
                        for attempt in range(2):
                            try:
                                await graphiti.add_episode(
                                    name=f'{gid}-{eid[:8]}', episode_body=text, source=EpisodeType.text,
                                    source_description='MiroFish', reference_time=datetime.now(timezone.utc),
                                    group_id=gid, entity_types=entity_types or None, edge_types=edge_types or None,
                                    edge_type_map=edge_map)
                                last_err = None
                                break
                            except Exception as single_exc:  # noqa: BLE001
                                last_err = single_exc
                                await asyncio.sleep(3)
                        if last_err is None:
                            episodes[eid].update(processed=True, finished_at=datetime.now(timezone.utc).isoformat())
                        else:
                            log.error('Fragmento %s de %s falló también solo: %s', eid[:8], gid, str(last_err)[:200])
                            episodes[eid].update(processed=True, error=str(last_err)[:500])
        finally:
            queue.task_done()


# ---------- API ----------
class GraphIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class OntologyIn(BaseModel):
    ontology: Dict[str, Any]


class EpisodeIn(BaseModel):
    data: str
    type: str = 'text'


class EpisodesIn(BaseModel):
    episodes: List[EpisodeIn]


class SearchIn(BaseModel):
    query: str
    scope: str = 'edges'
    limit: int = 10


@app.get('/health')
async def health():
    return {'status': 'ok', 'queue': queue.qsize()}


@app.post('/graphs/{gid}')
async def create_graph(gid: str, body: GraphIn):
    return {'graph_id': gid}  # Graphiti crea la base al primer episodio


@app.delete('/graphs/{gid}')
async def delete_graph(gid: str):
    await clear_data(_driver(gid), group_ids=[gid])
    ontologies.pop(gid, None)
    try:
        os.remove(_ontology_path(gid))
    except FileNotFoundError:
        pass
    return {'deleted': gid}


@app.put('/graphs/{gid}/ontology')
async def set_ontology(gid: str, body: OntologyIn):
    _build_types(body.ontology)  # valida antes de guardar
    ontologies[gid] = body.ontology
    with open(_ontology_path(gid), 'w', encoding='utf-8') as f:
        json.dump(body.ontology, f, ensure_ascii=False)
    return {'graph_id': gid, 'entity_types': len(body.ontology.get('entity_types', []) or []),
            'edge_types': len(body.ontology.get('edge_types', []) or [])}


@app.post('/graphs/{gid}/episodes')
async def add_episodes(gid: str, body: EpisodesIn):
    out, items = [], []
    for ep in body.episodes:
        eid = str(uuidlib.uuid4())
        episodes[eid] = {'uuid': eid, 'graph_id': gid, 'processed': False, 'error': None,
                         'created_at': datetime.now(timezone.utc).isoformat()}
        items.append((eid, ep.data))
        out.append({'uuid': eid, 'processed': False})
    await queue.put((gid, items))
    return {'episodes': out}


@app.get('/episodes/{eid}')
async def episode_status(eid: str):
    ep = episodes.get(eid)
    if not ep:
        raise HTTPException(404, 'episodio no encontrado (¿se reinició el servicio?)')
    return ep


@app.get('/graphs/{gid}')
async def get_graph(gid: str):
    graphs = await _list_graphs()
    if gid not in graphs and gid not in ontologies and not os.path.exists(_ontology_path(gid)):
        raise HTTPException(404, 'grafo no encontrado')
    return {'graph_id': gid}


@app.get('/graphs/{gid}/nodes')
async def list_nodes(gid: str, limit: Optional[int] = None, cursor: Optional[str] = None):
    nodes = await EntityNode.get_by_group_ids(_driver(gid), [gid], limit=limit, uuid_cursor=cursor)
    for n in nodes:
        node_graph[n.uuid] = gid
    return {'nodes': [_node_json(n) for n in nodes]}


@app.get('/graphs/{gid}/edges')
async def list_edges(gid: str, limit: Optional[int] = None, cursor: Optional[str] = None):
    edges = await EntityEdge.get_by_group_ids(_driver(gid), [gid], limit=limit, uuid_cursor=cursor)
    return {'edges': [_edge_json(e) for e in edges]}


@app.get('/graphs/{gid}/nodes/{nid}')
async def get_node(gid: str, nid: str):
    try:
        return _node_json(await EntityNode.get_by_uuid(_driver(gid), nid))
    except Exception:  # noqa: BLE001
        raise HTTPException(404, 'nodo no encontrado')


@app.get('/graphs/{gid}/nodes/{nid}/edges')
async def node_edges(gid: str, nid: str):
    edges = await EntityEdge.get_by_node_uuid(_driver(gid), nid)
    return {'edges': [_edge_json(e) for e in edges]}


async def _list_graphs() -> List[str]:
    try:
        return list(await base_driver.client.list_graphs())
    except Exception:  # noqa: BLE001
        return []


async def _find_node(nid: str):
    """Zep usa uuids globales: se busca el nodo en el grafo conocido o, si no, en todos."""
    candidates = ([node_graph[nid]] if nid in node_graph else []) + [g for g in await _list_graphs() if g != node_graph.get(nid)]
    for gid in candidates:
        try:
            node = await EntityNode.get_by_uuid(_driver(gid), nid)
            node_graph[nid] = gid
            return gid, node
        except Exception:  # noqa: BLE001
            continue
    raise HTTPException(404, 'nodo no encontrado')


@app.get('/nodes/{nid}')
async def get_node_global(nid: str):
    _, node = await _find_node(nid)
    return _node_json(node)


@app.get('/nodes/{nid}/edges')
async def node_edges_global(nid: str):
    gid, _ = await _find_node(nid)
    edges = await EntityEdge.get_by_node_uuid(_driver(gid), nid)
    return {'edges': [_edge_json(e) for e in edges]}


@app.get('/graphs/{gid}/usage')
async def graph_usage(gid: str, reset: int = 0):
    recs = USAGE.pop(gid, []) if reset else list(USAGE.get(gid, []))
    return {'graph_id': gid, 'usage': recs}


@app.post('/graphs/{gid}/search')
async def search(gid: str, body: SearchIn):
    if body.scope == 'nodes':
        cfg = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        cfg.limit = body.limit
        res = await graphiti.search_(body.query, config=cfg, group_ids=[gid], driver=_driver(gid))
        return {'nodes': [_node_json(n) for n in res.nodes], 'edges': []}
    cfg = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
    cfg.limit = body.limit
    res = await graphiti.search_(body.query, config=cfg, group_ids=[gid], driver=_driver(gid))
    return {'edges': [_edge_json(e) for e in res.edges], 'nodes': []}
