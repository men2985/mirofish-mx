"""
Ilustración del informe final.

Flujo: el LLM configurado (DeepSeek) lee full_report.md y redacta un prompt visual en inglés
más un pie de imagen en el idioma del usuario; OpenAI Images genera la imagen; se guarda como
illustration.png + illustration.json junto a full_report.md en la carpeta del informe.

Variables (.env): ILLUSTRATION_OPENAI_API_KEY (obligatoria para activar el botón),
ILLUSTRATION_IMAGE_MODEL, ILLUSTRATION_IMAGE_SIZE, ILLUSTRATION_IMAGE_QUALITY.
"""

import base64
import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from openai import OpenAI

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.locale import t
from ..utils.logger import get_logger
from .report_agent import ReportManager, ReportStatus

logger = get_logger('mirofish.illustration')

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

MAX_REPORT_CHARS = 9000

# Parche MX (11-22): la ilustración es una INFOGRAFÍA ILUSTRADA que cuenta el resultado (titular, veredicto,
# hallazgos con sus cifras y recomendación), no una imagen limpia sin texto. DeepSeek extrae los datos cortos y exactos;
# el prompt de la imagen se arma con una plantilla fija para que el texto salga tal cual y en español.
PROMPT_SYSTEM = (
    "Eres editor de una infografía. Recibes el informe de predicción de una simulación social con agentes. "
    "Extrae SOLO lo que dice el informe, sin inventar cifras: si el informe no da un número, usa una frase corta. "
    "Devuelve un objeto json con: "
    "\"titular\" (máx. 8 palabras, el resultado principal), "
    "\"veredicto\" (máx. 12 palabras: qué tan aceptada es la idea y por qué), "
    "\"hallazgos\" (lista de 3 o 4 objetos {\"etiqueta\": máx. 4 palabras, \"dato\": máx. 6 palabras o una cifra "
    "del informe}), "
    "\"recomendacion\" (máx. 12 palabras: el cambio más importante para que la acepten), "
    "\"escena\" (en inglés, 30-50 palabras: escena ilustrada central que represente la idea en su ciudad, con personas "
    "diversas reaccionando; sin personas reales reconocibles, sin logotipos), "
    "\"caption\" (una frase que resuma la conclusión). "
    "Todo el texto visible (titular, veredicto, hallazgos, recomendación, caption) en español natural de México, "
    "con acentos correctos y sin comillas internas."
)

PROMPT_EXAMPLE = {
    "titular": "Mucha curiosidad, pocas citas",
    "veredicto": "Aceptación media: interesa, pero el precio y la desconfianza frenan la cita",
    "hallazgos": [
        {"etiqueta": "Objeción principal", "dato": "Suena a estafa"},
        {"etiqueta": "Precio percibido", "dato": "900 pesos, caro"},
        {"etiqueta": "Quién agenda", "dato": "Mujeres de 35 a 55"},
    ],
    "recomendacion": "Ofrecer una primera sesión corta con prueba visible",
    "escena": "A lively Oaxaca neighborhood street ...",
    "caption": "La ciudad tiene curiosidad, pero aún no confía lo suficiente para agendar.",
}


def _q(text: str, limit: int) -> str:
    """Texto que irá dentro de la imagen: una línea, sin comillas, recortado."""
    t_ = ' '.join(str(text or '').replace('"', '').replace('“', '').replace('”', '').split())
    return t_[:limit].rstrip()


def build_image_prompt(d: Dict[str, Any]) -> str:
    hallazgos = [h for h in (d.get('hallazgos') or []) if isinstance(h, dict)][:4]
    tarjetas = '; '.join(f'card {n}: label "{_q(h.get("etiqueta"), 40)}" with value "{_q(h.get("dato"), 50)}"'
                         for n, h in enumerate(hallazgos, 1))
    return (
        "Editorial illustrated infographic, landscape 3:2, clean and airy layout with generous margins, flat vector "
        "illustration with subtle paper texture, warm natural palette with one strong accent color, crisp legible "
        "typography (bold sans-serif headline, regular sans-serif body). "
        f'Top: large headline exactly "{_q(d.get("titular"), 70)}". '
        f'Under it, a highlighted verdict banner exactly "{_q(d.get("veredicto"), 110)}". '
        f"Center-left: {_q(d.get('escena'), 400)} "
        f"Right side: {len(hallazgos)} stacked rounded info cards, each with a simple icon; {tarjetas}. "
        f'Bottom strip with a lightbulb icon: "Recomendación: {_q(d.get("recomendacion"), 110)}". '
        "Render every text EXACTLY as written above, in Spanish with correct accents and spelling; do not add any "
        "other words, numbers, captions, logos or watermarks. No recognizable real people."
    )


