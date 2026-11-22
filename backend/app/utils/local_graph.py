"""
Cliente del servicio de grafo propio (Graphiti + FalkorDB) con la misma forma que el cliente de Zep (fork MX).

Solo implementa lo que MiroFish usa de Zep:
  graph.create / get / delete / add / search, graph.episode.get, graph.node.get / get_edges,
  graph.node.with_raw_response.get_by_graph_id y graph.edge.with_raw_response.get_by_graph_id (paginación en una sola página),
  más dos operaciones propias: set_ontology_raw (ontología en JSON) y add_texts (alta de fragmentos en un solo llamado).

RoutingGraphClient permite convivir con Zep sin migrar nada:
  - grafos con prefijo LOCAL_PREFIX ("mfl_") van al servicio local; el resto (p. ej. "mirofish_...") a Zep Cloud;
  - consultas por uuid (nodo, aristas de un nodo, episodio) preguntan primero al servicio local y, si no existe, a Zep.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx

from .logger import get_logger

logger = get_logger('mirofish.local_graph')

LOCAL_PREFIX = 'mfl_'


class LocalGraphNotFound(Exception):
    """Equivalente local de zep_cloud.NotFoundError."""


def is_local_graph(graph_id: Optional[str]) -> bool:
    return bool(graph_id) and str(graph_id).startswith(LOCAL_PREFIX)


def _obj(d: Dict[str, Any]) -> SimpleNamespace:
    o = SimpleNamespace(**d)
    if 'uuid' in d:
        o.uuid_ = d['uuid']
    return o


class _Http:
    def __init__(self, base_url: str, timeout: float):
        self.base = base_url.rstrip('/')
        self.timeout = timeout

    def call(self, method: str, path: str, body: Any = None, params: Optional[dict] = None, retries: int = 3) -> Any:
        last = None
        for attempt in range(retries):
            try:
                r = httpx.request(method, self.base + path, json=body, params=params, timeout=self.timeout)
                if r.status_code == 404:
                    raise LocalGraphNotFound(f'{method} {path}: 404')
                r.raise_for_status()
                return r.json()
            except LocalGraphNotFound:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                time.sleep(1 + attempt * 2)
        raise last  # type: ignore[misc]


class _RawPage(SimpleNamespace):
    """Imita with_raw_response: .data con todos los elementos y sin cursor siguiente (una sola página)."""


class _NodeAPI:
    def __init__(self, http: _Http):
        self._h = http
        self.with_raw_response = SimpleNamespace(get_by_graph_id=self._raw_by_graph)

    def get(self, uuid_: str):
        return _obj(self._h.call('GET', f'/nodes/{uuid_}'))

    def get_edges(self, node_uuid: str):
        return [_obj(e) for e in self._h.call('GET', f'/nodes/{node_uuid}/edges')['edges']]

    def _raw_by_graph(self, graph_id: str, limit: int = 100, cursor: Optional[str] = None, **_):
        if cursor:
            return _RawPage(data=[], headers={})
        data = [_obj(n) for n in self._h.call('GET', f'/graphs/{graph_id}/nodes')['nodes']]
        return _RawPage(data=data, headers={})


class _EdgeAPI:
    def __init__(self, http: _Http):
        self._h = http
        self.with_raw_response = SimpleNamespace(get_by_graph_id=self._raw_by_graph)

    def _raw_by_graph(self, graph_id: str, limit: int = 100, cursor: Optional[str] = None, **_):
        if cursor:
            return _RawPage(data=[], headers={})
        data = [_obj(e) for e in self._h.call('GET', f'/graphs/{graph_id}/edges')['edges']]
        return _RawPage(data=data, headers={})


class _EpisodeAPI:
    def __init__(self, http: _Http):
        self._h = http

    def get(self, uuid_: str):
        d = self._h.call('GET', f'/episodes/{uuid_}')
        if d.get('error'):
            # Zep no expone errores por episodio; aquí se registra y se da por procesado para no colgar la espera
            logger.warning(f"Episodio {uuid_} terminó con error en el servicio de grafo: {d['error'][:200]}")
        return _obj(d)


class _GraphAPI:
    def __init__(self, http: _Http):
        self._h = http
        self.node = _NodeAPI(http)
        self.edge = _EdgeAPI(http)
        self.episode = _EpisodeAPI(http)

    def create(self, graph_id: str, name: Optional[str] = None, description: Optional[str] = None, **_):
        return _obj(self._h.call('POST', f'/graphs/{graph_id}', {'name': name, 'description': description}))

    def get(self, graph_id: str, **_):
        return _obj(self._h.call('GET', f'/graphs/{graph_id}'))

    def delete(self, graph_id: str, **_):
        return self._h.call('DELETE', f'/graphs/{graph_id}')

    def set_ontology_raw(self, graph_id: str, ontology: Dict[str, Any]):
        return self._h.call('PUT', f'/graphs/{graph_id}/ontology', {'ontology': ontology})

    def add(self, graph_id: str, data: str, type: str = 'text', **_):
        ep = self._h.call('POST', f'/graphs/{graph_id}/episodes', {'episodes': [{'data': data, 'type': type}]})['episodes'][0]
        return _obj(ep)

    def add_texts(self, graph_id: str, texts: List[str]) -> List[str]:
        eps = self._h.call('POST', f'/graphs/{graph_id}/episodes', {'episodes': [{'data': t} for t in texts]})['episodes']
        return [e['uuid'] for e in eps]

    def search(self, graph_id: str, query: str, limit: int = 10, scope: str = 'edges', **_):
        d = self._h.call('POST', f'/graphs/{graph_id}/search', {'query': query, 'scope': scope, 'limit': int(limit or 10)})
        return SimpleNamespace(edges=[_obj(e) for e in d.get('edges', [])],
                               nodes=[_obj(n) for n in d.get('nodes', [])], episodes=[], communities=[])


class LocalGraphClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.graph = _GraphAPI(_Http(base_url, timeout))
        self.is_local = True


# ---------- enrutador Zep / local ----------
class _RoutedNode:
    def __init__(self, local, zep):
        self._l, self._z = local, zep
        self.with_raw_response = SimpleNamespace(get_by_graph_id=self._raw_by_graph)

    def get(self, uuid_: str):
        try:
            return self._l.graph.node.get(uuid_)
        except LocalGraphNotFound:
            return self._z.graph.node.get(uuid_=uuid_)

    def get_edges(self, node_uuid: str):
        try:
            return self._l.graph.node.get_edges(node_uuid)
        except LocalGraphNotFound:
            return self._z.graph.node.get_edges(node_uuid=node_uuid)

    def _raw_by_graph(self, graph_id: str, **kw):
        if is_local_graph(graph_id):
            return self._l.graph.node.with_raw_response.get_by_graph_id(graph_id, **kw)
        return self._z.graph.node.with_raw_response.get_by_graph_id(graph_id, **kw)


class _RoutedEdge:
    def __init__(self, local, zep):
        self._l, self._z = local, zep
        self.with_raw_response = SimpleNamespace(get_by_graph_id=self._raw_by_graph)

    def _raw_by_graph(self, graph_id: str, **kw):
        if is_local_graph(graph_id):
            return self._l.graph.edge.with_raw_response.get_by_graph_id(graph_id, **kw)
        return self._z.graph.edge.with_raw_response.get_by_graph_id(graph_id, **kw)


class _RoutedEpisode:
    def __init__(self, local, zep):
        self._l, self._z = local, zep

    def get(self, uuid_: str):
        try:
            return self._l.graph.episode.get(uuid_)
        except LocalGraphNotFound:
            return self._z.graph.episode.get(uuid_=uuid_)


class _RoutedGraph:
    def __init__(self, local, zep):
        self._l, self._z = local, zep
        self.node = _RoutedNode(local, zep)
        self.edge = _RoutedEdge(local, zep)
        self.episode = _RoutedEpisode(local, zep)

    def _pick(self, graph_id):
        return self._l.graph if is_local_graph(graph_id) else self._z.graph

    def create(self, graph_id: str, **kw):
        return self._pick(graph_id).create(graph_id=graph_id, **kw)

    def get(self, graph_id: str, **kw):
        return self._pick(graph_id).get(graph_id, **kw)

    def delete(self, graph_id: str, **kw):
        return self._pick(graph_id).delete(graph_id=graph_id, **kw)

    def add(self, graph_id: str, **kw):
        return self._pick(graph_id).add(graph_id=graph_id, **kw)

    def search(self, graph_id: str, **kw):
        return self._pick(graph_id).search(graph_id=graph_id, **kw)

    def set_ontology_raw(self, graph_id: str, ontology: Dict[str, Any]):
        return self._l.graph.set_ontology_raw(graph_id, ontology)

    def add_texts(self, graph_id: str, texts: List[str]) -> List[str]:
        return self._l.graph.add_texts(graph_id, texts)

    def set_ontology(self, *a, **kw):  # firma de Zep: solo para grafos de Zep
        return self._z.graph.set_ontology(*a, **kw)


class RoutingGraphClient:
    """Cliente que reparte entre el servicio local (grafos mfl_*) y Zep Cloud (el resto)."""

    def __init__(self, local: LocalGraphClient, zep: Any):
        self._local = local
        self._zep = zep
        self.graph = _RoutedGraph(local, zep)
        self.is_local = True

    def __getattr__(self, name):  # p. ej. client.batch.* solo existe en Zep
        return getattr(self._zep, name)
