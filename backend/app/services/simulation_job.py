"""
Trabajo de simulación que ejecuta el worker (F3b, fork MX, 2026-11-22).

Antes /api/simulation/start lanzaba el subproceso de OASIS desde la API, y un deploy o reinicio de la API lo mataba.
Ahora la API encola y aquí, en el worker:
  1. se arranca el subproceso con el mismo SimulationRunner.start_simulation de siempre (estado en run_state.json,
     que la API relee en cada consulta);
  2. se vigila hasta que el monitor del runner termina (fin de rondas + cierre del entorno, o detener);
  3. se atienden las peticiones de detener que la API deja como archivo (stop_request.json);
  4. si las rondas terminaron y el entorno queda esperando entrevistas sin actividad más de SIM_ENV_IDLE_MIN minutos
     (30 por defecto), se cierra solo: así una simulación abandonada no ocupa un lugar del worker para siempre.

Entrevistas y close-env siguen por IPC de archivos (el subproceso los lee del volumen compartido), sin cambios.
"""
from __future__ import annotations

import os
import time

from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from .simulation_runner import RunnerStatus, SimulationRunner, SimulationStopPending

log = get_logger('mirofish.simulation_job')

ENV_IDLE_S = int(os.environ.get('SIM_ENV_IDLE_MIN', '30')) * 60
PROGRESS_EVERY_S = 10


def _rounds_done(state) -> bool:
    """Rondas terminadas en TODAS las plataformas activas (antes bastaba con una: en paralelo, la orden de cerrar
    llegaba mientras la otra seguía y se perdía)."""
    d = state.to_dict()
    activas = [p for p in ('twitter', 'reddit') if d.get(f'{p}_running') or d.get(f'{p}_completed')
               or d.get(f'{p}_current_round')]
    if not activas:
        return False
    return all(d.get(f'{p}_completed') for p in activas)


def _last_ipc_activity(simulation_id: str) -> float:
    sim_dir = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)
    stamps = []
    for name in ('ipc_commands', 'ipc_responses'):
        path = os.path.join(sim_dir, name)
        try:
            stamps.append(os.path.getmtime(path))
        except OSError:
            pass
    return max(stamps) if stamps else 0.0


def run_simulation_job(task_id: str, params: dict, current_locale: str) -> None:
    tm = TaskManager()
    simulation_id = params['simulation_id']
    tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=0, message='Iniciando la simulación')
    try:
        SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=params.get('platform') or 'parallel',
            max_rounds=params.get('max_rounds'),
            enable_graph_memory_update=bool(params.get('enable_graph_memory_update')),
            graph_id=params.get('graph_id'),
        )
    except Exception as exc:  # noqa: BLE001
        log.error(f'No arrancó la simulación {simulation_id}: {exc}')
        tm.fail_task(task_id, str(exc))
        return

    last_progress = 0.0
    rounds_done_at = None
    last_close = 0.0
    while True:
        if SimulationRunner.take_stop_request(simulation_id):
            log.info(f'Petición de detener {simulation_id} recibida desde la API')
            try:
                SimulationRunner.stop_simulation(simulation_id)
            except SimulationStopPending:
                pass  # el monitor terminará de cerrar; el bucle sigue hasta que el hilo acabe
            except Exception as exc:  # noqa: BLE001
                log.error(f'Detener {simulation_id} falló: {exc}')

        monitor = SimulationRunner._monitor_threads.get(simulation_id)
        if monitor is None or not monitor.is_alive():
            break

        now = time.time()
        state = SimulationRunner.get_run_state(simulation_id)
        if state is not None:
            if now - last_progress >= PROGRESS_EVERY_S:
                d = state.to_dict()
                tm.update_task(
                    task_id,
                    progress=int(d.get('progress_percent') or 0),
                    message=(f"Ronda {d.get('current_round', 0)}/{d.get('total_rounds', 0)} · "
                             f"{d.get('total_actions_count', 0)} acciones"),
                )
                last_progress = now
            if _rounds_done(state):
                rounds_done_at = rounds_done_at or now
                idle_since = max(rounds_done_at, _last_ipc_activity(simulation_id))
                # En el recorrido completo (pipeline) no se esperan entrevistas: se cierra al terminar las rondas
                limite = 0 if params.get('cerrar_al_terminar') else ENV_IDLE_S
                # La orden se reenvía cada 30 s: si llega antes de que el proceso entre en modo espera, se pierde
                if now - idle_since > limite and now - last_close >= 30:
                    motivo = 'recorrido completo' if params.get('cerrar_al_terminar') else f'{ENV_IDLE_S // 60} min sin actividad'
                    log.info(f'{simulation_id}: rondas terminadas ({motivo}); se cierra el entorno')
                    last_close = now
                    try:
                        SimulationRunner.close_simulation_env(simulation_id, timeout=60)
                    except Exception as exc:  # noqa: BLE001
                        log.error(f'Cierre automático de {simulation_id} falló: {exc}')
        time.sleep(2)

    final = SimulationRunner.get_run_state(simulation_id)
    status = final.runner_status if final else None
    if status in (RunnerStatus.COMPLETED, RunnerStatus.STOPPED):
        tm.complete_task(task_id, result=final.to_dict())
    else:
        tm.fail_task(task_id, (final.error if final and final.error else None)
                     or f'La simulación terminó en estado {status}')
