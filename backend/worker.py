"""
Worker de MiroFish (F3, fork MX, 2026-11-22).

Ejecuta los trabajos largos que antes corrían en hilos dentro de la API: construir el grafo, preparar la simulación y
generar el informe. La API solo encola (tabla `trabajos` en Postgres); así un deploy o reinicio de la API ya no mata
una corrida, y las corridas van en fila con un tope de concurrencia (WORKER_CONCURRENCY, 2 por defecto).

Si el propio worker se reinicia a mitad de un trabajo, al volver (o en la revisión de cada minuto) ese trabajo se
marca como interrumpido —tarea fallida y proyecto/simulación/informe en estado de error— en vez de quedar "en curso"
para siempre.

Arranque: `cd backend && uv run python worker.py` (servicio `worker` del stack, misma imagen que la API).
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from app.config import Config  # noqa: F401  (carga el .env)
from app.models.task import TaskManager
from app.utils.db import ensure_schema
from app.utils.locale import set_locale, t
from app.utils.logger import get_logger

log = get_logger('mirofish.worker')

CONCURRENCY = max(1, int(os.environ.get('WORKER_CONCURRENCY', '2')))          # grafo, preparación, informe
SIM_CONCURRENCY = max(1, int(os.environ.get('WORKER_SIM_CONCURRENCY', '2')))  # simulaciones (F3b)
SIM_KINDS = {'simulation_run', 'pipeline'}
HEARTBEAT_S = 20
STALE_S = 90
POLL_S = 2
WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"

_stop = threading.Event()
_running: dict[str, str] = {}  # task_id -> kind
_running_lock = threading.Lock()


def _handlers():
    # Import diferido: estos módulos arrastran la app completa
    from app.api.graph import run_graph_build_job
    from app.api.simulation import run_prepare_job
    from app.api.report import run_report_job
    from app.services.simulation_job import run_simulation_job
    from app.services.pipeline import run_pipeline_job
    return {
        'pipeline': run_pipeline_job,
        'simulation_run': run_simulation_job,
        'graph_build': run_graph_build_job,
        'simulation_prepare': run_prepare_job,
        'report_generate': run_report_job,
    }


# ---------- trabajos interrumpidos ----------
def _mark_interrupted(job: dict) -> None:
    task_id, kind, params = job['task_id'], job['kind'], job['params'] or {}
    msg = 'Interrumpido: el worker se reinició mientras corría este trabajo. Vuelve a lanzarlo.'
    tm = TaskManager()
    tm.fail_task(task_id, msg)
    try:
        if kind == 'graph_build':
            from app.models.project import ProjectManager, ProjectStatus
            p = ProjectManager.get_project(params.get('project_id'))
            if p and p.status == ProjectStatus.GRAPH_BUILDING:
                p.status = ProjectStatus.FAILED
                p.error = msg
                ProjectManager.save_project(p)
        elif kind == 'simulation_prepare':
            from app.services.simulation_manager import SimulationManager, SimulationStatus
            m = SimulationManager()
            st = m.get_simulation(params.get('simulation_id'))
            if st and st.status == SimulationStatus.PREPARING:
                st.status = SimulationStatus.FAILED
                st.error = msg
                m._save_simulation_state(st)
        elif kind == 'simulation_run':
            # El subproceso murió con el contenedor del worker: el estado queda en error, no "corriendo"
            from app.services.simulation_runner import RunnerStatus, SimulationRunner
            sid = params.get('simulation_id')
            st = SimulationRunner._load_run_state(sid)
            if st and st.runner_status in (RunnerStatus.STARTING, RunnerStatus.RUNNING, RunnerStatus.PAUSED,
                                           RunnerStatus.STOPPING):
                st.runner_status = RunnerStatus.FAILED
                st.twitter_running = False
                st.reddit_running = False
                st.error = msg
                SimulationRunner._save_run_state(st)
                SimulationRunner._sync_simulation_status(sid, RunnerStatus.FAILED, msg)
        elif kind == 'report_generate':
            from app.utils.zep_lifecycle import unregister_graph_reader
            from app.services.report_agent import ReportManager, ReportStatus
            unregister_graph_reader(params.get('graph_id'), params.get('report_id'))
            r = ReportManager.get_report(params.get('report_id'))
            if r and r.status not in (ReportStatus.COMPLETED, ReportStatus.FAILED):
                r.status = ReportStatus.FAILED
                r.error = msg
                ReportManager.save_report(r)
    except Exception:  # noqa: BLE001
        log.error(f'No se pudo marcar el estado de {kind} {task_id} como interrumpido:\n{traceback.format_exc()}')
    log.warning(f'Trabajo interrumpido marcado como fallido: {kind} {task_id} (worker anterior: {job.get("worker")})')


def _recover_orphans() -> None:
    try:
        for job in TaskManager().find_orphans(STALE_S):
            with _running_lock:
                if job['task_id'] in _running:
                    continue
            _mark_interrupted(job)
    except Exception:  # noqa: BLE001
        log.error(f'Revisión de trabajos interrumpidos falló:\n{traceback.format_exc()}')


# ---------- ejecución ----------
def _charge_job(kind, task_id, params, meter, since):
    from app.services.job_billing import charge_job
    charge_job(kind, task_id, params, meter, since)


def _run(job: dict, handlers: dict) -> None:
    task_id, kind = job['task_id'], job['kind']
    started = time.time()
    log.info(f'Inicia {kind} {task_id} (intento {job["intentos"]})')
    from app.services.credits import start_meter
    meter, _ = start_meter(kind)
    try:
        set_locale(job.get('locale') or 'es')
        fn = handlers.get(kind)
        if fn is None:
            TaskManager().fail_task(task_id, f'Tipo de trabajo desconocido: {kind}')
            return
        fn(task_id, job['params'] or {}, job.get('locale') or 'es')
    except Exception as exc:  # noqa: BLE001  (los cuerpos ya capturan sus errores; esto es la red de seguridad)
        log.error(f'{kind} {task_id} lanzó una excepción:\n{traceback.format_exc()}')
        try:
            TaskManager().fail_task(task_id, str(exc))
        except Exception:  # noqa: BLE001
            pass
    finally:
        _charge_job(kind, task_id, job['params'] or {}, meter, started)
        with _running_lock:
            _running.pop(task_id, None)
        log.info(f'Termina {kind} {task_id} en {int(time.time() - started)} s')


def _heartbeat_loop() -> None:
    last_recovery = 0.0
    while not _stop.wait(HEARTBEAT_S):
        with _running_lock:
            ids = list(_running)
        try:
            TaskManager().heartbeat(ids)
        except Exception:  # noqa: BLE001
            log.error(f'Latido falló:\n{traceback.format_exc()}')
        if time.time() - last_recovery > 60:
            _recover_orphans()
            last_recovery = time.time()


def main() -> None:
    ensure_schema()
    from app.services.credits import install_context_propagation
    install_context_propagation()  # los hilos de ThreadPoolExecutor heredan el medidor del trabajo
    handlers = _handlers()
    log.info(f'Worker {WORKER_NAME} listo: concurrencia {CONCURRENCY} (+{SIM_CONCURRENCY} simulaciones), '
             f'tipos {sorted(handlers)}')
    _recover_orphans()

    def _on_signal(signum, _frame):
        log.warning(f'Señal {signum}: deja de tomar trabajos nuevos')
        _stop.set()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    threading.Thread(target=_heartbeat_loop, daemon=True, name='latido').start()
    pool = ThreadPoolExecutor(max_workers=CONCURRENCY + SIM_CONCURRENCY, thread_name_prefix='trabajo')
    tm = TaskManager()
    llm_kinds = [k for k in handlers if k not in SIM_KINDS]
    sim_kinds = [k for k in handlers if k in SIM_KINDS]
    while not _stop.is_set():
        with _running_lock:
            busy_sim = sum(1 for k in _running.values() if k in SIM_KINDS)
            busy_llm = len(_running) - busy_sim
        job = None
        try:
            if busy_sim < SIM_CONCURRENCY:
                job = tm.claim_next(WORKER_NAME, sim_kinds)
            if job is None and busy_llm < CONCURRENCY:
                job = tm.claim_next(WORKER_NAME, llm_kinds)
        except Exception:  # noqa: BLE001
            log.error(f'No se pudo leer la cola:\n{traceback.format_exc()}')
            _stop.wait(10)
            continue
        if job:
            with _running_lock:
                _running[job['task_id']] = job['kind']
            pool.submit(_run, job, handlers)
            continue
        _stop.wait(POLL_S)
    log.info('Worker detenido; los trabajos en curso quedarán como interrumpidos si no terminan antes del cierre')
    pool.shutdown(wait=False, cancel_futures=True)


if __name__ == '__main__':
    main()
