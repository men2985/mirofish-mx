"""
Publicaciones públicas de un informe (parche MX).

Una publicación es una copia congelada de un informe: titular, resumen para WhatsApp, puntos clave,
ilustración, vista previa ligera para redes y el grafo reducido a lo que se dibuja. Vive en
uploads/shares/<token>/ y se sirve sin login en /p/<token> y /api/public/<token>/*.

Por qué copia congelada y no lectura en vivo: la página pública no debe depender de Zep, ni del
proyecto original, ni cambiar si el informe se regenera; y el visitante no necesita nada del resto.

Base para multi-tenant: cada publicación guarda owner (usuario del login) y tenant (None por ahora).
Cuando exista la base de datos, share.json se vuelve una fila y los archivos pasan a MinIO.
"""

import json
import os
import secrets
import shutil
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction, t
from ..utils.logger import get_logger
from .illustration import IllustrationService
from .report_agent import ReportManager, ReportStatus

logger = get_logger('mirofish.share')

SHARES_DIR = os.path.join(os.path.dirname(__file__), '../../uploads/shares')
MAX_REPORT_CHARS = 14000
MAX_NODE_SUMMARY = 220
MAX_EDGE_FACT = 160

_lock = threading.Lock()

SUMMARY_SYSTEM = (
    "Eres editor de un boletín. Recibes el informe final de una simulación social con agentes. "
    "Escribe un resumen para compartir públicamente por WhatsApp. Reglas: no inventes datos que no estén "
    "en el informe; deja claro que es una simulación de escenarios, no un hecho; nada de lenguaje técnico "
    "(no menciones agentes, rondas, grafos ni modelos); sin emojis; sin hashtags. Devuelve SOLO JSON: "
    "{\"titular\": \"una frase de 8 a 14 palabras\", "
    "\"whatsapp\": \"2 o 3 frases, máximo 420 caracteres, que digan la conclusión principal\", "
    "\"puntos\": [\"4 o 5 hallazgos clave, cada uno de máximo 160 caracteres\"], "
    "\"tipos\": {\"NombreTipo\": \"nombre corto en plural y en el idioma pedido\"}}. "
    "En \"tipos\" traduce cada nombre de la lista de tipos de participantes que se te da (en CamelCase) a un "
    "nombre corto y natural en plural; \"Entity\" es la categoría genérica: tradúcela como \"Otros\". "
    "Añade también \"relaciones\": {\"NOMBRE_RELACION\": \"verbo corto en minúsculas\"} traduciendo cada nombre de la "
    "lista de tipos de relación (ej. COMMENTS_ON -> comenta sobre)."
)


def _token_dir(token: str) -> str:
    return os.path.join(SHARES_DIR, token)


def _valid_token(token: str) -> bool:
    return bool(token) and len(token) <= 64 and all(c.isalnum() or c in '-_' for c in token)


