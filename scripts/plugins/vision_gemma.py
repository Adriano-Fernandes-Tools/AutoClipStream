"""Plugin de visão multimodal usando Ollama Gemma 3 Vision (local).

Envia frames representativos da gameplay para o modelo local inspecionar visualmente
e detectar reações, acontecimentos épicos, mortes ou vitórias na tela.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path
from typing import Any

from .base import BaseDetectorPlugin


class GemmaVisionPlugin(BaseDetectorPlugin):
    name = "gemma_vision"
    description = "Detecção visual inteligente via Gemma 3 Vision local"
    enabled = True

    def analyze(
        self,
        canal: str,
        video_path: Path,
        frames: list[Path],
        transcript_text: str,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        backend = str(config.get("ai_backend", "")).lower()
        if backend != "local":
            return []

        if not frames:
            return []

        # Analisa até 2 frames chave por bloco para manter a inferência rápida e responsiva
        selected_frames = frames[:2] if len(frames) <= 2 else [frames[len(frames) // 3], frames[(len(frames) * 2) // 3]]
        moments: list[dict[str, Any]] = []
        ollama_url = str(config.get("ai_local_url", "http://localhost:11434")).rstrip("/")

        for frame_file in selected_frames:
            try:
                b64 = base64.b64encode(frame_file.read_bytes()).decode("ascii")
                prompt = (
                    "Analise esta cena de gameplay/live. Há algum momento emocionante, vitória, morte, "
                    "expressão de susto/risada ou algo visualmente marcante? "
                    "Responda em JSON: {\"relevante\": true/false, \"score\": 0-100, \"descricao\": \"...\"}"
                )
                payload = json.dumps({
                    "model": "gemma3:4b",
                    "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                    "options": {"num_gpu": 0},
                    "stream": False,
                }).encode("utf-8")

                req = urllib.request.Request(
                    f"{ollama_url}/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data.get("message", {}).get("content", "")

                import re
                m = re.search(r"\{[^{}]+\}", content)
                if m:
                    res = json.loads(m.group())
                    if res.get("relevante") or int(res.get("score", 0)) >= 60:
                        moments.append({
                            "segundo": 30.0,
                            "score": float(res.get("score", 70)),
                            "tipo": "vision_ai",
                            "descricao": str(res.get("descricao", "Destaque visual identificado pelo Gemma Vision")),
                        })
            except Exception:
                pass

        return moments
