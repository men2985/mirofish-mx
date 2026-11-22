"""
Chat "Afinar tu idea" (fork MX).

El usuario cuenta su idea como se la contaría a alguien; la IA (DeepSeek, sin razonamiento) revisa lo mínimo que la
ciudad necesita para juzgarla (qué se ofrece, a quién, precio, canal, competencia, diferenciador), pregunta SOLO lo
que falta (máx. 2 preguntas por turno, con opciones para tocar, máx. 3 rondas) y al final entrega:
  - una ficha "Así entendimos tu idea" (lo supuesto va marcado como supuesto),
  - la instrucción reformulada (qué predecir),
  - el documento semilla en Markdown (la idea en la ciudad del usuario) — si el usuario no subió archivo, ESTE es el
    documento que lee el sistema; si subió, se agrega como contexto y lo del documento manda.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient

MAX_RONDAS = 3
MAX_DOC_CHARS = 6000

SISTEMA = """Eres el asistente de una ciudad digital de México donde ciudadanos sintéticos validan ideas de negocio.
Tu trabajo: entender bien la idea del usuario ANTES de lanzarla, conversando breve y cálido, en español de México.

Lo mínimo que la ciudad necesita saber: oferta (qué se vende o propone), precio, público (a quién va), canal (cómo se
vende o llega), competencia o alternativa actual, diferenciador (por qué elegir esto).

Reglas:
- Si un DOCUMENTO del usuario trae un dato, ese dato manda; no preguntes lo que ya dice ni lo contradigas.
- Pregunta SOLO lo que falte y sea importante: máximo 2 preguntas por turno, cada una con 2-4 opciones cortas y
  realistas para su ciudad (el usuario también puede escribir otra). Nunca repitas una pregunta ya contestada.
- Si ya es la ronda {ronda} de {max_rondas} o el usuario pide lanzar, NO preguntes más: completa lo que falte con
  supuestos razonables para su ciudad y márcalos en "supuestos".
- "respuesta": 1-2 frases, cálidas y concretas (qué entendiste o qué falta). Sin listas largas, sin emojis.
- Cuando tengas lo mínimo (o se acabaron las rondas), "lista" = true y entrega "instruccion" (1-2 frases: qué debe
  predecir la ciudad: aceptación, objeciones, quién compra, qué cambiar) y "semilla_md" (300-600 palabras en
  Markdown: el negocio, la oferta con su precio, el público en la ciudad del usuario, canal, competencia y
  alternativas locales, objeciones esperables; con nombres plausibles pero marcando como supuesto lo que no dio el
  usuario ni el documento).