class ShareService:
    """Crear, leer y revocar publicaciones."""

    # ---------- lectura ----------
    @staticmethod
    def get(token: str) -> Optional[Dict[str, Any]]:
        if not _valid_token(token):
            return None
        path = os.path.join(_token_dir(token), 'share.json')
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"share.json ilegible ({token}): {exc}")
            return None

    @staticmethod
    def find_by_report(report_id: str) -> Optional[Dict[str, Any]]:
        if not os.path.isdir(SHARES_DIR):
            return None
        for token in os.listdir(SHARES_DIR):
            meta = ShareService.get(token)
            if meta and meta.get('report_id') == report_id:
                return meta
        return None

    @staticmethod
    def file_path(token: str, name: str) -> Optional[str]:
        if not _valid_token(token) or name not in ('graph.json', 'illustration.png', 'preview.jpg'):
            return None
        path = os.path.join(_token_dir(token), name)
        return path if os.path.exists(path) else None

    @staticmethod
    def public_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
        """Lo que puede ver un visitante: sin dueño, sin ids internos."""
        return {
            'token': meta['token'],
            'titular': meta.get('titular', ''),
            'whatsapp': meta.get('whatsapp', ''),
            'puntos': meta.get('puntos', []),
            'report_title': meta.get('report_title', ''),
            'caption': meta.get('caption', ''),
            'has_image': meta.get('has_image', False),
            'graph_stats': meta.get('graph_stats', {}),
            'entity_types': meta.get('entity_types', []),
            'created_at': meta.get('created_at', ''),
        }

    # ---------- creación ----------
    @staticmethod
    def create(report_id: str, owner: Optional[str], graph_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        existing = ShareService.find_by_report(report_id)
        if existing:
            return existing

        report = ReportManager.get_report(report_id)
        if not report:
            raise ValueError(t('api.reportNotFound', id=report_id))
        if report.status != ReportStatus.COMPLETED:
            raise ValueError(t('api.shareReportNotCompleted'))

        text = (report.markdown_content or '').strip()
        if not text:
            md_path = ReportManager._get_report_markdown_path(report_id)
            if os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
        if not text:
            raise ValueError(t('api.illustrationEmptyReport'))

        title = report.outline.title if report.outline else ''
        type_names = []
        rel_names = []
        if graph_data:
            for e in graph_data.get('edges', []) or []:
                rn = e.get('name') or ''
                if rn and rn not in rel_names and len(rel_names) < 40:
                    rel_names.append(rn)
            for n in graph_data.get('nodes', []) or []:
                label = next((l for l in (n.get('labels') or []) if l != 'Entity'), 'Entity')
                if label not in type_names:
                    type_names.append(label)
        summary = ShareService._summarize(title, report.simulation_requirement, text, type_names, rel_names)

        with _lock:
            token = secrets.token_urlsafe(12)
            folder = _token_dir(token)
            os.makedirs(folder, exist_ok=True)

            # Ilustración + vista previa ligera para WhatsApp (el PNG pesa ~3 MB; WhatsApp ignora imágenes grandes)
            has_image = False
            caption = ''
            ill_meta = IllustrationService.get_meta(report_id)
            if ill_meta:
                src = IllustrationService.image_path(report_id)
                shutil.copyfile(src, os.path.join(folder, 'illustration.png'))
                ShareService._make_preview(src, os.path.join(folder, 'preview.jpg'))
                has_image = True
                caption = ill_meta.get('caption', '')

            graph_stats: Dict[str, int] = {}
            entity_types: List[Dict[str, Any]] = []
            if graph_data:
                snapshot, graph_stats, entity_types = ShareService._graph_snapshot(graph_data, summary.get('tipos', {}), summary.get('relaciones', {}))
                with open(os.path.join(folder, 'graph.json'), 'w', encoding='utf-8') as f:
                    json.dump(snapshot, f, ensure_ascii=False, separators=(',', ':'))

            meta = {
                'token': token,
                'report_id': report_id,
                'simulation_id': report.simulation_id,
                'graph_id': report.graph_id,
                'owner': owner,
                'tenant': None,
                'titular': summary.get('titular') or title,
                'whatsapp': summary.get('whatsapp', ''),
                'puntos': summary.get('puntos', []),
                'report_title': title,
                'caption': caption,
                'has_image': has_image,
                'graph_stats': graph_stats,
                'entity_types': entity_types,
                'summary_model': Config.LLM_MODEL_NAME,
                'created_at': datetime.now().isoformat(timespec='seconds'),
            }
            with open(os.path.join(folder, 'share.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info(f"Publicación creada: {token} para {report_id} (owner={owner})")
        return meta

    @staticmethod
    def revoke(report_id: str) -> bool:
        meta = ShareService.find_by_report(report_id)
        if not meta:
            return False
        shutil.rmtree(_token_dir(meta['token']), ignore_errors=True)
        logger.info(f"Publicación revocada: {meta['token']} ({report_id})")
        return True

    # ---------- ayudantes ----------
    @staticmethod
    def _summarize(title: str, requirement: str, text: str, type_names: List[str], rel_names: Optional[List[str]] = None) -> Dict[str, Any]:
        if len(text) > MAX_REPORT_CHARS:
            text = text[:MAX_REPORT_CHARS] + "\n\n[...]"
        data = LLMClient().chat_json(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM + " " + get_language_instruction()},
                {"role": "user", "content": f"Título del informe: {title}\n\nPregunta de la simulación: {requirement}\n\nTipos de participantes: {', '.join(type_names) or '(ninguno)'}\n\nTipos de relación: {', '.join(rel_names or []) or '(ninguno)'}\n\nInforme:\n{text}"},
            ],
            temperature=0.4,
            max_tokens=6000,
        )
        puntos = [str(p).strip() for p in (data.get('puntos') or []) if str(p).strip()][:5]
        tipos = data.get('tipos') if isinstance(data.get('tipos'), dict) else {}
        tipos = {str(k): str(v).strip()[:40] for k, v in tipos.items() if str(v).strip()}
        rels = data.get('relaciones') if isinstance(data.get('relaciones'), dict) else {}
        rels = {str(k): str(v).strip()[:40] for k, v in rels.items() if str(v).strip()}
        return {
            'tipos': tipos,
            'relaciones': rels,
            'titular': str(data.get('titular') or '').strip(),
            'whatsapp': str(data.get('whatsapp') or '').strip()[:600],
            'puntos': puntos,
        }

    @staticmethod
    def _make_preview(src: str, dst: str) -> None:
        try:
            from PIL import Image
            with Image.open(src) as im:
                im = im.convert('RGB')
                im.thumbnail((1200, 800))
                # Recorte 1.91:1 centrado (formato de tarjeta de enlace)
                w, h = im.size
                target_h = int(w / 1.91)
                if target_h < h:
                    top = (h - target_h) // 2
                    im = im.crop((0, top, w, top + target_h))
                im.save(dst, 'JPEG', quality=78, optimize=True, progressive=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"No se pudo crear la vista previa: {exc}")

    @staticmethod
    def _graph_snapshot(graph_data: Dict[str, Any], labels: Optional[Dict[str, str]] = None, rel_labels: Optional[Dict[str, str]] = None):
        labels = labels or {}
        rel_labels = rel_labels or {}
        palette = ['#FF6B35', '#004E89', '#7B2D8E', '#1A936F', '#C5283D', '#E9724C', '#3498db', '#9b59b6', '#27ae60', '#f39c12']
        types: Dict[str, Dict[str, Any]] = {}
        nodes = []
        for n in graph_data.get('nodes', []) or []:
            label = next((l for l in (n.get('labels') or []) if l != 'Entity'), 'Entity')
            if label not in types:
                types[label] = {'name': label, 'label': labels.get(label) or ('Otros' if label == 'Entity' else label),
                                'color': palette[len(types) % len(palette)], 'count': 0}
            types[label]['count'] += 1
            nodes.append({
                'id': n.get('uuid'),
                'name': n.get('name') or '',
                'type': label,
                'type_label': labels.get(label) or ('Otros' if label == 'Entity' else label),
                'summary': (n.get('summary') or '')[:MAX_NODE_SUMMARY],
            })
        ids = {n['id'] for n in nodes}
        edges = []
        for e in graph_data.get('edges', []) or []:
            s, d = e.get('source_node_uuid'), e.get('target_node_uuid')
            if s in ids and d in ids and s != d:
                edges.append({'s': s, 't': d, 'name': rel_labels.get(e.get('name') or '') or (e.get('name') or '').replace('_', ' ').lower(), 'fact': (e.get('fact') or '')[:MAX_EDGE_FACT]})
        stats = {'nodes': len(nodes), 'edges': len(edges), 'types': len(types)}
        return {'nodes': nodes, 'edges': edges, 'types': list(types.values())}, stats, list(types.values())
