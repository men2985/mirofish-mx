"""
任务状态管理
用于跟踪长时间运行的任务（如图谱构建）
"""

import uuid
import threading
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from ..utils.locale import t
from ..utils.db import connect


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"          # 等待中
    PROCESSING = "processing"    # 处理中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败


@dataclass
class Task:
    """任务数据类"""
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0              # 总进度百分比 0-100
    message: str = ""              # 状态消息
    result: Optional[Dict] = None  # 任务结果
    error: Optional[str] = None    # 错误信息
    metadata: Dict = field(default_factory=dict)  # 额外元数据
    progress_detail: Dict = field(default_factory=dict)  # 详细进度信息
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskManager:
    """
    任务管理器 — respaldado en Postgres (F3, parche MX 2026-11-22).

    Misma interfaz que la versión en memoria, pero el estado vive en la tabla `trabajos`: la API y el worker comparten
    las tareas y sobreviven a reinicios. Además expone la cola (enqueue/claim) que usa el worker.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    # ---------- conversión ----------
    @staticmethod
    def _row_to_task(r: Dict[str, Any]) -> Task:
        return Task(
            task_id=r['task_id'],
            task_type=r['task_type'],
            status=TaskStatus(r['status']),
            created_at=r['created_at'],
            updated_at=r['updated_at'],
            progress=r['progress'] or 0,
            message=r['message'] or '',
            result=r['result'],
            error=r['error'],
            metadata=r['metadata'] or {},
            progress_detail=r['progress_detail'] or {},
        )

    # ---------- interfaz original ----------
    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        task_id = str(uuid.uuid4())
        with connect() as c:
            c.execute(
                "INSERT INTO trabajos (task_id, task_type, metadata) VALUES (%s, %s, %s)",
                (task_id, task_type, Jsonb(metadata or {})),
            )
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        with connect() as c:
            r = c.execute("SELECT * FROM trabajos WHERE task_id = %s", (task_id,)).fetchone()
        return self._row_to_task(r) if r else None

    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None
    ):
        sets, vals = ["updated_at = now()"], []
        if status is not None:
            status = TaskStatus(status)
            sets.append("status = %s"); vals.append(status.value)
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                sets.append("finished_at = now()")
                sets.append("en_cola = false")
        if progress is not None:
            sets.append("progress = %s"); vals.append(int(progress))
        if message is not None:
            sets.append("message = %s"); vals.append(str(message))
        if result is not None:
            sets.append("result = %s"); vals.append(Jsonb(result))
        if error is not None:
            sets.append("error = %s"); vals.append(str(error))
        if progress_detail is not None:
            sets.append("progress_detail = %s"); vals.append(Jsonb(progress_detail))
        vals.append(task_id)
        with connect() as c:
            c.execute(f"UPDATE trabajos SET {', '.join(sets)} WHERE task_id = %s", vals)

    def complete_task(self, task_id: str, result: Dict):
        self.update_task(task_id, status=TaskStatus.COMPLETED, progress=100,
                         message=t('progress.taskComplete'), result=result)

    def fail_task(self, task_id: str, error: str):
        self.update_task(task_id, status=TaskStatus.FAILED, message=t('progress.taskFailed'), error=error)

    def list_tasks(self, task_type: Optional[str] = None) -> list:
        with connect() as c:
            if task_type:
                rows = c.execute("SELECT * FROM trabajos WHERE task_type = %s ORDER BY created_at DESC LIMIT 200",
                                 (task_type,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM trabajos ORDER BY created_at DESC LIMIT 200").fetchall()
        return [self._row_to_task(r).to_dict() for r in rows]

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        # Se conservan como historial (base para créditos/consumo en F4); no se borran.
        return None

    # ---------- cola (worker) ----------
    def enqueue(self, task_id: str, kind: str, params: Dict[str, Any], locale: Optional[str] = None) -> None:
        """Pone en cola una tarea ya creada para que la ejecute el worker."""
        with connect() as c:
            c.execute(
                "UPDATE trabajos SET kind = %s, params = %s, locale = %s, en_cola = true, updated_at = now() "
                "WHERE task_id = %s",
                (kind, Jsonb(params), locale, task_id),
            )

    def claim_next(self, worker: str, kinds: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """Toma el trabajo más antiguo en cola (FOR UPDATE SKIP LOCKED: dos workers nunca toman el mismo)."""
        with connect(transaction=True) as c:
            r = c.execute(
                "SELECT task_id FROM trabajos WHERE en_cola AND status = 'pending' "
                + ("AND kind = ANY(%s) " if kinds else "")
                + "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
                ((kinds,) if kinds else None),
            ).fetchone()
            if not r:
                return None
            return c.execute(
                "UPDATE trabajos SET en_cola = false, worker = %s, intentos = intentos + 1, started_at = now(), "
                "heartbeat_at = now(), updated_at = now() WHERE task_id = %s "
                "RETURNING task_id, kind, params, locale, intentos",
                (worker, r['task_id']),
            ).fetchone()

    def heartbeat(self, task_ids: list) -> None:
        if not task_ids:
            return
        with connect() as c:
            c.execute("UPDATE trabajos SET heartbeat_at = now() WHERE task_id = ANY(%s)", (list(task_ids),))

    def find_orphans(self, stale_seconds: int) -> list:
        """Trabajos de la cola que quedaron a medias: el worker que los tenía dejó de latir (reinicio, OOM, deploy)."""
        with connect() as c:
            return c.execute(
                "SELECT task_id, kind, params, worker FROM trabajos WHERE kind IS NOT NULL "
                "AND status IN ('pending', 'processing') AND NOT en_cola "
                "AND coalesce(heartbeat_at, updated_at) < now() - make_interval(secs => %s)",
                (stale_seconds,),
            ).fetchall()

    def active_job_for(self, kind: str, simulation_id: str, stale_seconds: int = 90,
                       queued_only: bool = False) -> Optional[str]:
        """task_id del trabajo vivo (en cola o con latido reciente) de ese tipo para esa simulación, o None."""
        with connect() as c:
            r = c.execute(
                "SELECT task_id FROM trabajos WHERE ((kind = %s AND params->>'simulation_id' = %s) OR "
                "(kind = 'pipeline' AND progress_detail->>'simulation_id' = %s AND progress_detail->>'fase' IN "
                "('preparacion', 'simulacion') AND (%s <> 'simulation_prepare' OR progress_detail->>'fase' = "
                "'preparacion'))) "
                "AND status IN ('pending', 'processing') AND (en_cola OR (NOT %s AND coalesce(heartbeat_at, "
                "updated_at) > now() - make_interval(secs => %s))) ORDER BY created_at DESC LIMIT 1",
                (kind, simulation_id, simulation_id, kind, queued_only, stale_seconds),
            ).fetchone()
        return r['task_id'] if r else None

    def queue_stats(self) -> Dict[str, Any]:
        with connect() as c:
            return c.execute(
                "SELECT count(*) FILTER (WHERE en_cola) AS en_cola, "
                "count(*) FILTER (WHERE kind IS NOT NULL AND NOT en_cola AND status IN ('pending','processing')) "
                "AS en_proceso FROM trabajos"
            ).fetchone()
