"""
Parches de recursos para el recomendador de la plataforma tipo Twitter de OASIS (fork MX).

OASIS recomienda posts con TWHIN-BERT: en cada ronda calcula vectores de perfiles + posts en la CPU del servidor.

Modo por defecto ("bert"): lotes pequeños.
  OASIS llama generate_post_vector(..., batch_size=1000): hasta 1000 textos en un solo lote de BERT con padding a 512 tokens;
  la atención de ese lote pedía 10-16 GB y el kernel mataba el subproceso. Aquí el lote real es OASIS_EMBED_BATCH_SIZE
  (16 por defecto): mismo resultado, ~60 veces menos memoria transitoria. Medido: 3.5-4.3 GB con 89 agentes.

Modo "openai" (MIROFISH_RECSYS_EMBEDDINGS=openai, fase F1):
  NO se carga el modelo BERT; los vectores se piden a la API de OpenAI (text-embedding-3-small por defecto) con caché en memoria
  por texto, así cada post se paga una sola vez aunque se recomiende en cada ronda. Clave: OPENAI_EMBEDDINGS_API_KEY o, si no
  existe, ILLUSTRATION_OPENAI_API_KEY. Se llama a api.openai.com explícitamente porque OPENAI_API_BASE_URL apunta a DeepSeek
  (lo fija el script para los agentes) y DeepSeek no tiene vectores.

Se importa justo después de `import oasis` en run_parallel_simulation.py, run_twitter_simulation.py y run_reddit_simulation.py.
"""
import hashlib
import json as _json
import os
import threading as _threading
import time as _time

# ---- Consumo para créditos (F4): una línea por llamada en usage_llm.jsonl (cwd = carpeta de la simulación) ----
_USAGE_LOCK = _threading.Lock()


