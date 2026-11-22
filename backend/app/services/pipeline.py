"""
Recorrido completo "validar idea" en un solo trabajo del worker (fork MX).

El usuario sube su documento y su idea, confirma UNA vez (ciudad, ciudadanos, agentes, rondas, costo) y mira la
pantalla en vivo. Aquí, sin más clics:
  1. grafo de conocimiento (si el proyecto aún no lo tiene)
  2. simulación nueva
  3. preparación: actores del documento (máx. `max_agentes`) + ciudadanos del territorio (`n_ciudadanos`)
  4. simulación con memoria dinámica (el grafo CRECE en vivo con las acciones) y cierre del entorno al terminar
  5. informe
  6. ilustración (infografía), sin bloquear el resultado si falla

Cada paso reutiliza el mismo código que la mesa de trabajo y se cobra por separado (job_billing.charge_job con la
tarea del paso como referencia). El avance (fase, ids) queda en progress_detail de la tarea del recorrido: lo lee la
pantalla en vivo, y TaskManager.active_job_for lo usa para que detener/preparar reconozcan la simulación en curso.
Si el usuario cierra el navegador, el recorrido sigue; al volver, la pantalla en vivo retoma desde la tarea.
"""
from __future__ import annotations

import re
import threading
import time
import traceback
import uuid
from typing import Any, Dict

from ..config import Config
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from .credits import CURRENT_METER, start_meter
from .job_billing import charge_job
from .simulation_manager import SimulationManager, SimulationStatus

log = get_logger('mirofish.recorrido')

FASES = {'grafo': 'Construyendo el grafo de conocimiento de tu documento',
         'preparacion': 'Eligiendo ciudadanos y preparando a los agentes',
         'simulacion': 'La ciudad está conversando sobre tu idea',
         'informe': 'Redactando el informe de predicción',
         'ilustracion': 'Dibujando la infografía del resultado'}


def _limpiar_mensaje(msg: str) -> str:
    msg = re.sub(r'^\[[^\]]*\]\s*', '', (msg or '').strip())  # quita '[etapa] '
    return msg[:140]


def _espejo(task_id: str, sub_id: str, lo: int, hi: int, res: Dict[str, Any], alto: threading.Event) -> None:
    """Copia el avance del paso en curso al recorrido (lo + (hi-lo)*avance), para que la pantalla no parezca
    colgada en pasos largos como el informe (3-5 min)."""
    tm = TaskManager()
    ultimo = None
    while not alto.wait(4):
        try:
            t = tm.get_task(sub_id)
            if not t:
                continue
            pct = lo + int((hi - lo) * max(0, min(100, t.progress or 0)) / 100)
            msg = _limpiar_mensaje(t.message)
            if (pct, msg) != ultimo:
                tm.update_task(task_id, progress=pct, message=msg or FASES[res['fase']], progress_detail=dict(res))
                ultimo = (pct, msg)
        except Exception:  # noqa: BLE001
            pass


def _paso(kind: str, sub_id: str, params: Dict[str, Any], fn, locale: str, espejo=None) -> None:
    """Ejecuta el cuerpo de un trabajo en este mismo hilo con su propio medidor, y lo cobra.
    espejo = (task_id, lo, hi, res): copia el avance del paso al recorrido mientras corre."""
    meter, token = start_meter(kind)
    t0 = time.time()
    alto = threading.Event()
    if espejo:
        threading.Thread(target=_espejo, args=(espejo[0], sub_id, espejo[1], espejo[2], espejo[3], alto),
                         daemon=True).start()
    try:
        fn(sub_id, params, locale)
    finally:
        alto.set()
        CURRENT_METER.reset(token)
        charge_job(kind, sub_id, params, meter, t0)


def _exige_completado(sub_id: str, que: str) -> None:
    t = TaskManager().get_task(sub_id)
    if not t or t.status != TaskStatus.COMPLETED:
        raise RuntimeError(f'{que} no terminó: {(t.error or t.message) if t else "sin tarea"}')


