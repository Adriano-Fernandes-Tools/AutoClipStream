"""Plugin de detecção de texto na tela via OCR nativo do Windows (estilo Text-Grab).

Utiliza o Windows.Media.Ocr / OCR nativo para identificar textos de impacto
direto na tela do jogo (ex: 'VICTORY', 'VITORIA', 'GAME OVER', 'ELIMINADO', 'WASTED', etc.).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .base import BaseDetectorPlugin

OCR_KEYWORDS_RE = re.compile(
    r"\b(victory|vitoria|derrota|defeat|game\s*over|wasted|eliminou|eliminado|clutch|"
    r"champion|campeao|winner|first\s*place|recorde|record|boss|round\s*won)\b",
    re.IGNORECASE,
)


class WindowsOcrPlugin(BaseDetectorPlugin):
    name = "windows_ocr"
    description = "Leitura de textos de destaque na tela via OCR nativo do Windows"
    enabled = True

    def analyze(
        self,
        canal: str,
        video_path: Path,
        frames: list[Path],
        transcript_text: str,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not frames:
            return []

        moments: list[dict[str, Any]] = []

        # Analisa até 3 frames por bloco
        sample = frames[:3] if len(frames) <= 3 else [frames[0], frames[len(frames) // 2], frames[-1]]

        for frame_file in sample:
            text = self._ocr_frame(frame_file)
            if not text:
                continue

            matches = OCR_KEYWORDS_RE.findall(text)
            if matches:
                kw_str = ", ".join(sorted(set(m.lower() for m in matches)))
                moments.append({
                    "segundo": 15.0,
                    "score": 80.0,
                    "tipo": "ocr_screen",
                    "descricao": f"Texto na tela (OCR): {kw_str}",
                })

        return moments

    def _ocr_frame(self, image_path: Path) -> str:
        """Extrai texto da imagem usando o Windows.Media.Ocr nativo via PowerShell."""
        ps_script = f"""
[Windows.Globalization.Language, Windows.Foundation.UniversalApiContract, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation.UniversalApiContract, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation.UniversalApiContract, ContentType = WindowsRuntime] | Out-Null

try {{
    $file = [System.IO.File]::OpenRead('{str(image_path).replace("'", "''")}')
    $stream = $file.AsRandomAccessStream()
    $decoderTask = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = $decoderTask.GetAwaiter().GetResult()
    $bitmapTask = $decoder.GetSoftwareBitmapAsync()
    $bitmap = $bitmapTask.GetAwaiter().GetResult()

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($engine -ne $null) {{
        $ocrResult = $engine.RecognizeAsync($bitmap).GetAwaiter().GetResult()
        Write-Output $ocrResult.Text
    }}
}} catch {{}}
"""
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return res.stdout.strip()
        except Exception:
            return ""