def _lock_for(report_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(report_id)
        if lock is None:
            lock = threading.Lock()
            _locks[report_id] = lock
        return lock


class IllustrationService:
    """Genera y sirve la ilustración de un informe."""

    @staticmethod
    def is_available() -> bool:
        return bool(Config.ILLUSTRATION_OPENAI_API_KEY)

    @classmethod
    def image_path(cls, report_id: str) -> str:
        return os.path.join(ReportManager._get_report_folder(report_id), 'illustration.png')

    @classmethod
    def meta_path(cls, report_id: str) -> str:
        return os.path.join(ReportManager._get_report_folder(report_id), 'illustration.json')

    @classmethod
    def get_meta(cls, report_id: str) -> Optional[Dict[str, Any]]:
        meta_path = cls.meta_path(report_id)
        if not os.path.exists(meta_path) or not os.path.exists(cls.image_path(report_id)):
            return None
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"illustration.json ilegible para {report_id}: {exc}")
            return None

    @classmethod
    def _report_text(cls, report_id: str, report) -> str:
        text = (report.markdown_content or '').strip()
        if not text:
            md_path = ReportManager._get_report_markdown_path(report_id)
            if os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
        return text

    @classmethod
    def generate(cls, report_id: str, force: bool = False) -> Dict[str, Any]:
        if not cls.is_available():
            raise ValueError(t('api.illustrationNotConfigured'))

        lock = _lock_for(report_id)
        if not lock.acquire(blocking=False):
            raise RuntimeError(t('api.illustrationInProgress'))
        try:
            report = ReportManager.get_report(report_id)
            if not report:
                raise ValueError(t('api.reportNotFound', id=report_id))
            if report.status != ReportStatus.COMPLETED:
                raise ValueError(t('api.illustrationReportNotCompleted'))

            existing = cls.get_meta(report_id)
            if existing and not force:
                return existing

            text = cls._report_text(report_id, report)
            if not text:
                raise ValueError(t('api.illustrationEmptyReport'))
            if len(text) > MAX_REPORT_CHARS:
                text = text[:MAX_REPORT_CHARS] + "\n\n[...]"

            title = report.outline.title if report.outline else ''
            logger.info(f"Ilustración: redactando prompt visual para {report_id}")
            llm = LLMClient()
            prompt_data = llm.chat_json(
                messages=[
                    {"role": "system", "content": PROMPT_SYSTEM},
                    {"role": "user", "content": (
                        f"Título del informe: {title}\n\nRequisito de la predicción: {report.simulation_requirement}"
                        f"\n\nInforme:\n{text}\n\nEjemplo del formato json (solo el formato; el contenido sale del "
                        f"informe):\n{json.dumps(PROMPT_EXAMPLE, ensure_ascii=False)}")},
                ],
                temperature=0.4,
                max_tokens=3000,
            )
            if not prompt_data.get('titular') or not prompt_data.get('hallazgos'):
                raise ValueError("El modelo no devolvió los datos de la infografía")
            image_prompt = build_image_prompt(prompt_data)
            caption = str(prompt_data.get('caption') or '').strip()

            logger.info(f"Ilustración: generando imagen con {Config.ILLUSTRATION_IMAGE_MODEL} para {report_id}")
            client = OpenAI(api_key=Config.ILLUSTRATION_OPENAI_API_KEY, timeout=180.0)
            result = client.images.generate(
                model=Config.ILLUSTRATION_IMAGE_MODEL,
                prompt=image_prompt,
                size=Config.ILLUSTRATION_IMAGE_SIZE,
                quality=Config.ILLUSTRATION_IMAGE_QUALITY,
                n=1,
            )
            try:  # F4: créditos (precio por imagen de llm_pricing)
                from .credits import record_image
                record_image(Config.ILLUSTRATION_IMAGE_MODEL, Config.ILLUSTRATION_IMAGE_QUALITY)
            except Exception:  # noqa: BLE001
                pass
            item = result.data[0]
            if getattr(item, 'b64_json', None):
                png = base64.b64decode(item.b64_json)
            elif getattr(item, 'url', None):
                png = httpx.get(item.url, timeout=60.0).content
            else:
                raise RuntimeError("La API de imágenes no devolvió datos")

            folder = ReportManager._get_report_folder(report_id)
            os.makedirs(folder, exist_ok=True)
            with open(cls.image_path(report_id), 'wb') as f:
                f.write(png)

            meta = {
                "report_id": report_id,
                "caption": caption,
                "image_prompt": image_prompt,
                "infografia": {k: prompt_data.get(k) for k in ('titular', 'veredicto', 'hallazgos', 'recomendacion')},
                "image_model": Config.ILLUSTRATION_IMAGE_MODEL,
                "size": Config.ILLUSTRATION_IMAGE_SIZE,
                "quality": Config.ILLUSTRATION_IMAGE_QUALITY,
                "prompt_model": Config.LLM_MODEL_NAME,
                "created_at": datetime.now().isoformat(timespec='seconds'),
                "bytes": len(png),
            }
            with open(cls.meta_path(report_id), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"Ilustración guardada: {cls.image_path(report_id)} ({len(png)} bytes)")
            return meta
        finally:
            lock.release()
