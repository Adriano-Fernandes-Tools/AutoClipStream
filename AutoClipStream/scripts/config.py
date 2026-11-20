"""Configurações centralizadas e utilitários compartilhados do projeto Stream-clipping.

Reúne caminhos do projeto, configurações de gravação, transcrição (faster-whisper),
extração de grades de frames e tipos/cores de marcadores para o Premiere Pro.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

# ==============================================================================
# CAMINHOS DO PROJETO
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CANAIS_DIR = PROJECT_ROOT / "canais"
CANAIS_CONFIG_FILE = PROJECT_ROOT / "canais.json"

# ==============================================================================
# BINÁRIOS EXTERNOS
# ==============================================================================

FFMPEG_BIN = shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe"
FFPROBE_BIN = shutil.which("ffprobe") or r"C:\ffmpeg\bin\ffprobe.exe"
SUBPROCESS_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ==============================================================================
# ESTRUTURA DE PASTAS POR CANAL
# ==============================================================================

CHANNEL_SUBDIRS: tuple[str, ...] = (
    "01_gravando",
    "02_para_processar",
    "03_em_processamento",
    "04_para_edicao",
    "05_cortes_extraidos",
    "06_arquivo_descartados",
    "_historico_lotes",
)

# ==============================================================================
# GRAVAÇÃO E ARQUIVOS
# ==============================================================================

DEFAULT_SEGMENT_MINUTES = 10
DEFAULT_QUALITY = "best"
DEFAULT_RECONNECT_SECONDS = 30
DEFAULT_TRANSCRIPTION_MODE = "manual"
PRE_TRANSCRICOES_DIRNAME = "_pre_transcricoes"
VIDEO_EXTENSIONS: tuple[str, ...] = (".mkv", ".mp4", ".ts", ".mov")

# ==============================================================================
# TRANSCRIÇÃO (FASTER-WHISPER) — BASEADO EM transcricao_whisper_v4.py
# ==============================================================================

WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "int8_float16"
WHISPER_BEAM_SIZE = 5
WHISPER_LANGUAGE: str | None = "pt"
VAD_ENABLED = True
VAD_MIN_SILENCE_MS = 500
VAD_SPEECH_PAD_MS = 400

FILTRAR_ALUCINACOES = True
ALUCINACOES_WHISPER: tuple[str, ...] = (
    r"legendas?\s+(por\s+|pela\s+)?adriana\s+zanotto",
    r"adriana\s+zanotto",
    r"legendas?\s+pela\s+comunidade\s+amara\.?\s*org",
    r"amara\.org",
)
ALUCINACOES_RE = re.compile("|".join(ALUCINACOES_WHISPER), re.IGNORECASE)

# ==============================================================================
# FRAMES VISUAIS E CONTEXTO ENTRE BLOCOS
# ==============================================================================

FRAME_INTERVAL_SECONDS = 15
GRID_COLS = 4
GRID_ROWS = 5
THUMB_WIDTH = 320
THUMB_HEIGHT = 180
OVERLAP_CONTEXT_SECONDS = 90

# ==============================================================================
# MARCADORES DO PREMIERE PRO (pproColor validados em modo_A_gerar_xml.py)
# ==============================================================================

MARKER_TYPES: dict[str, tuple[str, int | None]] = {
    "corte_forte": ("Corte forte", None),              # Verde padrão Premiere
    "climax": ("Clímax / Imperdível", 4281740498),     # Vermelho
    "possivel_corte": ("Possível corte", 4281049552),  # Amarelo
    "momento_visual": ("Destaque visual", 4294741314), # Azul
    "manual": ("Marcador manual", 4294741314),         # Azul (mesmo código do modo_A)
    "continuacao": ("Continua / Emenda", 4280578025),  # Laranja
}

# ==============================================================================
# TEMPLATE DO DOCUMENTO DE ESTADO DA LIVE
# ==============================================================================

ESTADO_LIVE_TEMPLATE = """# Estado da Live — {channel}

