"""
Cobro de trabajos del worker (F4, fork MX 2026-11-22). Lo usan worker.py (un trabajo = un cobro) y el recorrido
completo `pipeline.py` (un cobro por paso).

- El dueño que paga es el del proyecto (no quien lanzó), salvo proyectos anteriores a F4 (sin dueño: no cobran).
- Además de lo que mide el proceso, se suma el consumo que ocurre fuera de él:
  * construcción del grafo: consumo del servicio de grafo para ese grafo;
  * simulación: `usage_llm.jsonl` del subproceso OASIS (agentes, vectores, entrevistas) y el crecimiento del grafo
    durante la simulación (memoria dinámica: cada lote de acciones se extrae con IA en el servicio de grafo).
"""
from __future__ import annotations

import json
import os
import traceback
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

log = get_logger('mirofish.cobro_trabajos')

TIPO_COBRO = {'graph_build': 'grafo_ia', 'simulation_prepare': 'perfiles_ia', 'simulation_run': 'simulacion_ia',
              'report_generate': 'informe_ia', 'illustration': 'ilustracion_ia'}


def job_owner(params: Dict[str, Any]) -> Optional[str]:
    from ..utils.account import owner_of_project, owner_of_simulation
    if params.get('project_id'):
        return owner_of_project(params['project_id'])[1]
    if params.get('simulation_id'):
        return owner_of_simulation(params['simulation_id'])[1]
    return None


def _graph_usage(graph_id: Optional[str]) -> List[Dict[str, Any]]:
    from ..utils.local_graph import is_local_graph
    if not is_local_graph(graph_id):
        return []
    import httpx
    from ..config import Config
    r = httpx.get(f"{Config.GRAPH_SERVICE_URL}/graphs/{graph_id}/usage", params={'reset': 1}, timeout=30)
    return r.json().get('usage', []) if r.status_code == 200 else []


def external_usage(kind: str, params: Dict[str, Any], since: float) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    try:
        if kind == 'graph_build':
            from ..models.project import ProjectManager
            p = ProjectManager.get_project(params['project_id'])
            recs += _graph_usage(p.graph_id if p else None)
        elif kind == 'simulation_run':
            from .simulation_manager import SimulationManager
            from .simulation_runner import SimulationRunner
            sid = params['simulation_id']
            path = os.path.join(SimulationRunner.RUN_STATE_DIR, sid, 'usage_llm.jsonl')
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get('ts', 0) >= since:
                            recs.append(rec)
            # Crecimiento del grafo durante la simulación (antes no se cobraba)
            st = SimulationManager().get_simulation(sid)
            recs += _graph_usage(st.graph_id if st else None)
    except Exception:  # noqa: BLE001
        log.error(f'No se pudo leer el consumo externo de {kind}:\n{traceback.format_exc()}')
    return recs


def charge_job(kind: str, ref: str, params: Dict[str, Any], meter, since: float) -> Optional[Dict[str, Any]]:
    try:
        from .credits import charge_meter
        meter.extend(external_usage(kind, params, since))
        owner = job_owner(params)
        res = charge_meter(owner, meter, TIPO_COBRO.get(kind, 'api_ia'), reference_id=ref, extra={'trabajo': kind})
        log.info(f'{kind} {ref}: costo real {meter.cost_usd():.4f} USD; dueño {owner}; cobro {res}')
        return res
    except Exception:  # noqa: BLE001
        log.error(f'No se pudo cobrar {kind} {ref}:\n{traceback.format_exc()}')
        return None