def run_pipeline_job(task_id: str, params: Dict[str, Any], locale: str) -> None:
    from ..api.graph import run_graph_build_job
    from ..api.report import run_report_job
    from ..api.simulation import run_prepare_job
    from .simulation_job import run_simulation_job

    tm = TaskManager()
    pid = params['project_id']
    res: Dict[str, Any] = {'project_id': pid}

    def fase(nombre: str, pct: int) -> None:
        res['fase'] = nombre
        tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=pct, message=FASES[nombre],
                       progress_detail=dict(res))

    try:
        project = ProjectManager.get_project(pid)
        if not project:
            raise RuntimeError('Proyecto no encontrado')

        # 1) grafo
        if project.status != ProjectStatus.GRAPH_COMPLETED:
            fase('grafo', 3)
            sub = tm.create_task(f"构建图谱: {project.name}", metadata={'project_id': pid, 'recorrido': task_id})
            project.status = ProjectStatus.GRAPH_BUILDING
            project.graph_build_task_id = sub
            ProjectManager.save_project(project)
            _paso('graph_build', sub, {
                'project_id': pid, 'graph_name': project.name or 'MiroFish',
                'chunk_size': project.chunk_size or Config.DEFAULT_CHUNK_SIZE,
                'chunk_overlap': project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP,
                'resume_existing_batch': False}, run_graph_build_job, locale, espejo=(task_id, 3, 29, res))
            _exige_completado(sub, 'La construcción del grafo')
            project = ProjectManager.get_project(pid)
        res['graph_id'] = project.graph_id

        # 2) simulación nueva
        manager = SimulationManager()
        st = manager.create_simulation(project_id=pid, graph_id=project.graph_id, enable_twitter=True,
                                       enable_reddit=True)
        sid = st.simulation_id
        res['simulation_id'] = sid

        # 3) preparación (actores del documento + ciudadanos del territorio)
        fase('preparacion', 30)
        sub = tm.create_task(task_type='simulation_prepare',
                             metadata={'simulation_id': sid, 'project_id': pid, 'recorrido': task_id})
        st.status = SimulationStatus.PREPARING
        manager._save_simulation_state(st)
        _paso('simulation_prepare', sub, {
            'simulation_id': sid, 'entity_types': None, 'use_llm_for_profiles': True, 'parallel_profile_count': 5,
            'territorio': params.get('territorio'), 'n_ciudadanos': int(params.get('n_ciudadanos') or 0),
            'max_agentes': params.get('max_agentes')}, run_prepare_job, locale, espejo=(task_id, 30, 49, res))
        _exige_completado(sub, 'La preparación')

        # 4) simulación: el grafo crece en vivo (memoria dinámica) y el entorno se cierra al terminar las rondas
        fase('simulacion', 50)
        sub = tm.create_task(task_type='simulation_run',
                             metadata={'simulation_id': sid, 'project_id': pid, 'recorrido': task_id})
        _paso('simulation_run', sub, {
            'simulation_id': sid, 'platform': 'parallel', 'max_rounds': int(params.get('rondas') or 40),
            'enable_graph_memory_update': True, 'graph_id': project.graph_id, 'cerrar_al_terminar': True},
            run_simulation_job, locale)
        _exige_completado(sub, 'La simulación')

        # 5) informe
        fase('informe', 80)
        from ..utils.zep_lifecycle import register_graph_reader
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        res['report_id'] = report_id
        sub = tm.create_task(task_type='report_generate',
                             metadata={'simulation_id': sid, 'graph_id': project.graph_id, 'report_id': report_id,
                                       'recorrido': task_id})
        register_graph_reader(project.graph_id, report_id)
        _paso('report_generate', sub, {
            'graph_id': project.graph_id, 'simulation_id': sid,
            'simulation_requirement': project.simulation_requirement or '', 'report_id': report_id},
            run_report_job, locale, espejo=(task_id, 80, 94, res))
        _exige_completado(sub, 'El informe')

        # 6) infografía (si falla, el informe ya está listo)
        fase('ilustracion', 95)
        try:
            from .illustration import IllustrationService
            if IllustrationService.is_available():
                meter, token = start_meter('illustration')
                t0 = time.time()
                try:
                    IllustrationService.generate(report_id)
                finally:
                    CURRENT_METER.reset(token)
                    charge_job('illustration', f'{report_id}:ilustracion', {'project_id': pid}, meter, t0)
        except Exception as exc:  # noqa: BLE001
            log.warning(f'Infografía de {report_id} falló (el informe sigue disponible): {exc}')

        res['fase'] = 'listo'
        tm.update_task(task_id, progress_detail=dict(res))
        tm.complete_task(task_id, result=res)
    except Exception as exc:  # noqa: BLE001
        log.error(f'Recorrido {task_id} falló:\n{traceback.format_exc()}')
        res['error'] = str(exc)
        tm.update_task(task_id, progress_detail=dict(res))
        tm.fail_task(task_id, str(exc))