> Documento vivo atualizado a cada lote processado. Serve de contexto contínuo para a IA entender missões, piadas recorrentes e arcos em andamento entre blocos de 10 minutos.

## Estado Atual da Live

*(Nenhuma rodada analisada ainda.)*

## Arcos / Missões em Aberto

- *(Nenhum arco registrado ainda.)*

## Log Cronológico por Rodada

*(O histórico das rodadas aparecerá aqui.)*
"""


# ==============================================================================
# HELPERS
# ==============================================================================


def sanitize_channel_name(name: str) -> str:
    """Converte o nome do canal em um identificador seguro para pastas e arquivos."""
    normalized = unicodedata.normalize("NFKD", (name or "").strip())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_text).strip(" .")
    return cleaned or "canal"


def ensure_channel_structure(channel_name: str) -> dict[str, Path]:
    """Garante todas as subpastas e arquivos iniciais do canal em canais/<canal>/."""
    safe_name = sanitize_channel_name(channel_name)
    channel_root = CANAIS_DIR / safe_name
    channel_root.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {
        "name": Path(safe_name),
        "root": channel_root,
        "estado_live": channel_root / "estado_live.md",
        "ultimo_contexto": channel_root / "_ultimo_contexto.json",
        "registro_gravacoes": channel_root / "registro_gravacoes.jsonl",
        "marcadores_manuais": channel_root / "marcadores_manuais.json",
    }

    for subdir in CHANNEL_SUBDIRS:
        dir_path = channel_root / subdir
        dir_path.mkdir(parents=True, exist_ok=True)
        paths[subdir] = dir_path

    if not paths["estado_live"].exists():
        paths["estado_live"].write_text(
            ESTADO_LIVE_TEMPLATE.format(channel=safe_name),
            encoding="utf-8",
        )

    return paths


def load_channels_config(config_path: Path | None = None) -> dict[str, Any]:
    """Lê o arquivo canais.json e retorna o dicionário normalizado."""
    path = config_path or CANAIS_CONFIG_FILE
    if not path.exists():
        return {"padroes": {"modo_transcricao": DEFAULT_TRANSCRIPTION_MODE}, "canais": []}

    raw_text = path.read_text(encoding="utf-8-sig")
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Configuração inválida em {path}: esperado objeto JSON.")
    data.setdefault("padroes", {})
    if isinstance(data["padroes"], dict):
        data["padroes"].setdefault("modo_transcricao", DEFAULT_TRANSCRIPTION_MODE)
    data.setdefault("canais", [])
    return data


def format_hms(seconds: float) -> str:
    """Formata segundos como HH:MM:SS (ou MM:SS se desejar leitura curta, aqui sempre HH:MM:SS)."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_mmss(seconds: float) -> str:
    """Formata segundos como MM:SS (ou H:MM:SS quando passa de 1 hora)."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_srt_time(seconds: float) -> str:
    """Converte segundos para o formato de timecode SRT (HH:MM:SS,mmm)."""
    ms_total = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def list_channel_names() -> list[str]:
    """Lista os nomes de canais existentes na pasta canais/."""
    if not CANAIS_DIR.exists():
        return []
    return sorted(
        p.name
        for p in CANAIS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def get_python_console_exe() -> str:
    """Retorna python.exe mesmo quando o servidor foi iniciado via pyw/pythonw.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console_exe = exe.with_name("python.exe")
        if console_exe.exists():
            return str(console_exe)
    return str(exe)


def load_manual_markers(channel_name: str) -> list[dict[str, Any]]:
    """Lê a lista de marcadores manuais ao vivo do canal (retorna [] se não existir)."""
    safe_name = sanitize_channel_name(channel_name)
    path = CANAIS_DIR / safe_name / "marcadores_manuais.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def save_manual_markers(channel_name: str, markers: list[dict[str, Any]]) -> None:
    """Salva atomicamente a lista de marcadores manuais em marcadores_manuais.json."""
    paths = ensure_channel_structure(channel_name)
    target = paths["marcadores_manuais"]
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(markers, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(tmp), str(target))
