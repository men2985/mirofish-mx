"""
Cuentas y aislamiento por usuario (F4 paso 3, fork MX, 2026-11-22).

- El usuario llega en el encabezado X-Forwarded-User que pone Traefik desde el forwardAuth (servicio auth); Traefik lo
  sobrescribe en cada petición, así que el navegador no puede falsificarlo. Los datos de la cuenta (rol, tipo, saldo)
  viven en la tabla `users` del mismo Postgres (la crea y mantiene el servicio auth).
- Cada proyecto tiene `owner` (correo). Simulaciones, informes, grafos y tareas heredan el dueño de su proyecto.
- Los proyectos sin dueño (anteriores a F4) son del administrador.
- `guard_request` corre antes de cada petición a /api/ (salvo /api/public/): si la petición menciona un id que no es
  del usuario, responde 404 (no se revela que existe). El administrador ve todo.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from flask import g, jsonify, request

from .db import connect
from .logger import get_logger

log = get_logger('mirofish.cuentas')

_CACHE: Dict[str, Any] = {}
_TTL = 20
ID_KEYS = ('project_id', 'simulation_id', 'report_id', 'graph_id', 'task_id')


def _load_account(email: str) -> Optional[Dict[str, Any]]:
    hit = _CACHE.get(email)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    with connect() as c:
        row = c.execute("SELECT id, email, nombre, rol, account_type, saldo_usd, municipio_cve, alcance, radio_km "
                        "FROM users WHERE email = %s", (email,)).fetchone()
    _CACHE[email] = (time.time(), row)
    return row


def current_account() -> Optional[Dict[str, Any]]:
    if hasattr(g, 'account'):
        return g.account
    email = (request.headers.get('X-Forwarded-User') or '').strip().lower()
    g.account = _load_account(email) if email else None
    return g.account


def is_admin(acct: Optional[Dict[str, Any]] = None) -> bool:
    acct = acct if acct is not None else current_account()
    return bool(acct) and acct.get('rol') == 'admin'


def current_email() -> Optional[str]:
    acct = current_account()
    return acct['email'] if acct else None


def can_see(owner: Optional[str], acct: Optional[Dict[str, Any]] = None) -> bool:
    """Proyecto visible para la cuenta: el admin ve todo; los demás solo lo suyo (sin dueño = del admin)."""
    acct = acct if acct is not None else current_account()
    if not acct:
        return False
    if acct.get('rol') == 'admin':
        return True
    return bool(owner) and owner == acct['email']


# ---------- dueño de cada objeto ----------
def owner_of_project(project_id: str):
    from ..models.project import ProjectManager
    p = ProjectManager.get_project(project_id)
    return (True, p.owner) if p else (False, None)


def owner_of_simulation(simulation_id: str):
    from ..services.simulation_manager import SimulationManager
    st = SimulationManager().get_simulation(simulation_id)
    if not st:
        return False, None
    return owner_of_project(st.project_id)


def owner_of_report(report_id: str):
    from ..services.report_agent import ReportManager
    r = ReportManager.get_report(report_id)
    if not r:
        return False, None
    return owner_of_simulation(r.simulation_id)


def owner_of_graph(graph_id: str):
    from ..models.project import ProjectManager
    ps = ProjectManager.find_projects_by_graph_id(graph_id)
    if not ps:
        return False, None
    owners = {p.owner for p in ps}
    return True, owners.pop() if len(owners) == 1 else None


def owner_of_task(task_id: str):
    with connect() as c:
        r = c.execute("SELECT metadata, params FROM trabajos WHERE task_id = %s", (task_id,)).fetchone()
    if not r:
        return False, None
    ref = {**(r['params'] or {}), **(r['metadata'] or {})}
    if ref.get('project_id'):
        return owner_of_project(ref['project_id'])
    if ref.get('simulation_id'):
        return owner_of_simulation(ref['simulation_id'])
    return True, None


RESOLVERS = {'project_id': owner_of_project, 'simulation_id': owner_of_simulation, 'report_id': owner_of_report,
             'graph_id': owner_of_graph, 'task_id': owner_of_task}


def _ids_in_request() -> Dict[str, str]:
    found: Dict[str, str] = {}
    sources = [request.view_args or {}, request.args]
    body = request.get_json(silent=True) if request.is_json else None
    if isinstance(body, dict):
        sources.append(body)
    if request.form:
        sources.append(request.form)
    for src in sources:
        for k in ID_KEYS:
            v = src.get(k)
            if isinstance(v, str) and v and k not in found:
                found[k] = v
    return found


def territorio_de_cuenta(acct: Optional[Dict[str, Any]], alcance: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Territorio de la ciudad confirmada por el usuario (lo crea si no existe; sin IA). None si no confirmó ciudad."""
    if not acct or not acct.get('municipio_cve'):
        return None
    from ..services.citizen_creator import crear_territorio
    alc = alcance or acct.get('alcance') or 'zm'
    return crear_territorio(acct['municipio_cve'], alc, acct.get('radio_km') or 40)