MODO GUION: si el usuario pega (o el documento trae) un guion de anuncio en video (escenas, gancho, voz en off,
tomas, texto en pantalla, llamado a la acción), pon "modo" = "guion" y cambia lo mínimo a: producto (qué anuncia y su
precio si aplica), plataforma (TikTok, Reels, YouTube, Facebook), público, objetivo (qué debe hacer quien lo ve: comprar,
escribir por WhatsApp, agendar…) y duración aproximada. No preguntes por canal de venta ni competencia salvo que falte
algo esencial. En "ficha" usa las claves producto, plataforma, publico, objetivo, duracion, supuestos y "escenas"
(lista de objetos {"n": 1, "texto": "..."}: el guion del usuario dividido en escenas, SIN reescribirlo ni mejorarlo;
si no trae escenas marcadas, divídelo por momentos: gancho, desarrollo, prueba, llamado a la acción).
En modo guion, "instruccion" pide predecir: si el gancho detiene el scroll, en qué escena se pierde el interés y por
qué, qué objeciones despierta, quién haría clic o escribiría, y qué cambiar en cada escena. "semilla_md" describe el
anuncio como lo vería la ciudad: producto y precio, plataforma, público, objetivo, y el guion COMPLETO con sus escenas
numeradas tal cual (### Escena 1 …), más el contexto local de la ciudad del usuario.
Cuenta como guion aunque NO traiga escenas marcadas: una transcripción o monólogo hablado a cámara, en segunda
persona, con gancho inicial (pregunta o afirmación que interpela) y cierre con llamado a la acción ("agenda", "escríbenos",
"haz clic", "te dejo el número aquí abajo"). Si dudas entre idea y guion, pregunta "¿Esto es el guion de un video?"
con opciones ["Sí, es el guion de un video", "No, es la descripción de mi negocio"].
Si no es un guion, "modo" = "idea".
Responde SOLO con un objeto json."""

EJEMPLO = {
    "respuesta": "Entendí: cajas de pan de yema a domicilio los fines de semana. Me faltan dos datos para que la ciudad la juzgue bien.",
    "preguntas": [
        {"id": "precio", "pregunta": "¿A qué precio la caja?", "opciones": ["$100", "$120", "$150"]},
        {"id": "publico", "pregunta": "¿Quién es tu cliente principal?", "opciones": ["Familias", "Oficinas", "Turistas"]},
    ],
    "ficha": {"oferta": "Caja de 6 piezas de pan de yema con chocolate de agua", "precio": "", "publico": "",
              "canal": "Pedidos por WhatsApp, entrega a domicilio sábado y domingo", "competencia": "Panaderías del mercado",
              "diferenciador": "Llega caliente a tu puerta", "supuestos": []},
    "modo": "idea",
    "lista": False,
    "instruccion": "",
    "semilla_md": "",
}


def _limpiar_ficha(ficha: Any) -> Dict[str, Any]:
    if not isinstance(ficha, dict):
        return {}
    escenas = []
    for i, e in enumerate(ficha.get('escenas') or []):
        texto = e.get('texto') if isinstance(e, dict) else e
        if str(texto or '').strip():
            escenas.append({'n': (e.get('n') if isinstance(e, dict) else None) or i + 1, 'texto': str(texto).strip()[:600]})
    ficha = dict(ficha)
    if escenas:
        ficha['escenas'] = escenas[:20]
    else:
        ficha.pop('escenas', None)
    return ficha


def turno(historial: List[Dict[str, str]], ciudad: Optional[str], documento: str, forzar: bool,
          modo: Optional[str] = None) -> Dict[str, Any]:
    """Un turno del chat. historial = [{rol: 'usuario'|'asistente', texto}], el último es del usuario."""
    rondas = sum(1 for m in historial if m.get('rol') == 'asistente')
    ronda = min(rondas + 1, MAX_RONDAS)
    sistema = SISTEMA.replace('{ronda}', str(ronda)).replace('{max_rondas}', str(MAX_RONDAS))
    contexto = f"Ciudad del usuario: {ciudad or 'no indicada (asume una ciudad mediana de México)'}.\n"
    if documento:
        contexto += f"\nDOCUMENTO del usuario (manda sobre cualquier supuesto):\n{documento[:MAX_DOC_CHARS]}\n"
    if modo == 'guion':
        contexto += "\nEl usuario indicó que el texto es el GUION de un anuncio en video: usa el MODO GUION (\"modo\" = \"guion\").\n"
    if forzar or ronda >= MAX_RONDAS:
        contexto += "\nYa no preguntes: entrega la ficha final con lista = true, instruccion y semilla_md.\n"
    conversacion = '\n'.join(f"{'Usuario' if m.get('rol') == 'usuario' else 'Asistente'}: {m.get('texto', '')}"
                             for m in historial[-12:])
    data = LLMClient().chat_json(
        [{'role': 'system', 'content': sistema},
         {'role': 'user', 'content': f"{contexto}\nConversación hasta ahora:\n{conversacion}\n\n"
                                     f"Ejemplo del formato json (solo el formato):\n{json.dumps(EJEMPLO, ensure_ascii=False)}"}],
        temperature=0.4, max_tokens=3000)
    preguntas = [p for p in (data.get('preguntas') or []) if isinstance(p, dict) and p.get('pregunta')][:2]
    lista = bool(data.get('lista')) or forzar or ronda >= MAX_RONDAS
    if lista:
        preguntas = []
    return {
        'respuesta': str(data.get('respuesta') or '').strip(),
        'preguntas': [{'id': str(p.get('id') or i), 'pregunta': str(p['pregunta'])[:160],
                       'opciones': [str(o)[:40] for o in (p.get('opciones') or [])][:4]} for i, p in enumerate(preguntas)],
        'ficha': _limpiar_ficha(data.get('ficha')),
        'modo': 'guion' if (modo == 'guion' or data.get('modo') == 'guion') else 'idea',
        'lista': lista and bool(data.get('semilla_md')),
        'instruccion': str(data.get('instruccion') or '').strip(),
        'semilla_md': str(data.get('semilla_md') or '').strip(),
        'ronda': ronda,
    }