def _log_usage(model, prompt=0, hit=0, completion=0):
    try:
        rec = {"model": str(model or ""), "in": int(prompt or 0), "hit": int(hit or 0), "out": int(completion or 0),
               "ts": _time.time()}
        with _USAGE_LOCK, open("usage_llm.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec) + "\n")
    except Exception as exc:  # noqa: BLE001  (medir nunca debe romper la simulación)
        print(f"[oasis_memory_patch] no se pudo anotar consumo: {exc}", flush=True)


def _usage_of(resp):
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    hit = getattr(u, "prompt_cache_hit_tokens", None)
    if hit is None:
        d = getattr(u, "prompt_tokens_details", None)
        hit = getattr(d, "cached_tokens", 0) if d is not None else 0
    return getattr(resp, "model", None), getattr(u, "prompt_tokens", 0), hit, getattr(u, "completion_tokens", 0)

try:
    import oasis.social_platform.recsys as _recsys

    _MODE = os.environ.get("MIROFISH_RECSYS_EMBEDDINGS", "bert").strip().lower()

    if _MODE == "openai":
        import torch
        from openai import OpenAI

        _KEY = os.environ.get("OPENAI_EMBEDDINGS_API_KEY") or os.environ.get("ILLUSTRATION_OPENAI_API_KEY")
        if not _KEY:
            raise RuntimeError("falta OPENAI_EMBEDDINGS_API_KEY / ILLUSTRATION_OPENAI_API_KEY")
        _MODEL = os.environ.get("OPENAI_EMBEDDINGS_MODEL", "text-embedding-3-small")
        _client = OpenAI(api_key=_KEY, base_url="https://api.openai.com/v1", timeout=120.0, max_retries=5)
        _cache = {}
        _stats = {"calls": 0, "texts": 0, "hits": 0}

        def _key(text):
            return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()

        def _embed_texts(texts):
            clean = [(t.strip()[:2000] if isinstance(t, str) and t.strip() else "empty") for t in texts]
            missing = [t for t in dict.fromkeys(clean) if _key(t) not in _cache]
            for i in range(0, len(missing), 256):
                batch = missing[i:i + 256]
                resp = _client.embeddings.create(model=_MODEL, input=batch)
                _log_usage(_MODEL, getattr(getattr(resp, "usage", None), "total_tokens", 0))
                for t, item in zip(batch, resp.data):
                    _cache[_key(t)] = item.embedding
                _stats["calls"] += 1
                _stats["texts"] += len(batch)
            _stats["hits"] += len(clean) - len(missing)
            return torch.tensor([_cache[_key(t)] for t in clean], dtype=torch.float32)

        def _generate_post_vector_openai(model, tokenizer, texts, batch_size=None):
            out = _embed_texts(list(texts))
            if _stats["calls"] and _stats["calls"] % 20 == 0:
                print(f"[oasis_memory_patch] vectores OpenAI: {_stats['texts']} textos pedidos, "
                      f"{_stats['hits']} servidos de caché, {_stats['calls']} llamadas", flush=True)
            return out

        _orig_get_recsys_model = _recsys.get_recsys_model

        def _get_recsys_model_without_bert(recsys_type=None):
            # Evita descargar y cargar TWHIN-BERT: devuelve marcadores no nulos para que OASIS no lo vuelva a intentar
            if recsys_type == "twhin-bert":
                return ("openai-embeddings", "openai-embeddings")
            return _orig_get_recsys_model(recsys_type)

        _recsys.get_recsys_model = _get_recsys_model_without_bert
        _recsys.generate_post_vector = _generate_post_vector_openai
        print(f"[oasis_memory_patch] recomendador con vectores de OpenAI ({_MODEL}); BERT no se carga", flush=True)
    else:
        _BATCH = max(1, int(os.environ.get("OASIS_EMBED_BATCH_SIZE", "16")))
        _orig_generate_post_vector = _recsys.generate_post_vector

        def _generate_post_vector_small_batches(model, tokenizer, texts, batch_size=None):
            return _orig_generate_post_vector(model, tokenizer, texts, _BATCH)

        _recsys.generate_post_vector = _generate_post_vector_small_batches
        print(f"[oasis_memory_patch] generate_post_vector con lotes de {_BATCH} textos (BERT local)", flush=True)
except Exception as _exc:  # noqa: BLE001
    print(f"[oasis_memory_patch] no aplicado: {_exc}", flush=True)


# ---- Agentes sin razonamiento en DeepSeek (2026-11-22) ----
# camel/OASIS llama a chat.completions.create del SDK de OpenAI sin opción para extra_body; se envuelve el método
# (síncrono y asíncrono) y se agrega thinking=disabled solo cuando el modelo es de DeepSeek.
try:
    from openai.resources.chat import completions as _oa_completions

    def _wrap(orig, is_async):
        def _inject(kwargs):
            if str(kwargs.get("model", "")).lower().startswith("deepseek"):
                extra = dict(kwargs.get("extra_body") or {})
                extra.setdefault("thinking", {"type": "disabled"})
                kwargs["extra_body"] = extra
            return kwargs

        def _note(resp, kwargs):
            u = _usage_of(resp)
            if u:
                _log_usage(u[0] or kwargs.get("model"), u[1], u[2], u[3])
            return resp

        if is_async:
            async def _create(self, *args, **kwargs):
                return _note(await orig(self, *args, **_inject(kwargs)), kwargs)
        else:
            def _create(self, *args, **kwargs):
                return _note(orig(self, *args, **_inject(kwargs)), kwargs)
        return _create

    _oa_completions.Completions.create = _wrap(_oa_completions.Completions.create, False)
    _oa_completions.AsyncCompletions.create = _wrap(_oa_completions.AsyncCompletions.create, True)
    print("[oasis_memory_patch] DeepSeek sin razonamiento (thinking=disabled) en los agentes", flush=True)
    print(f"[oasis_memory_patch] consumo de IA -> {os.path.join(os.getcwd(), 'usage_llm.jsonl')}", flush=True)
except Exception as _exc:  # noqa: BLE001
    print(f"[oasis_memory_patch] thinking=disabled no aplicado: {_exc}", flush=True)