# Acciones que gastan IA: con cuenta de créditos sin saldo se frenan antes de encolar
COSTLY_POST = ('/api/graph/ontology/generate', '/api/graph/build', '/api/simulation/prepare', '/api/simulation/start',
               '/api/simulation/validar', '/api/simulation/afinar',
               '/api/report/generate', '/api/simulation/interview', '/api/report/chat')


def _is_costly() -> bool:
    p = request.path or ''
    return request.method == 'POST' and (p.startswith(COSTLY_POST) or p.endswith('/illustration'))


def guard_request():
    """before_request de la app: 401 sin cuenta, 404 si toca algo de otro usuario, 402 sin saldo en acciones de IA.
    Además abre el medidor de consumo de la petición (créditos)."""
    path = request.path or ''
    if not path.startswith('/api/') or path.startswith('/api/public/'):
        return None
    acct = current_account()
    if not acct:
        return jsonify(success=False, error='Inicia sesión para continuar.'), 401
    from ..services.credits import start_meter
    g.meter, _ = start_meter(f'{request.method} {path}')
    # El saldo depende solo del tipo de cuenta: un admin en 'credit' también paga (p. ej. para probar el cobro)
    if _is_costly() and acct.get('account_type') != 'bypass' and float(acct.get('saldo_usd') or 0) <= 0:
        return jsonify(success=False, sin_saldo=True,
                       error='Tu saldo se agotó. Recarga créditos para seguir validando ideas.',
                       recargar='/_auth/creditos'), 402
    for key, value in _ids_in_request().items():
        try:
            exists, owner = RESOLVERS[key](value)
        except Exception as exc:  # noqa: BLE001
            log.warning(f'No se pudo resolver el dueño de {key}={value}: {exc}')
            exists, owner = True, None
        if exists and not can_see(owner, acct):
            log.warning(f"{acct['email']} intentó acceder a {key}={value} de otro dueño")
            return jsonify(success=False, error='No encontrado.'), 404
    return None


def charge_request(response):
    """after_request: cobra lo que la petición gastó en IA (p. ej. ontología, ilustración, entrevistas)."""
    meter = getattr(g, 'meter', None)
    acct = getattr(g, 'account', None)
    if meter is None or not acct or not meter.calls:
        return response
    try:
        from ..services.credits import charge_meter
        tipo = 'ilustracion_ia' if (request.path or '').endswith('/illustration') else 'api_ia'
        charge_meter(acct['email'], meter, tipo, extra={'ruta': f'{request.method} {request.path}'})
        _CACHE.pop(acct['email'], None)  # saldo nuevo en la siguiente petición
    except Exception as exc:  # noqa: BLE001
        log.error(f'No se pudo cobrar la petición {request.path}: {exc}')
    return response
