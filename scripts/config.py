"""Configurações centralizadas e utilitários compartilhados do projeto Stream-clipping.

Reúne caminhos do projeto, configurações de gravação, transcrição (faster-whisper),
extração de grades de frames, tipos/cores de marcadores para o Premiere Pro e
configurações de detecção automática de momentos interessantes (modo automático).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

# ==============================================================================
# CAMINHOS DO PROJETO
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
MARKETING_DIR = PROJECT_ROOT / "marketing"
CANAIS_CONFIG_FILE = PROJECT_ROOT / "canais.json"
CANAIS_EXAMPLE_FILE = PROJECT_ROOT / "canais.example.json"


def get_canais_dir() -> Path:
    """Retorna o diretório base de canais configurado em canais.json ou o padrão local."""
    try:
        if CANAIS_CONFIG_FILE.exists():
            data = json.loads(CANAIS_CONFIG_FILE.read_text(encoding="utf-8-sig"))
            custom = str(data.get("padroes", {}).get("diretorio_canais", "")).strip()
            if custom:
                p = Path(custom)
                p.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return PROJECT_ROOT / "canais"


CANAIS_DIR = get_canais_dir()

# ==============================================================================
# BINÁRIOS EXTERNOS
# ==============================================================================


def _resolve_binary(binary_name: str) -> str:
    """Resolve executable path across PATH and standard Windows user installations."""
    found = shutil.which(binary_name) or shutil.which(f"{binary_name}.exe")
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.is_dir():
            matches = list(winget_root.glob(f"**/{binary_name}.exe"))
            if matches:
                return str(matches[0])

    user_home = Path.home()
    spotdl_path = user_home / ".spotdl" / f"{binary_name}.exe"
    if spotdl_path.exists():
        return str(spotdl_path)

    standard_path = Path(r"C:\ffmpeg\bin") / f"{binary_name}.exe"
    if standard_path.exists():
        return str(standard_path)

    return binary_name


FFMPEG_BIN = _resolve_binary("ffmpeg")
FFPROBE_BIN = _resolve_binary("ffprobe")
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
# DETECÇÃO AUTOMÁTICA DE MOMENTOS INTERESSANTES
# ==============================================================================

# Modo de análise: "blocos" (manual, acumula blocos) ou "automatico" (processa assim que fecha)
DEFAULT_ANALYSIS_MODE = "blocos"

# Backend de AI: "desabilitado", "api" (OpenAI-compatible) ou "local" (Ollama)
DEFAULT_AI_BACKEND = "desabilitado"

# Score mínimo (0-100) para gerar um marcador automático
DEFAULT_AUTO_CLIP_SCORE = 65

# Deletar bloco original (.mkv) após gerar os cortes para economizar espaço em disco
DEFAULT_DELETE_BLOCK_AFTER_CLIP = False

# Palavras-chave de impacto que aumentam o score textual (regex case-insensitive)
IMPACT_KEYWORDS_RE = re.compile(
    r"\b("
    r"caral+ho|cara+l+h+o|krl|kkk+|hauha+|rsrs+|hue+|mano+|"
    r"q(ue|e)+|isso a[íi]|chapei|isnano|lend[áa]rio|imposs[íi]vel|"
    r"olh[ao] isso|n[ãa]o acredito|meu deus|nossa|perfeito|inacredit[áa]vel|"
    r"clip(a|ou|ei|isso)|clippa|salv(a|ou|ei)|grav(a|ou|ei) isso|"
    r"ganhei|matei|eliminei|first blood|clutch|ace|fl(ick|ick)|"
    r"rota[çc][ãa]o|rotina|arrumei|consertei|quebrei|top demais"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# ==============================================================================
# TRANSCRIÇÃO (FASTER-WHISPER) — BASEADO EM transcricao_whisper_v4.py
# ==============================================================================

WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "float16"
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
    channel_root = get_canais_dir() / safe_name
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
    """Lê o arquivo canais.json e retorna o dicionário normalizado.
    Se canais.json não existir, inicializa copiando o canais.example.json.
    """
    path = config_path or CANAIS_CONFIG_FILE
    if not path.exists():
        if CANAIS_EXAMPLE_FILE.exists() and path == CANAIS_CONFIG_FILE:
            try:
                shutil.copy2(CANAIS_EXAMPLE_FILE, CANAIS_CONFIG_FILE)
            except Exception:
                pass
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


def gerar_srt_trecho(
    segments: list[dict[str, Any]],
    inicio_seg: float,
    duracao_seg: float,
    destino_srt: Path,
) -> bool:
    """Gera um arquivo .srt com timestamps relativos ao clipe (iniciando em 00:00:00)."""
    fim_seg = inicio_seg + duracao_seg
    linhas: list[str] = []
    idx = 1
    for seg in segments:
        s_start = float(seg.get("start", 0.0))
        s_end = float(seg.get("end", 0.0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        if s_end > inicio_seg and s_start < fim_seg:
            rel_start = max(0.0, s_start - inicio_seg)
            rel_end = min(duracao_seg, s_end - inicio_seg)
            if rel_end > rel_start:
                linhas.append(str(idx))
                linhas.append(f"{format_srt_time(rel_start)} --> {format_srt_time(rel_end)}")
                linhas.append(text)
                linhas.append("")
                idx += 1
    if linhas:
        destino_srt.write_text("\n".join(linhas), encoding="utf-8")
        return True
    return False


def list_channel_names() -> list[str]:
    """Lista os nomes de canais existentes na pasta canais/."""
    base = get_canais_dir()
    if not base.exists():
        return []
    return sorted(
        p.name
        for p in base.iterdir()
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


# ==============================================================================
# DETECÇÃO AUTOMÁTICA DE CATEGORIA/JOGO DA LIVE
# ==============================================================================

STREAM_CATEGORY_CACHE: dict[str, dict[str, Any]] = {}
_PENDING_CATEGORY_CHECKS: set[str] = set()
_CATEGORY_LOCK = threading.Lock()


def _query_streamlink_category(clean_url: str) -> None:
    raw_cat = ""
    tag = "geral"
    cmd = [get_python_console_exe(), "-m", "streamlink", "--json", clean_url]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=SUBPROCESS_FLAGS,
        )
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            raw_cat = str(data.get("metadata", {}).get("category") or "").strip()
    except Exception:
        pass

    if raw_cat:
        lower = raw_cat.lower()
        if "grand theft auto" in lower or "gta" in lower:
            tag = "gta"
        elif "fortnite" in lower:
            tag = "fortnite"
        else:
            slug = re.sub(r"[^\w]+", "-", lower).strip("-")
            if MARKETING_DIR.exists() and (MARKETING_DIR / slug).is_dir():
                tag = slug
            else:
                tag = "geral"

    with _CATEGORY_LOCK:
        STREAM_CATEGORY_CACHE[clean_url] = {
            "raw": raw_cat,
            "tag": tag,
            "timestamp": time.time(),
        }
        _PENDING_CATEGORY_CHECKS.discard(clean_url)


def detect_stream_category(url: str, cache_ttl: float = 300.0, block: bool = False) -> dict[str, str]:
    """Detecta categoria/jogo ao vivo via streamlink com cache em memória.

    Por padrão (block=False), não trava a requisição HTTP: retorna o cache atual imediatamente
    e faz a busca em segundo plano para que a tela abra instantaneamente.
    Retorna {'raw': 'Grand Theft Auto VI', 'tag': 'gta'}.
    """
    clean_url = (url or "").strip()
    if not clean_url or "exemplo" in clean_url.lower():
        return {"raw": "", "tag": "geral"}

    now = time.time()
    with _CATEGORY_LOCK:
        cached = STREAM_CATEGORY_CACHE.get(clean_url)
        is_fresh = cached and (now - float(cached.get("timestamp", 0)) < cache_ttl)
        if is_fresh:
            return {"raw": str(cached.get("raw", "")), "tag": str(cached.get("tag", "geral"))}

        if clean_url in _PENDING_CATEGORY_CHECKS:
            if cached:
                return {"raw": str(cached.get("raw", "")), "tag": str(cached.get("tag", "geral"))}
            return {"raw": "", "tag": "geral"}

        _PENDING_CATEGORY_CHECKS.add(clean_url)

    if block:
        _query_streamlink_category(clean_url)
        with _CATEGORY_LOCK:
            res = STREAM_CATEGORY_CACHE.get(clean_url, {"raw": "", "tag": "geral"})
            return {"raw": str(res.get("raw", "")), "tag": str(res.get("tag", "geral"))}
    else:
        t = threading.Thread(
            target=_query_streamlink_category,
            args=(clean_url,),
            daemon=True,
            name=f"cat-detector-{clean_url[-12:]}",
        )
        t.start()
        if cached:
            return {"raw": str(cached.get("raw", "")), "tag": str(cached.get("tag", "geral"))}
        return {"raw": "", "tag": "geral"}

