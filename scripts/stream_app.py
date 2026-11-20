"""Servidor local e API do Stream-clipping App (Edge --app mode).

Executa em http://127.0.0.1:8795 e oferece uma interface simples para:
- Cadastrar e gerenciar canais (Twitch / YouTube) em canais.json
- Iniciar/parar gravadores ao vivo em paralelo (ChannelRecorder)
- Disparar preparo de lotes (Whisper + grades visuais) em subprocesso (libera VRAM ao terminar)
- Finalizar lotes analisados gerando o XML do Premiere Pro
- Cortar clipes .mp4 opcionais, editar estado_live.md e acompanhar logs ao vivo

Uso:
    py -3.11 stream_app.py
    py -3.11 stream_app.py --port 8795
"""

from __future__ import annotations

import argparse
import atexit
import collections
import errno
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import (
    CANAIS_CONFIG_FILE,
    CANAIS_DIR,
    DEFAULT_ANALYSIS_MODE,
    DEFAULT_AI_BACKEND,
    DEFAULT_AUTO_CLIP_SCORE,
    DEFAULT_DELETE_BLOCK_AFTER_CLIP,
    DEFAULT_QUALITY,
    DEFAULT_RECONNECT_SECONDS,
    DEFAULT_SEGMENT_MINUTES,
    DEFAULT_TRANSCRIPTION_MODE,
    PRE_TRANSCRICOES_DIRNAME,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    SUBPROCESS_FLAGS,
    VIDEO_EXTENSIONS,
    detect_stream_category,
    ensure_channel_structure,
    format_mmss,
    gerar_srt_trecho,
    get_canais_dir,
    get_python_console_exe,
    list_channel_names,
    load_channels_config,
    load_manual_markers,
    sanitize_channel_name,
    save_manual_markers,
)
from gravar_lives import ChannelRecorder

HOST = "127.0.0.1"
PORT = 8795
UI_FILE = SCRIPTS_DIR / "app_ui.html"

LOGS_LOCK = threading.Lock()
LOGS_BY_CHANNEL: dict[str, collections.deque[dict[str, str]]] = {}


def append_log(source: str, text: str, *, channel: str = "") -> None:
    """Adiciona uma linha ao buffer de logs em memória."""
    ts = datetime.now().strftime("%H:%M:%S")
    with LOGS_LOCK:
        buffer = LOGS_BY_CHANNEL.setdefault(channel, collections.deque(maxlen=250))
        buffer.append({"ts": ts, "source": str(source), "text": str(text)})


class _AppLogHandler(logging.Handler):
    """Redireciona logs do módulo logging para o buffer de logs do servidor."""

    def emit(self, record: logging.LogRecord) -> None:
        source = record.name.split(".")[-1].upper()
        append_log(source, self.format(record), channel=getattr(record, "channel", ""))


_app_log_handler = _AppLogHandler()
_app_log_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("cortar_marcadores").addHandler(_app_log_handler)
logging.getLogger("cortar_marcadores").setLevel(logging.WARNING)


def get_logs(channels: list[str], limit: int = 150) -> dict[str, list[dict[str, str]]]:
    with LOGS_LOCK:
        return {channel: list(LOGS_BY_CHANNEL.get(channel, ()))[-limit:] for channel in channels}


def clear_logs(channel: str) -> None:
    with LOGS_LOCK:
        LOGS_BY_CHANNEL.pop(channel, None)


def _detect_platform(url: str) -> str:
    low = (url or "").lower()
    if "twitch.tv" in low:
        return "twitch"
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    return "outro"


def _save_channels_config(cfg: dict[str, Any]) -> None:
    CANAIS_CONFIG_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _count_videos(folder: Path, recursive: bool = False) -> int:
    if not folder.exists():
        return 0
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    return sum(
        1 for p in iterator if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def _count_dirs(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


class RecorderManager:
    """Gerencia threads ativas de ChannelRecorder por canal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}

    def set_error(self, channel: str, message: str) -> None:
        safe = sanitize_channel_name(channel)
        with self._lock:
            if message:
                self._errors[safe] = str(message)
            else:
                self._errors.pop(safe, None)

    def clear_error(self, channel: str) -> None:
        safe = sanitize_channel_name(channel)
        with self._lock:
            self._errors.pop(safe, None)

    def get_error(self, channel: str) -> str:
        safe = sanitize_channel_name(channel)
        with self._lock:
            return self._errors.get(safe, "")

    def _prune_locked(self) -> None:
        mortos = [name for name, info in self._active.items() if not info["thread"].is_alive()]
        for name in mortos:
            del self._active[name]

    def is_recording(self, channel_name: str) -> tuple[bool, str]:
        with self._lock:
            self._prune_locked()
            info = self._active.get(channel_name)
            if info:
                return True, str(info["started_at"])
            return False, ""

    def active_count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._active)

    def get_current_position(self, channel_name: str) -> dict[str, Any] | None:
        safe = sanitize_channel_name(channel_name)
        with self._lock:
            self._prune_locked()
            info = self._active.get(safe)
            rec = info.get("recorder") if info else None
        if rec is None:
            return None
        return rec.get_current_position()

    def start_channel(self, entry: dict[str, Any], padroes: dict[str, Any]) -> tuple[bool, str]:
        name = sanitize_channel_name(str(entry.get("nome") or ""))
        url = str(entry.get("url") or "").strip()
        if not url or "EXEMPLO" in url.upper():
            return False, f"Canal '{name}' está com URL vazia ou de exemplo."

        cookies_val = entry.get("cookies")
        if not cookies_val and _detect_platform(url) == "youtube":
            cookies_val = padroes.get("cookies_youtube")

        with self._lock:
            self._prune_locked()
            if name in self._active:
                return False, f"Canal '{name}' já está gravando."
            self._errors.pop(name, None)

            stop_event = threading.Event()
            rec = ChannelRecorder(
                name=name,
                url=url,
                segment_minutes=int(
                    entry.get("duracao_bloco_minutos")
                    or padroes.get("duracao_bloco_minutos", DEFAULT_SEGMENT_MINUTES)
                ),
                quality=str(
                    entry.get("qualidade")
                    or padroes.get("qualidade", DEFAULT_QUALITY)
                ),
                engine=str(entry.get("motor") or "auto"),
                reconnect_seconds=int(
                    entry.get("intervalo_reconexao_segundos")
                    or padroes.get("intervalo_reconexao_segundos", DEFAULT_RECONNECT_SECONDS)
                ),
                cookies_file=str(cookies_val).strip() if cookies_val else None,
                stop_event=stop_event,
                on_log=lambda source, text: append_log(source, text, channel=name),
                on_error=self.set_error,
            )
            t = threading.Thread(target=rec.run_forever, name=f"rec-{name}", daemon=True)
            self._active[name] = {
                "thread": t,
                "stop_event": stop_event,
                "recorder": rec,
                "started_at": datetime.now().strftime("%H:%M:%S"),
                "url": url,
            }
            t.start()

        append_log(name, f"Gravador iniciado via App ({url}).", channel=name)
        return True, f"Gravação iniciada para '{name}'."

    def start_all_active(self) -> tuple[int, list[str]]:
        cfg = load_channels_config()
        padroes = cfg.get("padroes", {})
        started = 0
        messages: list[str] = []
        for entry in cfg.get("canais", []):
            if not isinstance(entry, dict) or not entry.get("ativo", False):
                continue
            ok, msg = self.start_channel(entry, padroes)
            if ok:
                started += 1
            messages.append(msg)
        return started, messages

    def stop_channel(self, channel_name: str, wait_timeout: float = 12.0) -> bool:
        safe = sanitize_channel_name(channel_name)
        with self._lock:
            self._prune_locked()
            info = self._active.get(safe)
            if not info:
                return False
            info["stop_event"].set()
            thread: threading.Thread = info["thread"]

        thread.join(timeout=wait_timeout)
        with self._lock:
            self._prune_locked()
        append_log(safe, "Solicitada parada do gravador pelo App.", channel=safe)
        return True

    def stop_all(self, wait_timeout: float = 12.0) -> int:
        with self._lock:
            self._prune_locked()
            items = list(self._active.items())
            for _name, info in items:
                info["stop_event"].set()

        for _name, info in items:
            info["thread"].join(timeout=wait_timeout)

        with self._lock:
            self._prune_locked()
        if items:
            append_log("APP", f"{len(items)} gravador(es) encerrado(s) com segurança.")
        return len(items)


class JobManager:
    """Executa preparar_lote, finalizar_lote e cortar_marcadores em subprocesso único."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_proc: subprocess.Popen[str] | None = None
        self._state: dict[str, Any] = {
            "running": False,
            "kind": "",
            "channel": "",
            "started_at": "",
            "last_message": "",
            "last_error": "",
            "last_exit_code": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def cancel_job(self) -> tuple[bool, str]:
        """Cancela a tarefa em execução de forma forçada e limpa a árvore de processos."""
        with self._lock:
            if not self._state["running"] or self._current_proc is None:
                return False, "Nenhuma tarefa em execução para cancelar."
            proc = self._current_proc
            kind = self._state["kind"]
            channel = self._state["channel"]

        append_log(kind.upper(), "Cancelamento de tarefa solicitado pelo usuário...", channel=channel)
        try:
            # No Windows, encerra toda a árvore do processo filho
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=SUBPROCESS_FLAGS,
                )
            else:
                proc.terminate()
                time.sleep(0.5)
                if proc.poll() is None:
                    proc.kill()
        except Exception as exc:
            append_log(kind.upper(), f"Erro ao cancelar processo: {exc}", channel=channel)
            return False, f"Falha ao cancelar: {exc}"

        return True, "Tarefa cancelada com sucesso."

    def start_job(
        self,
        kind: str,
        channel: str,
        script_name: str,
        script_args: list[str],
    ) -> tuple[bool, str]:
        script_path = SCRIPTS_DIR / script_name
        if not script_path.exists():
            return False, f"Script não encontrado: {script_path}"

        with self._lock:
            if self._state["running"]:
                return (
                    False,
                    f"Já existe uma tarefa em andamento ({self._state['kind']} - {self._state['channel']}). Aguarde concluir.",
                )
            self._state = {
                "running": True,
                "kind": kind,
                "channel": channel,
                "started_at": datetime.now().strftime("%H:%M:%S"),
                "last_message": f"Iniciando {script_name}...",
                "last_error": "",
                "last_exit_code": None,
            }

        cmd = [get_python_console_exe(), "-u", str(script_path), *script_args]
        t = threading.Thread(
            target=self._run_worker,
            args=(kind, channel, cmd),
            name=f"job-{kind}",
            daemon=True,
        )
        t.start()
        return True, f"Tarefa '{kind}' iniciada."

    def _run_worker(self, kind: str, channel: str, cmd: list[str]) -> None:
        append_log(kind.upper(), f"Executando: {' '.join(cmd)}", channel=channel)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        exit_code = -1
        last_line = ""
        error_msg = ""

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=SUBPROCESS_FLAGS,
            )
            with self._lock:
                self._current_proc = proc

            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.rstrip("\r\n")
                    if line.strip():
                        last_line = line.strip()
                        append_log(kind.upper(), last_line, channel=channel)
                        with self._lock:
                            self._state["last_message"] = last_line
            exit_code = proc.wait()
            if exit_code != 0:
                error_msg = f"Processo encerrou com código {exit_code}."
        except Exception as exc:
            error_msg = str(exc)
            append_log(kind.upper(), f"ERRO: {error_msg}", channel=channel)
        finally:
            with self._lock:
                self._current_proc = None
                self._state["running"] = False
                self._state["last_exit_code"] = exit_code
                self._state["last_error"] = error_msg
                if not error_msg and not self._state["last_message"]:
                    self._state["last_message"] = "Concluído."
            append_log(
                kind.upper(),
                f"Finalizado (código {exit_code})" + (f" - {error_msg}" if error_msg else ""),
                channel=channel,
            )


RECORDER_MGR = RecorderManager()
JOB_MGR = JobManager()
SERVER_REF: dict[str, Any] = {"server": None}
ANALYZE_CANCEL_EVENTS: dict[str, threading.Event] = {}


def _list_untranscribed_queue_files() -> list[Path]:
    """Lista blocos fechados em canais/*/02_para_processar/ sem pré-transcrição."""
    if not CANAIS_DIR.exists():
        return []
    agora = time.time()
    pendentes: list[Path] = []
    for channel_dir in sorted(CANAIS_DIR.iterdir()):
        if not channel_dir.is_dir() or channel_dir.name.startswith("."):
            continue
        dir_fila = channel_dir / "02_para_processar"
        if not dir_fila.exists():
            continue
        pre_dir = dir_fila / PRE_TRANSCRICOES_DIRNAME
        for p in sorted(dir_fila.iterdir()):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if (agora - mtime) < 4.0:
                continue
            if (pre_dir / f"{p.stem}.json").exists():
                continue
            pendentes.append(p)
    return pendentes


class AutoTranscribeWatcher:
    """Monitora 02_para_processar/ e pré-transcreve blocos quando modo_transcricao == 'automatico'."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._failed_attempts: dict[str, int] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="auto-transcribe-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        self._wake_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                cfg = load_channels_config()
                modo = str(
                    cfg.get("padroes", {}).get(
                        "modo_transcricao", DEFAULT_TRANSCRIPTION_MODE
                    )
                ).lower()

                if modo == "automatico" and not JOB_MGR.snapshot()["running"]:
                    candidatos = _list_untranscribed_queue_files()
                    por_canal: dict[str, list[Path]] = {}
                    for arquivo in candidatos:
                        if self._failed_attempts.get(str(arquivo), 0) < 2:
                            por_canal.setdefault(arquivo.parent.parent.name, []).append(arquivo)
                    for channel, arquivos in por_canal.items():
                        if self._stop_event.is_set():
                            break
                        ok, _msg = JOB_MGR.start_job(
                            "auto-whisper",
                            channel,
                            "preparar_lote.py",
                            ["--pre-transcrever", *[str(p) for p in arquivos]],
                        )
                        if not ok:
                            break
                        while (
                            not self._stop_event.is_set()
                            and JOB_MGR.snapshot()["running"]
                        ):
                            self._stop_event.wait(1.0)
                        snap = JOB_MGR.snapshot()
                        if snap.get("last_exit_code", 0) != 0:
                            for p in arquivos:
                                pre_json = (
                                    p.parent
                                    / PRE_TRANSCRICOES_DIRNAME
                                    / f"{p.stem}.json"
                                )
                                if not pre_json.exists():
                                    key = str(p)
                                    self._failed_attempts[key] = (
                                        self._failed_attempts.get(key, 0) + 1
                                    )
            except Exception as exc:
                append_log("AUTO-WHISPER", f"Erro no watcher: {exc}")

            self._wake_event.wait(5.0)
            self._wake_event.clear()


AUTO_TRANSCRIBE = AutoTranscribeWatcher()


def _resolve_transcript_json(mkv_path: Path, pre_dir: Path) -> Path | None:
    """Finds the transcript JSON for a video block, checking multiple locations.

    Search order:
      1. _pre_transcricoes/<stem>.json  (auto-transcribed before batch)
      2. 03_em_processamento/<lote>/transcricoes/<stem>.json  (moved to batch)
      3. 04_para_edicao/<lote>/transcricoes/<stem>.json       (batch finalised)
    """
    # 1. pre-transcription cache (most common for auto-clip mode)
    candidate = pre_dir / f"{mkv_path.stem}.json"
    if candidate.exists():
        return candidate

    # 2 & 3. scan processing and editing batch folders
    channel_dir = mkv_path.parent.parent  # canais/<canal>/
    for batch_root in ("03_em_processamento", "04_para_edicao"):
        batch_base = channel_dir / batch_root
        if not batch_base.exists():
            continue
        for lote_dir in sorted(batch_base.iterdir(), reverse=True):
            if not lote_dir.is_dir():
                continue
            tr = lote_dir / "transcricoes" / f"{mkv_path.stem}.json"
            if tr.exists():
                return tr
    return None


def select_top_momentos(
    momentos: list[dict[str, Any]],
    max_clips: int = 5,
    min_distance_sec: float = 35.0,
) -> list[dict[str, Any]]:
    """Seleciona os melhores momentos com maior score, garantindo distribuição temporal sem sobreposição."""
    if not momentos:
        return []
    sorted_m = sorted(momentos, key=lambda x: float(x.get("score", 0)), reverse=True)
    selected: list[dict[str, Any]] = []
    for m in sorted_m:
        sec = float(m.get("segundo", 0))
        if any(abs(sec - float(s.get("segundo", 0))) < min_distance_sec for s in selected):
            continue
        selected.append(m)
        if len(selected) >= max_clips:
            break
    selected.sort(key=lambda x: float(x.get("segundo", 0)))
    return selected


class AutoClipWatcher:
    """Monitora blocos pré-transcritos e corta clipes automaticamente quando modo_analise == 'automatico'."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._processed: set[str] = set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="auto-clip-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        self._wake_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            ch_name = ""
            try:
                cfg = load_channels_config()
                padroes = cfg.get("padroes", {})
                modo = str(padroes.get("modo_analise", DEFAULT_ANALYSIS_MODE)).lower()

                if modo == "automatico":
                    from auto_clip import analyze_block, save_auto_markers
                    from cortar_marcadores import _cortar_trecho, _slugify
                    from premiere_xml import probe_video

                    min_score = float(padroes.get("auto_clip_min_score", DEFAULT_AUTO_CLIP_SCORE))
                    custom_output = str(padroes.get("output_dir", "")).strip()

                    for ch_name in list_channel_names():
                        if self._stop_event.is_set():
                            break
                        ch_paths = ensure_channel_structure(ch_name)
                        dir_fila = ch_paths["02_para_processar"]
                        pre_dir = dir_fila / PRE_TRANSCRICOES_DIRNAME
                        if not dir_fila.exists():
                            continue

                        for mkv_path in sorted(dir_fila.iterdir()):
                            if self._stop_event.is_set():
                                break
                            if not mkv_path.is_file() or mkv_path.suffix.lower() not in VIDEO_EXTENSIONS:
                                continue

                            transcript_json = _resolve_transcript_json(mkv_path, pre_dir)
                            # Se modo de transcrição for automático, aguarda o Whisper concluir antes de analisar.
                            # Regra: se não há transcrição ainda, só avança se o JOB_MGR não estiver rodando
                            # (i.e., Whisper terminou ou nunca foi disparado) E o arquivo já é antigo o suficiente.
                            modo_tr = str(padroes.get("modo_transcricao", "automatico")).lower()
                            if modo_tr == "automatico" and transcript_json is None:
                                # Se há um job rodando (Whisper provavelmente), aguarda sem timeout
                                if JOB_MGR.snapshot()["running"]:
                                    continue
                                # Sem job rodando: aguarda pelo menos 60s após o arquivo parar de ser escrito
                                # para garantir que o gravador fechou o segmento antes de analisar
                                try:
                                    age = time.time() - mkv_path.stat().st_mtime
                                except OSError:
                                    age = 0
                                if age < 60:
                                    continue

                            marker_file = ch_paths["root"] / "marcadores_automaticos" / f"{mkv_path.stem}.json"
                            # Reanalize if: transcript is now available but the previous analysis was audio-only
                            # (text_score == 0 means Whisper hadn't run yet when the marker was written).
                            should_reanalyze = False
                            if marker_file.exists():
                                try:
                                    old_m = json.loads(marker_file.read_text(encoding="utf-8"))
                                    was_audio_only = old_m.get("text_score", 0) == 0 and old_m.get("ai_score") is None
                                    if transcript_json is not None and was_audio_only:
                                        should_reanalyze = True
                                except Exception:
                                    pass

                            if should_reanalyze:
                                self._processed.discard(str(mkv_path))
                            elif marker_file.exists() or str(mkv_path) in self._processed:
                                continue

                            self._processed.add(str(mkv_path))
                            append_log("AUTO-CLIP", f"Analisando momentos: {ch_name}/{mkv_path.name}...", channel=ch_name)
                            entry, _ = _find_channel_entry(ch_name)
                            canal_jogo = str(entry.get("jogo") or "auto").strip().lower() if entry else "auto"
                            canal_url = str(entry.get("url") or "") if entry else ""
                            if canal_jogo in ("auto", "") and canal_url:
                                cat_info = detect_stream_category(canal_url)
                                resolved_jogo = cat_info.get("tag") or "geral"
                            else:
                                resolved_jogo = canal_jogo
                            analysis = analyze_block(ch_name, mkv_path, transcript_json, padroes, jogo=resolved_jogo)
                            save_auto_markers(ch_name, mkv_path.stem, analysis)

                            score = float(analysis.get("score", 0.0))
                            momentos = analysis.get("momentos", [])
                            append_log("AUTO-CLIP", f"[{ch_name}] Score: {score:.0f}/100 | Momentos: {len(momentos)}", channel=ch_name)

                            # Extrai clips se ha momentos detectados, independente do score global.
                            # O score global e um indicador; os picos individuais ja foram filtrados em auto_clip.py.
                            best_m_score = max((float(m.get("score", 0)) for m in momentos), default=0.0)
                            if momentos and best_m_score >= min_score / 2:
                                if custom_output:
                                    out_base = Path(custom_output) / ch_name / "clips_automaticos"
                                else:
                                    out_base = ch_paths["05_cortes_extraidos"] / f"auto_{mkv_path.stem}"
                                out_base.mkdir(parents=True, exist_ok=True)

                                try:
                                    dur_video = float(probe_video(mkv_path)["duration"])
                                    cortes_feitos = 0
                                    top_momentos = select_top_momentos(momentos, max_clips=5, min_distance_sec=35.0)
                                    for idx, m in enumerate(top_momentos, start=1):
                                        center = float(m.get("segundo", 0))
                                        inicio = max(0.0, center - 12.0)
                                        dur_corte = min(dur_video - inicio, 30.0)
                                        if dur_corte <= 2.0:
                                            continue
                                        desc = str(m.get("descricao") or f"momento_{idx}")
                                        slug = _slugify(desc, max_len=36)
                                        clip_file = out_base / f"{idx:02d}_{slug}_{int(center)}s.mp4"
                                        _cortar_trecho(mkv_path, clip_file, inicio, dur_corte, modo_copia=False, encoder="auto", channel=ch_name)

                                        # Resolve transcript at cut time — file may have been moved to a batch folder
                                        tr_at_cut = _resolve_transcript_json(mkv_path, pre_dir)
                                        try:
                                            if tr_at_cut is not None and tr_at_cut.exists():
                                                raw_tr = json.loads(tr_at_cut.read_text(encoding="utf-8"))
                                                segs = raw_tr if isinstance(raw_tr, list) else raw_tr.get("segments", [])
                                                gerar_srt_trecho(segs, inicio, dur_corte, clip_file.with_suffix(".srt"))
                                        except Exception:
                                            pass

                                        append_log("AUTO-CLIP", f"★ Clip e legenda (.srt) extraídos ({format_mmss(inicio)} -> {format_mmss(inicio+dur_corte)}): {clip_file.name}", channel=ch_name)
                                        cortes_feitos += 1

                                    deletar_bloco = _limpar_fila_apos_clip(entry, padroes)
                                    # Only delete the source block if the clip was made WITH transcript context.
                                    # Without a transcript the auto-clip result is audio-only and may be poor quality;
                                    # keeping the file allows a re-analysis once Whisper finishes.
                                    had_transcript = transcript_json is not None
                                    if deletar_bloco and cortes_feitos > 0 and had_transcript:
                                        try:
                                            mkv_path.unlink(missing_ok=True)
                                            append_log("AUTO-CLIP", f"Bloco da fila (02_para_processar) removido para economizar espaço: {mkv_path.name}", channel=ch_name)
                                        except Exception as del_err:
                                            append_log("AUTO-CLIP", f"Aviso ao remover bloco da fila: {del_err}", channel=ch_name)
                                except Exception as exc:
                                    append_log("AUTO-CLIP", f"Aviso ao cortar clip: {exc}", channel=ch_name)

            except Exception as exc:
                append_log("AUTO-CLIP", f"Erro no auto-clip: {exc}", channel=ch_name)

            self._wake_event.wait(6.0)
            self._wake_event.clear()


AUTO_CLIP = AutoClipWatcher()


def _scan_channel_lotes(channel_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lê os lotes em 03_em_processamento/ e 04_para_edicao/ de um canal."""
    lotes_proc: list[dict[str, Any]] = []
    dir_proc = channel_dir / "03_em_processamento"
    if dir_proc.exists():
        for lote_dir in sorted(dir_proc.iterdir(), reverse=True):
            if not lote_dir.is_dir():
                continue
            lotes_proc.append(
                {
                    "lote_id": lote_dir.name,
                    "videos_count": _count_videos(lote_dir),
                    "has_decision": (lote_dir / "decisao_lote.json").exists(),
                    "has_pacote": (lote_dir / "_pacote_ia.md").exists(),
                }
            )

    lotes_edicao: list[dict[str, Any]] = []
    dir_edicao = channel_dir / "04_para_edicao"
    dir_cortes = channel_dir / "05_cortes_extraidos"
    if dir_edicao.exists():
        for lote_dir in sorted(dir_edicao.iterdir(), reverse=True):
            if not lote_dir.is_dir():
                continue
            xmls = sorted(p.name for p in lote_dir.glob("*.xml"))
            cortes_lote_dir = dir_cortes / lote_dir.name
            lotes_edicao.append(
                {
                    "lote_id": lote_dir.name,
                    "videos_count": _count_videos(lote_dir),
                    "xml_files": xmls,
                    "resumo_exists": (lote_dir / "resumo_lote.md").exists(),
                    "cortes_count": _count_videos(cortes_lote_dir),
                }
            )

    return lotes_proc, lotes_edicao


def _get_local_ip() -> str:
    """Retorna o IP da maquina na rede local para facilitar acesso remoto."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def build_state_payload() -> dict[str, Any]:
    cfg = load_channels_config()
    padroes = cfg.get("padroes", {})
    config_entries: dict[str, dict[str, Any]] = {}
    ordered_names: list[str] = []

    for entry in cfg.get("canais", []):
        if not isinstance(entry, dict):
            continue
        name = sanitize_channel_name(str(entry.get("nome") or ""))
        if name not in config_entries:
            config_entries[name] = entry
            ordered_names.append(name)

    base_canais = get_canais_dir()
    canais_out: list[dict[str, Any]] = []
    for name in ordered_names:
        entry = config_entries.get(name)
        in_config = entry is not None
        url = str((entry or {}).get("url") or "").strip()
        channel_dir = base_canais / name

        counts = {
            "01_gravando": _count_videos(channel_dir / "01_gravando"),
            "02_para_processar": _count_videos(channel_dir / "02_para_processar"),
            "03_em_processamento": _count_dirs(channel_dir / "03_em_processamento"),
            "04_para_edicao": _count_dirs(channel_dir / "04_para_edicao"),
            "05_cortes_extraidos": _count_videos(channel_dir / "05_cortes_extraidos", recursive=True),
            "06_arquivo_descartados": _count_dirs(channel_dir / "06_arquivo_descartados"),
        }
        lotes_proc, lotes_edicao = _scan_channel_lotes(channel_dir)
        recording, rec_since = RECORDER_MGR.is_recording(name)

        dir_fila = channel_dir / "02_para_processar"
        pre_dir = dir_fila / PRE_TRANSCRICOES_DIRNAME
        pre_count = 0
        if dir_fila.exists() and pre_dir.exists():
            pre_count = sum(
                1
                for p in dir_fila.iterdir()
                if p.is_file()
                and p.suffix.lower() in VIDEO_EXTENSIONS
                and (pre_dir / f"{p.stem}.json").exists()
            )

        cat_info = detect_stream_category(url) if url else {"raw": "", "tag": "geral"}
        canais_out.append(
            {
                "nome": name,
                "url": url,
                "plataforma": _detect_platform(url),
                "ativo": bool((entry or {}).get("ativo", False)),
                "jogo": str((entry or {}).get("jogo") or "auto").strip().lower(),
                "deletar_bloco_apos_clip": _limpar_fila_apos_clip(entry, padroes),
                "categoria_detectada": cat_info.get("raw", ""),
                "tag_detectada": cat_info.get("tag", "geral"),
                "duracao_bloco_minutos": int(
                    (entry or {}).get(
                        "duracao_bloco_minutos",
                        padroes.get("duracao_bloco_minutos", DEFAULT_SEGMENT_MINUTES),
                    )
                ),
                "qualidade": str(
                    (entry or {}).get(
                        "qualidade", padroes.get("qualidade", DEFAULT_QUALITY)
                    )
                ),
                "motor": str((entry or {}).get("motor") or "auto"),
                "cookies": str((entry or {}).get("cookies") or ""),
                "in_config": in_config,
                "recording": recording,
                "recording_since": rec_since,
                "current_segment": RECORDER_MGR.get_current_position(name) if recording else None,
                "manual_markers": load_manual_markers(name),
                "last_error": RECORDER_MGR.get_error(name),
                "pre_transcribed_count": pre_count,
                "counts": counts,
                "lotes_em_processamento": lotes_proc,
                "lotes_edicao": lotes_edicao,
            }
        )

    return {
        "project_root": str(PROJECT_ROOT),
        "diretorio_canais_efetivo": str(get_canais_dir()),
        "padroes": {
            "modo_transcricao": padroes.get("modo_transcricao", DEFAULT_TRANSCRIPTION_MODE),
            "modo_analise": padroes.get("modo_analise", DEFAULT_ANALYSIS_MODE),
            "ai_backend": padroes.get("ai_backend", DEFAULT_AI_BACKEND),
            "ai_api_url": padroes.get("ai_api_url", ""),
            "ai_api_key": padroes.get("ai_api_key", ""),
            "ai_modelo": padroes.get("ai_modelo", "gemma3:4b"),
            "ai_local_url": padroes.get("ai_local_url", "http://localhost:11434"),
            "auto_clip_min_score": int(padroes.get("auto_clip_min_score", DEFAULT_AUTO_CLIP_SCORE)),
            "deletar_bloco_apos_clip": bool(padroes.get("deletar_bloco_apos_clip", DEFAULT_DELETE_BLOCK_AFTER_CLIP)),
            "diretorio_canais": padroes.get("diretorio_canais", ""),
            "output_dir": padroes.get("output_dir", ""),
            "duracao_bloco_minutos": int(padroes.get("duracao_bloco_minutos", DEFAULT_SEGMENT_MINUTES)),
            "qualidade": padroes.get("qualidade", DEFAULT_QUALITY),
            "intervalo_reconexao_segundos": int(padroes.get("intervalo_reconexao_segundos", DEFAULT_RECONNECT_SECONDS)),
        },
        "canais": canais_out,
        "recording_active_count": RECORDER_MGR.active_count(),
        "job": JOB_MGR.snapshot(),
        "logs": get_logs([canal["nome"] for canal in canais_out]),
        "server_info": {
            "port": PORT,
            "url_local": f"http://127.0.0.1:{PORT}",
            "url_network": f"http://{_get_local_ip()}:{PORT}",
        },
    }


def _limpar_fila_apos_clip(entry, padroes) -> bool:
    valor_padrao = padroes.get("deletar_bloco_apos_clip", DEFAULT_DELETE_BLOCK_AFTER_CLIP)
    return bool((entry or {}).get("deletar_bloco_apos_clip", valor_padrao))


def _find_channel_entry(name: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cfg = load_channels_config()
    safe = sanitize_channel_name(name)
    for entry in cfg.get("canais", []):
        if isinstance(entry, dict) and sanitize_channel_name(str(entry.get("nome") or "")) == safe:
            return entry, cfg
    return None, cfg


def _validate_safe_project_path(raw_path: str) -> Path:
    candidate = Path(raw_path).resolve()
    root_resolved = PROJECT_ROOT.resolve()
    if not candidate.is_relative_to(root_resolved):
        raise PermissionError("Acesso fora da pasta do projeto não é permitido.")
    return candidate


class StreamAppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Silencia logs padrão do BaseHTTPRequestHandler no terminal
        return

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, raw, "application/json; charset=utf-8")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Payload JSON deve ser um objeto.")
        return data

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            if path in ("/", "/index.html"):
                if not UI_FILE.exists():
                    self._send_bytes(
                        HTTPStatus.NOT_FOUND,
                        b"app_ui.html nao encontrado",
                        "text/plain; charset=utf-8",
                    )
                    return
                html_bytes = UI_FILE.read_bytes()
                self._send_bytes(HTTPStatus.OK, html_bytes, "text/html; charset=utf-8")
                return

            if path.startswith("/assets/"):
                asset_name = path[len("/assets/"):]
                asset_path = SCRIPTS_DIR / "assets" / asset_name
                if asset_path.exists() and asset_path.is_file():
                    _ext = asset_path.suffix.lower()
                    _mime = {
                        ".svg": "image/svg+xml",
                        ".ico": "image/x-icon",
                        ".png": "image/png",
                    }.get(_ext, "application/octet-stream")
                    self._send_bytes(HTTPStatus.OK, asset_path.read_bytes(), _mime)
                else:
                    self._send_bytes(HTTPStatus.NOT_FOUND, b"asset not found", "text/plain")
                return

            if path == "/api/state":
                self._send_json(HTTPStatus.OK, build_state_payload())
                return

            if path == "/api/channel/details":
                qs = urllib.parse.parse_qs(parsed.query)
                raw_ch = (qs.get("channel") or [""])[0]
                if not raw_ch:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Parâmetro 'channel' obrigatório."})
                    return
                safe = sanitize_channel_name(raw_ch)
                channel_dir = CANAIS_DIR / safe
                estado_path = channel_dir / "estado_live.md"
                estado_text = (
                    estado_path.read_text(encoding="utf-8")
                    if estado_path.exists()
                    else ""
                )

                fila_arquivos: list[dict[str, Any]] = []
                dir_fila = channel_dir / "02_para_processar"
                pre_dir = dir_fila / PRE_TRANSCRICOES_DIRNAME
                if dir_fila.exists():
                    for p in sorted(dir_fila.iterdir()):
                        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                            st = p.stat()
                            fila_arquivos.append(
                                {
                                    "name": p.name,
                                    "size_mb": round(st.st_size / (1024 * 1024), 1),
                                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                                    "pre_transcribed": (pre_dir / f"{p.stem}.json").exists(),
                                }
                            )

                resumos: list[dict[str, str]] = []
                dir_edicao = channel_dir / "04_para_edicao"
                if dir_edicao.exists():
                    for lote_dir in sorted(dir_edicao.iterdir(), reverse=True)[:8]:
                        if not lote_dir.is_dir():
                            continue
                        r_path = lote_dir / "resumo_lote.md"
                        if r_path.exists():
                            resumos.append(
                                {
                                    "lote_id": lote_dir.name,
                                    "content": r_path.read_text(encoding="utf-8"),
                                }
                            )

                self._send_json(
                    HTTPStatus.OK,
                    {
                        "channel": safe,
                        "estado_live": estado_text,
                        "fila_arquivos": fila_arquivos,
                        "resumos": resumos,
                    },
                )
                return

            if path == "/api/ollama/status":
                self._handle_ollama_status()
                return

            if path == "/api/browse/folder":
                self._handle_browse_folder()
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint não encontrado."})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"JSON inválido: {exc}"})
            return

        try:
            if path == "/api/job/cancel":
                self._handle_job_cancel(body)
                return
            if path == "/api/channel/set-jogo":
                self._handle_set_channel_jogo(body)
                return
            if path == "/api/browse/folder":
                self._handle_browse_folder()
                return
            if path == "/api/channel/transcribe-queue":
                self._handle_transcribe_queue(body)
                return
            if path == "/api/channel/analyze-clips":
                self._handle_analyze_clips(body)
                return
            if path == "/api/channels/save":
                self._handle_channels_save(body)
                return
            if path == "/api/settings":
                self._handle_settings(body)
                return
            if path == "/api/channels/delete":
                self._handle_channels_delete(body)
                return
            if path == "/api/record/start":
                self._handle_record_start(body)
                return
            if path == "/api/record/stop":
                self._handle_record_stop(body)
                return
            if path == "/api/prepare":
                self._handle_prepare(body)
                return
            if path == "/api/finalize":
                self._handle_finalize(body)
                return
            if path == "/api/lote/auto-analyze":
                self._handle_lote_auto_analyze(body)
                return
            if path == "/api/cut":
                self._handle_cut(body)
                return
            if path == "/api/channel/estado":
                self._handle_save_estado(body)
                return
            if path == "/api/markers/add":
                self._handle_markers_add(body)
                return
            if path == "/api/markers/delete":
                self._handle_markers_delete(body)
                return
            if path == "/api/open-folder":
                self._handle_open_folder(body)
                return
            if path == "/api/open-browser":
                self._handle_open_browser()
                return
            if path == "/api/ollama/pull":
                self._handle_ollama_pull(body)
                return
            if path == "/api/ollama/install":
                self._handle_ollama_install()
                return
            if path == "/api/logs/clear":
                channel = str(body.get("channel") or "").strip()
                if not channel:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe o canal."})
                    return
                clear_logs(sanitize_channel_name(channel))
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/shutdown":
                self._handle_shutdown()
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint não encontrado."})
        except PermissionError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _handle_channels_save(self, body: dict[str, Any]) -> None:
        cfg = load_channels_config()
        if isinstance(body.get("padroes"), dict):
            cfg["padroes"].update(body["padroes"])

        ch = body.get("channel")
        if isinstance(ch, dict):
            nome = sanitize_channel_name(str(ch.get("nome") or ""))
            url = str(ch.get("url") or "").strip()
            if not url:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "A URL da live é obrigatória."})
                return

            novo_item: dict[str, Any] = {
                "nome": nome,
                "url": url,
                "deletar_bloco_apos_clip": bool(ch.get("deletar_bloco_apos_clip", False)),
                "ativo": bool(ch.get("ativo", True)),
                "motor": str(ch.get("motor") or "auto"),
                "duracao_bloco_minutos": max(
                    1,
                    int(
                        ch.get("duracao_bloco_minutos")
                        or cfg["padroes"].get("duracao_bloco_minutos", DEFAULT_SEGMENT_MINUTES)
                    ),
                ),
                "qualidade": str(ch.get("qualidade") or cfg["padroes"].get("qualidade", DEFAULT_QUALITY)),
            }
            cookies = str(ch.get("cookies") or "").strip()
            if cookies or _detect_platform(url) == "youtube":
                novo_item["cookies"] = cookies

            original_raw = str(body.get("original_name") or nome)
            original_safe = sanitize_channel_name(original_raw)

            canais = [c for c in cfg.get("canais", []) if isinstance(c, dict)]
            replaced = False
            for idx, existing in enumerate(canais):
                ex_name = sanitize_channel_name(str(existing.get("nome") or ""))
                if ex_name in (original_safe, nome):
                    if "deletar_bloco_apos_clip" not in ch:
                        novo_item["deletar_bloco_apos_clip"] = _limpar_fila_apos_clip(existing, cfg["padroes"])
                    if "jogo" in existing:
                        novo_item["jogo"] = existing["jogo"]
                    canais[idx] = novo_item
                    replaced = True
                    break
            if not replaced:
                canais.append(novo_item)
            cfg["canais"] = canais

            ensure_channel_structure(nome)
            append_log("APP", f"Canal '{nome}' salvo em canais.json.", channel=nome)

        _save_channels_config(cfg)
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_settings(self, body: dict[str, Any]) -> None:
        cfg = load_channels_config()
        padroes = cfg.setdefault("padroes", {})

        # --- Transcrição ---
        modo = body.get("modo_transcricao")
        if modo is not None:
            modo_norm = str(modo).strip().lower()
            if modo_norm not in ("manual", "automatico"):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "modo_transcricao inválido. Use 'manual' ou 'automatico'."},
                )
                return
            padroes["modo_transcricao"] = modo_norm
            append_log("APP", f"Modo de transcrição alterado para: {modo_norm}.")
            if modo_norm == "automatico":
                AUTO_TRANSCRIBE.wake()

        # --- Análise automática de momentos ---
        modo_analise = body.get("modo_analise")
        if modo_analise is not None:
            modo_analise_norm = str(modo_analise).strip().lower()
            if modo_analise_norm not in ("blocos", "automatico"):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "modo_analise inválido. Use 'blocos' ou 'automatico'."},
                )
                return
            padroes["modo_analise"] = modo_analise_norm
            append_log("APP", f"Modo de análise alterado para: {modo_analise_norm}.")

        # --- AI backend ---
        ai_backend = body.get("ai_backend")
        if ai_backend is not None:
            ai_backend_norm = str(ai_backend).strip().lower()
            if ai_backend_norm not in ("desabilitado", "api", "local", "local_custom"):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "ai_backend inválido. Use 'desabilitado', 'api', 'local' ou 'local_custom'."},
                )
                return
            padroes["ai_backend"] = ai_backend_norm
            append_log("APP", f"AI backend alterado para: {ai_backend_norm}.")

        for key in ("ai_api_url", "ai_api_key", "ai_modelo", "ai_local_url"):
            if body.get(key) is not None:
                padroes[key] = str(body[key]).strip()

        if body.get("auto_clip_min_score") is not None:
            score = int(body["auto_clip_min_score"])
            padroes["auto_clip_min_score"] = max(0, min(100, score))

        if body.get("deletar_bloco_apos_clip") is not None:
            padroes["deletar_bloco_apos_clip"] = bool(body["deletar_bloco_apos_clip"])
            append_log("APP", f"Deletar bloco da fila pós-clip: {'Ativado' if padroes['deletar_bloco_apos_clip'] else 'Desativado'}")

        # --- Pasta de canais / gravações ---
        if body.get("diretorio_canais") is not None:
            raw_dir = str(body["diretorio_canais"]).strip()
            if raw_dir and not Path(raw_dir).exists():
                try:
                    Path(raw_dir).mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"Não foi possível criar diretorio_canais: {exc}"},
                    )
                    return
            padroes["diretorio_canais"] = raw_dir
            append_log("APP", f"Diretório de canais configurado: {raw_dir or '(padrão: canais/ no projeto)'}")

        # --- Pasta de saída de cortes ---
        if body.get("output_dir") is not None:
            raw_dir = str(body["output_dir"]).strip()
            if raw_dir and not Path(raw_dir).exists():
                try:
                    Path(raw_dir).mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"Não foi possível criar output_dir: {exc}"},
                    )
                    return
            padroes["output_dir"] = raw_dir
            append_log("APP", f"Pasta de saída configurada: {raw_dir or '(padrão)'}")

        # --- Gravação ---
        if body.get("duracao_bloco_minutos") is not None:
            padroes["duracao_bloco_minutos"] = max(1, int(body["duracao_bloco_minutos"]))
        if body.get("qualidade") is not None:
            padroes["qualidade"] = str(body["qualidade"]).strip() or DEFAULT_QUALITY

        _save_channels_config(cfg)
        self._send_json(HTTPStatus.OK, {"ok": True, "padroes": padroes})

    def _handle_channels_delete(self, body: dict[str, Any]) -> None:
        nome = sanitize_channel_name(str(body.get("channel") or ""))
        if not nome:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Canal não informado."})
            return
        RECORDER_MGR.stop_channel(nome, wait_timeout=5.0)
        cfg = load_channels_config()
        cfg["canais"] = [
            c
            for c in cfg.get("canais", [])
            if isinstance(c, dict) and sanitize_channel_name(str(c.get("nome") or "")) != nome
        ]
        _save_channels_config(cfg)

        delete_files = bool(body.get("delete_files") or body.get("delete_folder"))
        if delete_files:
            canal_dir = get_canais_dir() / nome
            if canal_dir.exists():
                shutil.rmtree(canal_dir, ignore_errors=True)
                append_log("APP", f"Canal '{nome}' e seus arquivos apagados do disco.")
            else:
                append_log("APP", f"Canal '{nome}' removido de canais.json.")
        else:
            append_log("APP", f"Canal '{nome}' removido de canais.json (pastas preservadas).")

        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_browse_folder(self) -> None:
        result_path = [""]

        def _ask() -> None:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                selected = filedialog.askdirectory(title="Selecione a pasta")
                root.destroy()
                if selected:
                    result_path[0] = str(Path(selected).resolve())
            except Exception as exc:
                append_log("APP", f"Erro no seletor de pasta: {exc}")

        t = threading.Thread(target=_ask, daemon=True)
        t.start()
        t.join(timeout=60)
        self._send_json(HTTPStatus.OK, {"path": result_path[0]})

    def _handle_transcribe_queue(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        files = body.get("files")
        if files and isinstance(files, list):
            # Processa arquivos específicos da fila
            ch_paths = ensure_channel_structure(channel) if channel else None
            fila_dir = ch_paths["02_para_processar"] if ch_paths else None
            target_paths: list[str] = []
            for f in files:
                p = (fila_dir / Path(f).name) if fila_dir else Path(f)
                if p.is_file():
                    target_paths.append(str(p))
            if not target_paths:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Nenhum arquivo selecionado válido encontrado na fila."})
                return
            args = ["--pre-transcrever", *target_paths]
            if channel:
                args = ["--canal", channel, *args]
        else:
            args = ["--canal", channel, "--pre-transcrever"] if channel else ["--todos", "--pre-transcrever"]

        ok, msg = JOB_MGR.start_job("transcrever", channel, "preparar_lote.py", args)
        if not ok:
            self._send_json(HTTPStatus.CONFLICT, {"error": msg})
            return
        append_log("APP", f"Transcrição manual da fila iniciada para '{channel or 'todos'}'.", channel=channel)
        self._send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    def _handle_analyze_clips(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        if not channel:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel'."})
            return

        cancel_ev = threading.Event()
        ANALYZE_CANCEL_EVENTS[channel] = cancel_ev

        def _worker() -> None:
            append_log("AUTO-CLIP", f"Iniciando análise manual de cortes para '{channel}'...", channel=channel)
            try:
                from auto_clip import analyze_block, save_auto_markers
                from cortar_marcadores import _cortar_trecho, _slugify
                from premiere_xml import probe_video

                cfg = load_channels_config()
                padroes = cfg.get("padroes", {})
                min_score = float(padroes.get("auto_clip_min_score", DEFAULT_AUTO_CLIP_SCORE))
                custom_output = str(padroes.get("output_dir", "")).strip()

                # Busca tag de jogo do canal (ou detecta ao vivo se auto)
                entry, _ = _find_channel_entry(channel)
                canal_jogo = str(entry.get("jogo") or "auto").strip().lower() if entry else "auto"
                canal_url = str(entry.get("url") or "") if entry else ""
                if canal_jogo in ("auto", "") and canal_url:
                    cat_info = detect_stream_category(canal_url)
                    resolved_jogo = cat_info.get("tag") or "geral"
                else:
                    resolved_jogo = canal_jogo

                ch_paths = ensure_channel_structure(channel)
                dir_fila = ch_paths["02_para_processar"]
                pre_dir = dir_fila / PRE_TRANSCRICOES_DIRNAME
                if not dir_fila.exists():
                    append_log("AUTO-CLIP", f"Pasta 02_para_processar não encontrada em {channel}.", channel=channel)
                    return

                # Se veio lista específica de arquivos na requisição
                req_files = body.get("files")
                if req_files and isinstance(req_files, list):
                    mkv_files = [dir_fila / Path(f).name for f in req_files if (dir_fila / Path(f).name).is_file() and Path(f).suffix.lower() in VIDEO_EXTENSIONS]
                    is_from_lote = False
                else:
                    mkv_files = [p for p in sorted(dir_fila.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS] if dir_fila.exists() else []
                    is_from_lote = False
                    if not mkv_files:
                        dir_proc = ch_paths["03_em_processamento"]
                        if dir_proc.exists():
                            for lote_folder in sorted(dir_proc.iterdir(), reverse=True):
                                if lote_folder.is_dir():
                                    l_files = [p for p in sorted(lote_folder.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
                                    if l_files:
                                        mkv_files = l_files
                                        pre_dir = lote_folder / "transcricoes"
                                        is_from_lote = True
                                        append_log("AUTO-CLIP", f"Fila 02_para_processar vazia. Analisando {len(mkv_files)} vídeo(s) do lote '{lote_folder.name}'...", channel=channel)
                                        break

                if not mkv_files:
                    append_log("AUTO-CLIP", f"Nenhum vídeo na fila ou em lotes para {channel}.", channel=channel)
                    return

                cortes_total = 0
                for mkv_path in mkv_files:
                    if cancel_ev.is_set():
                        append_log("AUTO-CLIP", f"Análise de cortes interrompida pelo usuário para '{channel}'.", channel=channel)
                        break

                    transcript_json = _resolve_transcript_json(mkv_path, pre_dir)
                    append_log("AUTO-CLIP", f"Analisando: {mkv_path.name}...", channel=channel)
                    analysis = analyze_block(
                        channel,
                        mkv_path,
                        transcript_json,
                        padroes,
                        jogo=resolved_jogo,
                    )
                    save_auto_markers(channel, mkv_path.stem, analysis)
                    score = float(analysis.get("score", 0.0))
                    momentos = analysis.get("momentos", [])
                    append_log("AUTO-CLIP", f"[{channel}] {mkv_path.name}: Score {score:.0f}/100 | {len(momentos)} momentos", channel=channel)

                    if cancel_ev.is_set():
                        break

                    # Extrai clips se ha momentos detectados, independente do score global.
                    # Ordena por score para pegar os melhores primeiro.
                    best_m_score = max((float(m.get("score", 0)) for m in momentos), default=0.0)
                    if momentos and best_m_score >= min_score / 2:
                        if custom_output:
                            out_base = Path(custom_output) / channel / "clips_extraidos"
                        else:
                            out_base = ch_paths["05_cortes_extraidos"] / f"cortes_{mkv_path.stem}"
                        out_base.mkdir(parents=True, exist_ok=True)

                        cortes_bloco = 0
                        try:
                            dur_video = float(probe_video(mkv_path)["duration"])
                            top_momentos = select_top_momentos(momentos, max_clips=5, min_distance_sec=35.0)
                            for idx, m in enumerate(top_momentos, start=1):
                                if cancel_ev.is_set():
                                    break
                                center = float(m.get("segundo", 0))
                                inicio = max(0.0, center - 12.0)
                                dur_corte = min(dur_video - inicio, 35.0)
                                if dur_corte <= 2.0:
                                    continue
                                desc = str(m.get("descricao") or f"momento_{idx}")
                                slug = _slugify(desc, max_len=36)
                                clip_file = out_base / f"{idx:02d}_{slug}_{int(center)}s.mp4"
                                _cortar_trecho(mkv_path, clip_file, inicio, dur_corte, modo_copia=False, encoder="auto", channel=channel)

                                # Resolve transcript at cut time — file may have moved to a batch folder
                                tr_at_cut = _resolve_transcript_json(mkv_path, pre_dir)
                                if tr_at_cut is not None and tr_at_cut.exists():
                                    try:
                                        raw_tr = json.loads(tr_at_cut.read_text(encoding="utf-8"))
                                        segs = raw_tr if isinstance(raw_tr, list) else raw_tr.get("segments", [])
                                        gerar_srt_trecho(segs, inicio, dur_corte, clip_file.with_suffix(".srt"))
                                    except Exception:
                                        pass

                                append_log("AUTO-CLIP", f"★ Clip e legenda salvos: {clip_file.name}", channel=channel)
                                cortes_total += 1
                                cortes_bloco += 1

                            deletar_bloco = _limpar_fila_apos_clip(entry, padroes)
                            if deletar_bloco and cortes_bloco > 0 and not is_from_lote and not cancel_ev.is_set():
                                try:
                                    mkv_path.unlink(missing_ok=True)
                                    append_log("AUTO-CLIP", f"Bloco da fila (02_para_processar) removido para economizar espaço: {mkv_path.name}", channel=channel)
                                except Exception as del_err:
                                    append_log("AUTO-CLIP", f"Aviso ao remover bloco da fila: {del_err}", channel=channel)
                        except Exception as exc:
                            append_log("AUTO-CLIP", f"Erro ao cortar clip: {exc}", channel=channel)

                if not cancel_ev.is_set():
                    append_log("AUTO-CLIP", f"Finalizada análise de {channel}. Total de {cortes_total} clip(s) extraído(s).", channel=channel)
            except Exception as exc:
                append_log("AUTO-CLIP", f"Erro na análise de cortes: {exc}", channel=channel)
            finally:
                ANALYZE_CANCEL_EVENTS.pop(channel, None)

        threading.Thread(target=_worker, daemon=True, name="manual-clip-analysis").start()
        self._send_json(HTTPStatus.OK, {"ok": True, "message": f"Análise de cortes iniciada para '{channel}'."})

    def _handle_record_start(self, body: dict[str, Any]) -> None:
        if body.get("all"):
            started, msgs = RECORDER_MGR.start_all_active()
            if started == 0:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Nenhum canal ativo válido pronto para gravar. Verifique canais.json.", "messages": msgs},
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "started": started, "messages": msgs})
            return

        channel = sanitize_channel_name(str(body.get("channel") or ""))
        entry, cfg = _find_channel_entry(channel)
        if not entry:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Canal '{channel}' não está cadastrado em canais.json."},
            )
            return
        ok, msg = RECORDER_MGR.start_channel(entry, cfg.get("padroes", {}))
        status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
        self._send_json(status, {"ok": ok, "message": msg} if ok else {"error": msg})

    def _handle_record_stop(self, body: dict[str, Any]) -> None:
        if body.get("all"):
            stopped = RECORDER_MGR.stop_all()
            self._send_json(HTTPStatus.OK, {"ok": True, "stopped": stopped})
            return
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        stopped = RECORDER_MGR.stop_channel(channel)
        self._send_json(HTTPStatus.OK, {"ok": True, "stopped": stopped})

    def _handle_prepare(self, body: dict[str, Any]) -> None:
        args: list[str] = []
        target_label = ""
        if body.get("all"):
            args.append("--todos")
        else:
            channel = sanitize_channel_name(str(body.get("channel") or ""))
            if not channel:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel' ou 'all'."})
                return
            args.extend(["--canal", channel])
            target_label = channel

        if body.get("intervalo_frames"):
            args.extend(["--intervalo-frames", str(int(body["intervalo_frames"]))])
        if body.get("sem_whisper"):
            args.append("--sem-whisper")
        if body.get("sem_frames"):
            args.append("--sem-frames")

        files = body.get("files")
        if files and isinstance(files, list):
            target_filenames = [Path(f).name for f in files if str(f).strip()]
            if target_filenames:
                args.extend(["--arquivos", *target_filenames])

        ok, msg = JOB_MGR.start_job("preparar", target_label, "preparar_lote.py", args)
        if not ok:
            self._send_json(HTTPStatus.CONFLICT, {"error": msg})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    def _handle_finalize(self, body: dict[str, Any]) -> None:
        args: list[str] = []
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        lote = str(body.get("lote") or "").strip()
        if channel:
            args.extend(["--canal", channel])
        else:
            args.append("--todos")
        if lote:
            args.extend(["--lote", lote])
        if body.get("xml_por_bloco"):
            args.append("--xml-por-bloco")

        ok, msg = JOB_MGR.start_job("finalizar", channel, "finalizar_lote.py", args)
        if not ok:
            self._send_json(HTTPStatus.CONFLICT, {"error": msg})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    def _handle_lote_auto_analyze(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        lote = str(body.get("lote") or "").strip()
        if not channel or not lote:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel' e 'lote'."})
            return

        paths = ensure_channel_structure(channel)
        lote_dir = paths["03_em_processamento"] / lote
        if not lote_dir.exists():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Lote '{lote}' não encontrado em 03_em_processamento."})
            return

        def _worker() -> None:
            try:
                append_log("LOTE", f"Iniciando análise com IA do lote '{lote}' ({channel})...", channel=channel)
                from auto_clip import analyze_block
                from premiere_xml import probe_video

                cfg = load_channels_config()
                padroes = cfg.get("padroes", {})
                min_score = float(padroes.get("auto_clip_min_score", DEFAULT_AUTO_CLIP_SCORE))

                video_files = sorted(
                    p for p in lote_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                )
                if not video_files:
                    append_log("LOTE", f"Nenhum arquivo de vídeo encontrado no lote '{lote}'.", channel=channel)
                    return

                transcricoes_dir = lote_dir / "transcricoes"
                manuais = load_manual_markers(channel)

                blocos_decisao: list[dict[str, Any]] = []
                total_aprovados = 0
                total_descartados = 0

                for vpath in video_files:
                    tr_json = transcricoes_dir / f"{vpath.stem}.json"
                    duracao = 600.0
                    try:
                        duracao = float(probe_video(vpath)["duration"])
                    except Exception:
                        pass

                    manuais_deste = [m for m in manuais if m.get("bloco_arquivo") == vpath.name or m.get("bloco_stem") == vpath.stem]

                    append_log("LOTE", f"Analisando bloco: {vpath.name}...", channel=channel)
                    analysis = analyze_block(channel, vpath, tr_json, padroes)
                    score = int(analysis.get("score", 0))
                    momentos = analysis.get("momentos", [])

                    marcadores: list[dict[str, Any]] = []
                    for idx, m in enumerate(momentos, start=1):
                        center = float(m.get("segundo", 0.0))
                        in_s = max(0.0, center - 10.0)
                        out_s = min(duracao, center + 20.0)
                        desc = str(m.get("descricao") or f"Momento {idx}")
                        marcadores.append({
                            "in_seg": in_s,
                            "out_seg": out_s,
                            "titulo": desc,
                            "comentario": f"Score {m.get('score', score)}/100",
                            "tipo": "destaque",
                        })

                    for mm in manuais_deste:
                        m_sec = float(mm.get("segundo", 0.0))
                        marcadores.append({
                            "in_seg": max(0.0, m_sec - 5.0),
                            "out_seg": min(duracao, m_sec + 25.0),
                            "titulo": f"★ {mm.get('nota') or 'Marcador manual'}",
                            "comentario": "Marcado ao vivo",
                            "tipo": "manual",
                        })

                    eh_aprovado = (score >= min_score) or (len(marcadores) > 0) or bool(manuais_deste)
                    if eh_aprovado:
                        total_aprovados += 1
                        blocos_decisao.append({
                            "arquivo": vpath.name,
                            "status": "aprovado",
                            "marcadores": marcadores,
                            "cortes_descartados": [],
                        })
                    else:
                        total_descartados += 1
                        blocos_decisao.append({
                            "arquivo": vpath.name,
                            "status": "descartado",
                            "motivo_descarte": f"Pouca atividade relevante (score {score}/100)",
                            "marcadores": [],
                            "cortes_descartados": [],
                        })

                decisao_payload = {
                    "resumo_rodada": f"Lote {lote} analisado automaticamente. {total_aprovados} bloco(s) aprovado(s), {total_descartados} descartado(s).",
                    "estado_atual_live": f"Análise concluída com sucesso.",
                    "arcos_em_aberto": [],
                    "blocos": blocos_decisao,
                }
                decisao_file = lote_dir / "decisao_lote.json"
                decisao_file.write_text(json.dumps(decisao_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                append_log("LOTE", f"decisao_lote.json gerado ({total_aprovados} aprovados, {total_descartados} descartados). Disparando geração de XML do Premiere...", channel=channel)

                args = ["--canal", channel, "--lote", lote]
                JOB_MGR.start_job("finalizar", channel, "finalizar_lote.py", args)
            except Exception as exc:
                append_log("LOTE", f"Erro na auto-análise do lote '{lote}': {exc}", channel=channel)

        threading.Thread(target=_worker, daemon=True, name="auto-lote-analyze").start()
        self._send_json(HTTPStatus.OK, {"ok": True, "message": f"Análise de IA e geração de XML iniciadas para o lote '{lote}'."})

    def _handle_cut(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        lote = str(body.get("lote") or "").strip()
        if not channel or not lote:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel' e 'lote'."})
            return
        args = ["--canal", channel, "--lote", lote]
        if body.get("folga") is not None:
            args.extend(["--folga", str(float(body["folga"]))])
        if body.get("copia"):
            args.append("--copia")

        ok, msg = JOB_MGR.start_job("cortar", channel, "cortar_marcadores.py", args)
        if not ok:
            self._send_json(HTTPStatus.CONFLICT, {"error": msg})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    def _handle_save_estado(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        content = str(body.get("content") or "")
        if not channel:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Canal não informado."})
            return
        paths = ensure_channel_structure(channel)
        paths["estado_live"].write_text(content, encoding="utf-8")
        append_log(channel, "estado_live.md atualizado manualmente pelo App.", channel=channel)
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_markers_add(self, body: dict[str, Any]) -> None:
        raw_ch = str(body.get("channel") or "").strip()
        if not raw_ch:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Canal não informado."})
            return
        channel = sanitize_channel_name(raw_ch)
        pos = RECORDER_MGR.get_current_position(channel)
        if pos is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Aguarde o bloco começar a gravar para marcar um ponto."},
            )
            return

        nota = str(body.get("nota") or "").strip()
        retrocesso = float(body.get("retrocesso_seg") or 0.0)
        seg = max(0.0, round(float(pos["segundo"]) - retrocesso, 2))
        item = {
            "id": f"m_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:19]}",
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "arquivo": pos["arquivo"],
            "segundo": seg,
            "nota": nota,
        }
        markers = load_manual_markers(channel)
        markers.append(item)
        save_manual_markers(channel, markers)

        append_log(
            channel,
            f"★ Marcador manual em {format_mmss(seg)} ({pos['arquivo']})"
            + (f" — {nota}" if nota else ""),
            channel=channel,
        )
        self._send_json(HTTPStatus.OK, {"ok": True, "marker": item})

    def _handle_markers_delete(self, body: dict[str, Any]) -> None:
        raw_ch = str(body.get("channel") or "").strip()
        marker_id = str(body.get("id") or "").strip()
        if not raw_ch or not marker_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel' e 'id'."})
            return
        channel = sanitize_channel_name(raw_ch)
        markers = load_manual_markers(channel)
        restantes = [m for m in markers if str(m.get("id") or "") != marker_id]
        save_manual_markers(channel, restantes)
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_open_folder(self, body: dict[str, Any]) -> None:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            channel = body.get("channel")
            subdir = body.get("subdir")
            if channel:
                safe = sanitize_channel_name(str(channel))
                paths = ensure_channel_structure(safe)
                if subdir and subdir in paths:
                    target = paths[subdir]
                elif subdir:
                    target = paths["root"] / str(subdir)
                else:
                    target = paths["root"]
            else:
                target = PROJECT_ROOT
        else:
            target = Path(raw_path)

        safe_target = _validate_safe_project_path(str(target))
        if not safe_target.exists():
            safe_target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(safe_target))
        self._send_json(HTTPStatus.OK, {"ok": True, "path": str(safe_target)})

    def _handle_open_browser(self) -> None:
        url = f"http://127.0.0.1:{PORT}"
        try:
            if hasattr(os, "startfile"):
                os.startfile(url)
            else:
                import webbrowser
                webbrowser.open(url)
            self._send_json(HTTPStatus.OK, {"ok": True, "url": url})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _handle_ollama_status(self) -> None:
        cfg = load_channels_config()
        local_url = str(cfg.get("padroes", {}).get("ai_local_url") or "http://localhost:11434").rstrip("/")
        running = False
        models: list[str] = []
        has_gemma = False

        try:
            req = urllib.request.Request(f"{local_url}/api/tags", headers={"User-Agent": "AutoClipStream"})
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    running = True
                    models = [str(m.get("name", "")) for m in data.get("models", []) if isinstance(m, dict)]
                    has_gemma = any("gemma" in m.lower() for m in models)
        except Exception:
            running = False

        installed = (shutil.which("ollama") is not None) or (shutil.which("ollama.exe") is not None)
        self._send_json(HTTPStatus.OK, {
            "installed": installed,
            "running": running,
            "models": models,
            "has_gemma": has_gemma,
            "url": local_url,
        })

    def _handle_ollama_pull(self, body: dict[str, Any]) -> None:
        model_name = str(body.get("model") or "gemma3:4b").strip()
        cfg = load_channels_config()
        local_url = str(cfg.get("padroes", {}).get("ai_local_url") or "http://localhost:11434").rstrip("/")

        def _pull_worker() -> None:
            append_log("OLLAMA", f"Iniciando download do modelo '{model_name}'...")
            try:
                payload = json.dumps({"name": model_name, "stream": True}).encode("utf-8")
                req = urllib.request.Request(
                    f"{local_url}/api/pull",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1800) as resp:
                    last_pct = -1
                    for raw_line in resp:
                        if not raw_line:
                            continue
                        try:
                            chunk = json.loads(raw_line.decode("utf-8"))
                            status = chunk.get("status", "")
                            total = chunk.get("total", 0)
                            completed = chunk.get("completed", 0)
                            if total > 0 and completed > 0:
                                pct = int((completed / total) * 100)
                                if pct >= last_pct + 10 or pct == 100:
                                    last_pct = pct
                                    bar_len = 10
                                    filled = int(bar_len * completed / total)
                                    bar = "█" * filled + "░" * (bar_len - filled)
                                    mb_done = completed / (1024 * 1024)
                                    mb_total = total / (1024 * 1024)
                                    append_log("OLLAMA", f"[{bar}] {pct}% ({mb_done:.0f}MB / {mb_total:.0f}MB) — {status}")
                            elif status:
                                append_log("OLLAMA", f"Status: {status}")
                        except Exception:
                            pass
                append_log("OLLAMA", f"Modelo '{model_name}' pronto para uso!")
                return
            except Exception as exc:
                append_log("OLLAMA", f"Tentando download via CLI local ({exc})...")

            ollama_bin = shutil.which("ollama") or "ollama"
            try:
                proc = subprocess.run(
                    [ollama_bin, "pull", model_name],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=SUBPROCESS_FLAGS,
                )
                if proc.returncode == 0:
                    append_log("OLLAMA", f"Modelo '{model_name}' baixado com sucesso via CLI!")
                else:
                    append_log("OLLAMA", f"Falha no download via CLI: {proc.stderr[:150]}")
            except Exception as exc:
                append_log("OLLAMA", f"Erro ao baixar modelo '{model_name}': {exc}")

        threading.Thread(target=_pull_worker, daemon=True, name="ollama-pull").start()
        self._send_json(HTTPStatus.OK, {"ok": True, "message": f"Download de '{model_name}' iniciado em segundo plano."})

    def _handle_ollama_install(self) -> None:
        def _install_worker() -> None:
            append_log("OLLAMA", "Instalação silenciosa do Ollama iniciada via winget...")
            try:
                cmd = [
                    "winget", "install", "-e", "--id", "Ollama.Ollama",
                    "--accept-source-agreements", "--accept-package-agreements",
                    "--silent", "--disable-interactivity"
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=SUBPROCESS_FLAGS,
                )
                if proc.stdout:
                    for line in proc.stdout:
                        line_str = line.strip()
                        if line_str and ("%" in line_str or "Baixando" in line_str or "Instalando" in line_str or "Encontrado" in line_str):
                            append_log("OLLAMA", line_str)
                proc.wait()
                if proc.returncode == 0:
                    append_log("OLLAMA", "Ollama instalado com sucesso! Serviço disponível em http://localhost:11434")
                else:
                    append_log("OLLAMA", f"Winget finalizado com código {proc.returncode}")
            except Exception as exc:
                append_log("OLLAMA", f"Erro no instalador: {exc}")

        threading.Thread(target=_install_worker, daemon=True, name="ollama-install").start()
        self._send_json(HTTPStatus.OK, {"ok": True, "message": "Instalação silenciosa do Ollama disparada via winget."})

    def _handle_job_cancel(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        cancelled_any = False
        msgs: list[str] = []

        # Cancela processo subprocess gerenciado por JOB_MGR
        if JOB_MGR.snapshot().get("running"):
            ok, msg = JOB_MGR.cancel_job()
            if ok:
                cancelled_any = True
                msgs.append(msg)

        # Cancela event de análise manual de cortes se ativo
        if channel and channel in ANALYZE_CANCEL_EVENTS:
            ANALYZE_CANCEL_EVENTS[channel].set()
            cancelled_any = True
            msgs.append(f"Análise de cortes do canal '{channel}' cancelada.")
        elif not channel and ANALYZE_CANCEL_EVENTS:
            for ch, ev in list(ANALYZE_CANCEL_EVENTS.items()):
                ev.set()
            cancelled_any = True
            msgs.append("Todas as análises manuais de corte foram canceladas.")

        if cancelled_any:
            self._send_json(HTTPStatus.OK, {"ok": True, "message": " | ".join(msgs)})
        else:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Nenhuma tarefa em execução para cancelar."})

    def _handle_set_channel_jogo(self, body: dict[str, Any]) -> None:
        channel = sanitize_channel_name(str(body.get("channel") or ""))
        jogo = str(body.get("jogo") or "").strip().lower()
        if not channel:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Informe 'channel'."})
            return

        cfg = load_channels_config()
        canais = cfg.setdefault("canais", [])
        found = False
        for c in canais:
            if sanitize_channel_name(c.get("nome", "")) == channel:
                c["jogo"] = jogo
                found = True
                break

        if not found:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Canal '{channel}' não encontrado em canais.json."})
            return

        _save_channels_config(cfg)
        append_log("APP", f"Jogo/Categoria de '{channel}' alterado para: '{jogo or 'Geral'}'.", channel=channel)
        self._send_json(HTTPStatus.OK, {"ok": True, "channel": channel, "jogo": jogo})

    def _handle_shutdown(self) -> None:
        self._send_json(HTTPStatus.OK, {"ok": True, "message": "Encerrando servidor..."})

        def _stop_async() -> None:
            time.sleep(0.25)
            AUTO_CLIP.stop()
            AUTO_TRANSCRIBE.stop()
            RECORDER_MGR.stop_all(wait_timeout=8.0)
            srv = SERVER_REF.get("server")
            if srv is not None:
                srv.shutdown()

        threading.Thread(target=_stop_async, daemon=True).start()


def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Aguarda o servidor HTTP ficar disponível na porta antes de abrir o Edge."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.25)
    return False


def _launch_edge_and_watch(host: str, port: int) -> None:
    """Abre o aplicativo em modo Edge --app ou no navegador padrão."""
    _wait_for_server(host, port)
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_exe = next((p for p in edge_paths if Path(p).exists()), None)
    url = f"http://{host}:{port}"
    if edge_exe:
        cmd = [edge_exe, f"--app={url}", "--window-size=1240,780"]
    else:
        cmd = ["cmd", "/c", "start", url]

    try:
        subprocess.Popen(cmd)
    except Exception as exc:
        append_log("APP", f"Aviso ao abrir aplicativo no navegador: {exc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Servidor local do Stream-clipping App.")
    parser.add_argument("--host", default=HOST, help=f"Endereço (padrão: {HOST}).")
    parser.add_argument("--port", type=int, default=PORT, help=f"Porta HTTP (padrão: {PORT}).")
    parser.add_argument("--open-app", action="store_true", help="Abre no Microsoft Edge e monitora seu fechamento.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    class _LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        server = _LocalServer((args.host, args.port), StreamAppHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            print(f"Stream-clipping App já está rodando em http://{args.host}:{args.port}/")
            if args.open_app:
                _launch_edge_and_watch(args.host, args.port)
            return 0
        raise

    SERVER_REF["server"] = server
    atexit.register(AUTO_CLIP.stop)
    atexit.register(AUTO_TRANSCRIBE.stop)
    atexit.register(RECORDER_MGR.stop_all, 6.0)
    AUTO_TRANSCRIBE.start()
    AUTO_CLIP.start()

    if args.open_app:
        threading.Thread(
            target=_launch_edge_and_watch,
            args=(args.host, args.port),
            daemon=True,
            name="edge-window-watcher",
        ).start()

    def _sig_handler(signum, frame) -> None:
        threading.Thread(
            target=lambda: (AUTO_CLIP.stop(), AUTO_TRANSCRIBE.stop(), RECORDER_MGR.stop_all(6.0), server.shutdown()),
            daemon=True,
        ).start()

    signal.signal(signal.SIGINT, _sig_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _sig_handler)

    append_log("APP", f"Servidor iniciado em http://{args.host}:{args.port}/")
    print(f"Stream-clipping App rodando em http://{args.host}:{args.port}/", flush=True)

    try:
        server.serve_forever()
    finally:
        AUTO_CLIP.stop()
        AUTO_TRANSCRIBE.stop()
        RECORDER_MGR.stop_all(6.0)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
