"""Gravador contínuo multi-canal para lives da Twitch e do YouTube.

Captura streams ao vivo via Streamlink ou yt-dlp (com fallback automático),
divide em blocos .mkv de X em X minutos (padrão: 10 min) dentro de 01_gravando/
e move automaticamente cada bloco fechado para 02_para_processar/.

Uso:
    py -3.11 gravar_lives.py
    py -3.11 gravar_lives.py --url https://www.twitch.tv/canal --minutos 10
    py -3.11 gravar_lives.py --url nome1=https://twitch.tv/c1 --url nome2=https://youtube.com/@c2/live
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from config import (
    DEFAULT_QUALITY,
    DEFAULT_RECONNECT_SECONDS,
    DEFAULT_SEGMENT_MINUTES,
    FFMPEG_BIN,
    FFPROBE_BIN,
    SUBPROCESS_FLAGS,
    ensure_channel_structure,
    format_mmss,
    get_python_console_exe,
    load_channels_config,
    sanitize_channel_name,
)

MIN_VALID_MKV_BYTES = 16 * 1024  # 16 KB
PRINT_LOCK = threading.Lock()


def log_msg(channel: str, text: str) -> None:
    """Imprime mensagem sincronizada entre threads com carimbo de hora."""
    now = datetime.now().strftime("%H:%M:%S")
    with PRINT_LOCK:
        print(f"[{now}] [{channel}] {text}", flush=True)


def _find_tool_cmd(binary_name: str, python_module: str) -> list[str] | None:
    """Retorna o comando para executar streamlink/yt-dlp (binário no PATH ou módulo Python)."""
    exe = shutil.which(binary_name)
    if exe:
        return [exe]
    if importlib.util.find_spec(python_module) is not None:
        return [get_python_console_exe(), "-m", python_module]
    return None


def _is_twitch_url(url: str) -> bool:
    return "twitch.tv" in url.lower()


def _is_youtube_url(url: str) -> bool:
    low = url.lower()
    return "youtube.com" in low or "youtu.be" in low


def _infer_channel_name(url: str, fallback_idx: int) -> str:
    """Extrai um nome amigável a partir da URL caso --nome não seja informado."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p and p.lower() not in ("live", "watch", "channel", "c", "user")]
    if parts:
        candidate = parts[0].lstrip("@")
        if candidate:
            return sanitize_channel_name(candidate)
    return f"canal_{fallback_idx}"


def _probe_duration_safe(video_path: Path) -> float | None:
    """Obtém a duração do MKV via ffprobe sem lançar exceção."""
    try:
        res = subprocess.run(
            [
                FFPROBE_BIN,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            creationflags=SUBPROCESS_FLAGS,
        )
        val = res.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def _move_with_retry(src: Path, dst: Path, retries: int = 8, delay: float = 0.4) -> bool:
    """Move um arquivo tentando novamente se o Windows ainda estiver liberando o handle."""
    for _ in range(retries):
        try:
            if not src.exists():
                return False
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                stem = dst.stem
                suffix = dst.suffix
                dst = dst.with_name(f"{stem}_{int(time.time())}{suffix}")
            shutil.move(str(src), str(dst))
            return True
        except PermissionError:
            time.sleep(delay)
        except OSError:
            time.sleep(delay)
    return False


def _append_recording_log(log_path: Path, lock: threading.Lock, entry: dict[str, Any]) -> None:
    with lock:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _build_capture_cmd(
    engine: str,
    url: str,
    quality: str,
    cookies_file: str | None,
) -> list[str]:
    """Monta a linha de comando do Streamlink ou yt-dlp escrevendo o stream em stdout."""
    if engine == "streamlink":
        base = _find_tool_cmd("streamlink", "streamlink")
        if not base:
            raise RuntimeError("Streamlink não encontrado no Python 3.11 nem no PATH.")
        cmd = [*base, "--stdout", "--loglevel", "warning"]
        if _is_twitch_url(url):
            cmd.append("--twitch-disable-ads")
        cmd.extend([url, quality or DEFAULT_QUALITY])
        return cmd

    if engine == "yt-dlp":
        base = _find_tool_cmd("yt-dlp", "yt_dlp")
        if not base:
            raise RuntimeError("yt-dlp não encontrado no Python 3.11 nem no PATH.")
        fmt = "best[protocol*=m3u8]/best[ext=mp4]/best"
        cmd = [*base, "-f", fmt, "--no-part", "--quiet", "--no-warnings", "-o", "-"]
        if cookies_file:
            cookie_val = str(cookies_file).strip()
            if cookie_val.lower().startswith("browser:"):
                browser_name = cookie_val.split(":", 1)[1].strip()
                if browser_name:
                    cmd.extend(["--cookies-from-browser", browser_name])
            else:
                cookie_path = Path(cookie_val)
                if cookie_path.exists():
                    cmd.extend(["--cookies", str(cookie_path)])
        cmd.append(url)
        return cmd

    raise ValueError(f"Motor de captura desconhecido: {engine}")


def _choose_engine_order(preferred: str, url: str, cookies_file: str | None) -> list[str]:
    """Define a ordem de tentativa entre streamlink e yt-dlp."""
    has_sl = _find_tool_cmd("streamlink", "streamlink") is not None
    has_yd = _find_tool_cmd("yt-dlp", "yt_dlp") is not None

    if preferred == "streamlink":
        return ["streamlink"] if has_sl else []
    if preferred == "yt-dlp":
        return ["yt-dlp"] if has_yd else []

    # Modo auto
    order: list[str] = []
    if cookies_file and _is_youtube_url(url) and has_yd:
        order.append("yt-dlp")
        if has_sl:
            order.append("streamlink")
        return order

    if has_sl:
        order.append("streamlink")
    if has_yd:
        order.append("yt-dlp")
    return order


def _classify_capture_error(stderr_lines: list[str], url: str) -> tuple[bool, str]:
    """Classifica erros do capturador e indica se o canal deve pausar imediatamente."""
    joined = "\n".join(stderr_lines).lower()
    auth_markers = (
        "sign in to confirm",
        "not a bot",
        "429",
        "too many requests",
        "login required",
        "use --cookies",
        "members-only",
        "private video",
    )
    if any(m in joined for m in auth_markers):
        if _is_youtube_url(url):
            return (
                True,
                "YouTube exigiu autenticação/cookies ou limitou requisições (429). Exporte um cookies.txt da sua conta e informe em Editar canal -> Cookies.",
            )
        last_err = next(
            (l for l in reversed(stderr_lines) if "error" in l.lower() or "unable" in l.lower()),
            stderr_lines[-1] if stderr_lines else "Erro de autenticação ou acesso.",
        )
        return True, last_err

    offline_markers = (
        "no playable streams found",
        "is offline",
        "live event will begin",
    )
    if any(m in joined for m in offline_markers):
        return False, "Canal offline no momento."

    last_err = next(
        (l for l in reversed(stderr_lines) if "error" in l.lower() or "unable" in l.lower()),
        "",
    )
    return False, last_err


class ChannelRecorder:
    """Gerencia a gravação contínua e segmentada de um único canal."""

    def __init__(
        self,
        name: str,
        url: str,
        segment_minutes: int = DEFAULT_SEGMENT_MINUTES,
        quality: str = DEFAULT_QUALITY,
        engine: str = "auto",
        reconnect_seconds: int = DEFAULT_RECONNECT_SECONDS,
        cookies_file: str | None = None,
        stop_event: threading.Event | None = None,
        on_log: Callable[[str, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        self.name = sanitize_channel_name(name)
        self.url = url.strip()
        self.segment_seconds = max(10, int(segment_minutes * 60))
        self.quality = quality or DEFAULT_QUALITY
        self.engine_pref = (engine or "auto").lower()
        self.reconnect_seconds = max(5, int(reconnect_seconds))
        self.cookies_file = cookies_file
        self.stop_event = stop_event or threading.Event()
        self.on_log = on_log
        self.on_error = on_error
        self.last_error = ""

        self.paths = ensure_channel_structure(self.name)
        self.log_lock = threading.Lock()
        self._pos_lock = threading.Lock()
        self._progress_sec: float | None = None
        self._progress_ts: float = 0.0
        self._last_closed_end_sec: float = 0.0
        self._session_moved: set[str] = set()

    def get_current_position(self) -> dict[str, Any] | None:
        """Retorna {'arquivo': <mkv em gravacao>, 'segundo': <float>} ou None."""
        dir_gravando = self.paths["01_gravando"]
        if not dir_gravando.exists():
            return None

        with self._pos_lock:
            moved_copy = set(self._session_moved)
            prog = self._progress_sec
            prog_ts = self._progress_ts
            closed_end = self._last_closed_end_sec

        candidatos = [
            p
            for p in sorted(dir_gravando.glob(f"{self.name}_*.mkv"))
            if p.name not in moved_copy
        ]
        if not candidatos:
            return None

        active_mkv = candidatos[-1]
        if prog is not None:
            smooth = prog + min(2.0, max(0.0, time.time() - prog_ts))
            if closed_end > 0 and smooth >= (closed_end - 1.0):
                sec = max(0.0, smooth - closed_end)
            else:
                sec = max(0.0, smooth)
        else:
            try:
                sec = max(0.0, time.time() - active_mkv.stat().st_ctime)
            except OSError:
                sec = 0.0

        return {"arquivo": active_mkv.name, "segundo": round(sec, 2)}

    def _log(self, text: str) -> None:
        log_msg(self.name, text)
        if self.on_log is not None:
            try:
                self.on_log(self.name, text)
            except Exception:
                pass

    def _move_closed_segment(self, seg_path: Path, duration_csv: float | None = None) -> None:
        if not seg_path.exists():
            return
        size = seg_path.stat().st_size
        if size < MIN_VALID_MKV_BYTES:
            self._log(f"Descartando segmento vazio/corrompido ({size} B): {seg_path.name}")
            try:
                seg_path.unlink(missing_ok=True)
            except OSError:
                pass
            return

        dst = self.paths["02_para_processar"] / seg_path.name
        if _move_with_retry(seg_path, dst):
            dur = duration_csv if (duration_csv and duration_csv > 0) else _probe_duration_safe(dst)
            dur_str = format_mmss(dur) if dur is not None else "?:??"
            size_mb = size / (1024 * 1024)
            self._log(
                f"Bloco fechado -> 02_para_processar/{dst.name} ({dur_str} | {size_mb:.1f} MB)"
            )
            _append_recording_log(
                self.paths["registro_gravacoes"],
                self.log_lock,
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "canal": self.name,
                    "arquivo": dst.name,
                    "duracao_seg": round(dur, 2) if dur is not None else None,
                    "tamanho_bytes": size,
                    "url": self.url,
                },
            )

    def _watch_csv(
        self,
        csv_path: Path,
        moved_names: set[str],
        watcher_stop: threading.Event,
    ) -> None:
        """Observa o segment_list CSV do FFmpeg e move blocos recém-fechados."""
        dir_gravando = self.paths["01_gravando"]
        while not watcher_stop.is_set():
            if csv_path.exists():
                try:
                    lines = csv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for row in csv.reader(lines):
                        if not row:
                            continue
                        raw_name = row[0].strip()
                        if not raw_name:
                            continue
                        seg_path = Path(raw_name)
                        if not seg_path.is_absolute():
                            seg_path = dir_gravando / seg_path.name
                        if seg_path.name in moved_names:
                            continue
                        dur_csv: float | None = None
                        if len(row) >= 3:
                            try:
                                start_t = float(row[1])
                                end_t = float(row[2])
                                dur_csv = end_t - start_t
                                with self._pos_lock:
                                    if end_t > self._last_closed_end_sec:
                                        self._last_closed_end_sec = end_t
                            except ValueError:
                                dur_csv = None
                        if seg_path.exists():
                            moved_names.add(seg_path.name)
                            with self._pos_lock:
                                self._session_moved.add(seg_path.name)
                            self._move_closed_segment(seg_path, dur_csv)
                except OSError:
                    pass
            watcher_stop.wait(2.0)

    def _drain_remaining(self, moved_names: set[str], csv_path: Path) -> None:
        """Move qualquer bloco .mkv restante em 01_gravando/ após encerrar o FFmpeg."""
        dir_gravando = self.paths["01_gravando"]
        # Primeiro lê uma última vez o CSV para aproveitar durações exatas
        if csv_path.exists():
            try:
                lines = csv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                for row in csv.reader(lines):
                    if not row:
                        continue
                    seg_name = Path(row[0].strip()).name
                    if seg_name in moved_names:
                        continue
                    dur_csv = None
                    if len(row) >= 3:
                        try:
                            dur_csv = float(row[2]) - float(row[1])
                        except ValueError:
                            dur_csv = None
                    seg_path = dir_gravando / seg_name
                    if seg_path.exists():
                        moved_names.add(seg_name)
                        self._move_closed_segment(seg_path, dur_csv)
            except OSError:
                pass

        # Depois drena qualquer .mkv remanescente do canal
        for mkv in sorted(dir_gravando.glob(f"{self.name}_*.mkv")):
            if mkv.name not in moved_names:
                moved_names.add(mkv.name)
                self._move_closed_segment(mkv, None)

        try:
            csv_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _run_session(self, engine: str) -> tuple[bool, int]:
        """Executa uma tentativa de gravação com o motor informado.

        Retorna (produziu_bloco_ou_stream_valido, exit_code_captura).
        """
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_gravando = self.paths["01_gravando"]
        csv_path = dir_gravando / f"_segmentos_{session_id}.csv"
        out_pattern = dir_gravando / f"{self.name}_%Y-%m-%d_%H-%M-%S.mkv"

        capture_cmd = _build_capture_cmd(engine, self.url, self.quality, self.cookies_file)
        ffmpeg_cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-progress",
            "pipe:1",
            "-nostats",
            "-fflags",
            "+genpts+discardcorrupt",
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-dn",
            "-sn",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-reset_timestamps",
            "1",
            "-avoid_negative_ts",
            "make_zero",
            "-segment_format",
            "matroska",
            "-segment_list",
            str(csv_path),
            "-segment_list_type",
            "csv",
            "-strftime",
            "1",
            str(out_pattern),
        ]

        self._log(
            f"Conectando ({engine} | blocos de {self.segment_seconds // 60} min): {self.url}"
        )

        with self._pos_lock:
            self._progress_sec = None
            self._progress_ts = 0.0
            self._last_closed_end_sec = 0.0
            self._session_moved = set()

        moved_names: set[str] = set()
        watcher_stop = threading.Event()
        watcher_thread = threading.Thread(
            target=self._watch_csv,
            args=(csv_path, moved_names, watcher_stop),
            daemon=True,
        )
        watcher_thread.start()

        capture_proc: subprocess.Popen[bytes] | None = None
        ffmpeg_proc: subprocess.Popen[bytes] | None = None

        try:
            capture_proc = subprocess.Popen(
                capture_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=SUBPROCESS_FLAGS,
            )
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=capture_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=SUBPROCESS_FLAGS,
            )
            # Fecha a ponta do pipe no processo pai para o FFmpeg receber EOF ao fechar o capturador
            if capture_proc.stdout is not None:
                capture_proc.stdout.close()

            # Consome stderr e progress do ffmpeg em threads leves para não encher buffer
            cap_errs: list[str] = []
            ff_errs: list[str] = []

            def _drain_progress(stream) -> None:
                try:
                    for raw_line in iter(stream.readline, b""):
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if line.startswith("out_time_us="):
                            raw_val = line.split("=", 1)[1].strip()
                            if raw_val and raw_val != "N/A":
                                try:
                                    sec = int(raw_val) / 1_000_000.0
                                    with self._pos_lock:
                                        self._progress_sec = sec
                                        self._progress_ts = time.time()
                                except ValueError:
                                    pass
                except Exception:
                    pass

            def _drain_stderr(stream, bucket: list[str], prefix: str) -> None:
                try:
                    for raw_line in iter(stream.readline, b""):
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if line:
                            bucket.append(line)
                            if "error" in line.lower() or "unable" in line.lower():
                                self._log(f"[{prefix}] {line}")
                except Exception:
                    pass

            t_prog = threading.Thread(
                target=_drain_progress,
                args=(ffmpeg_proc.stdout,),
                daemon=True,
            )
            t_cap = threading.Thread(
                target=_drain_stderr,
                args=(capture_proc.stderr, cap_errs, engine),
                daemon=True,
            )
            t_ff = threading.Thread(
                target=_drain_stderr,
                args=(ffmpeg_proc.stderr, ff_errs, "ffmpeg"),
                daemon=True,
            )
            t_prog.start()
            t_cap.start()
            t_ff.start()

            while not self.stop_event.is_set():
                cap_rc = capture_proc.poll()
                ff_rc = ffmpeg_proc.poll()
                if cap_rc is not None or ff_rc is not None:
                    break
                time.sleep(0.5)

            if self.stop_event.is_set():
                self._log("Encerrando gravação com segurança (fechando MKV atual)...")
                if capture_proc.poll() is None:
                    capture_proc.terminate()
                    try:
                        capture_proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        capture_proc.kill()
                if ffmpeg_proc.poll() is None:
                    try:
                        ffmpeg_proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        ffmpeg_proc.terminate()
                        try:
                            ffmpeg_proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            ffmpeg_proc.kill()
            else:
                # Aguarda o ffmpeg finalizar o último bloco após EOF do capturador
                if ffmpeg_proc.poll() is None:
                    try:
                        ffmpeg_proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        ffmpeg_proc.terminate()
                if capture_proc.poll() is None:
                    capture_proc.terminate()

            t_prog.join(timeout=1)
            t_cap.join(timeout=1)
            t_ff.join(timeout=1)
            cap_code = capture_proc.returncode if capture_proc.returncode is not None else -1

        finally:
            watcher_stop.set()
            watcher_thread.join(timeout=3)
            self._drain_remaining(moved_names, csv_path)

        produced = len(moved_names) > 0
        if not produced and cap_errs:
            last_line = cap_errs[-1]
            if "error" not in last_line.lower() and "unable" not in last_line.lower():
                self._log(f"[{engine}] {last_line}")

        return produced, cap_code, cap_errs

    def run_forever(self) -> None:
        """Mantém a gravação ativa com reconexão automática até stop_event."""
        engines = _choose_engine_order(self.engine_pref, self.url, self.cookies_file)
        if not engines:
            msg = "Nem 'streamlink' nem 'yt-dlp' estão disponíveis no Python 3.11."
            self.last_error = msg
            if self.on_error is not None:
                try:
                    self.on_error(self.name, msg)
                except Exception:
                    pass
            self._log(f"ERRO: {msg}")
            return

        while not self.stop_event.is_set():
            cycle_produced = False

            for idx, engine in enumerate(engines):
                if self.stop_event.is_set():
                    break

                produced, _exit_code, stderr_lines = self._run_session(engine)
                if self.stop_event.is_set():
                    break

                if produced:
                    cycle_produced = True
                    if self.last_error:
                        self.last_error = ""
                        if self.on_error is not None:
                            try:
                                self.on_error(self.name, "")
                            except Exception:
                                pass
                    break

                deve_parar, msg = _classify_capture_error(stderr_lines, self.url)
                has_next = idx + 1 < len(engines)

                if deve_parar:
                    next_is_ytdlp_with_cookies = (
                        has_next
                        and engines[idx + 1] == "yt-dlp"
                        and bool(self.cookies_file and str(self.cookies_file).strip())
                    )
                    if next_is_ytdlp_with_cookies:
                        self._log(
                            f"Falha via {engine}; tentando yt-dlp com cookies configurados..."
                        )
                        self.stop_event.wait(1)
                        continue

                    self.last_error = msg
                    if self.on_error is not None:
                        try:
                            self.on_error(self.name, msg)
                        except Exception:
                            pass
                    self._log(f"PAUSADO: {msg}")
                    return

                if msg == "Canal offline no momento.":
                    self._log(msg)

                if has_next:
                    next_engine = engines[idx + 1]
                    self._log(
                        f"Sem stream via {engine}. Tentando fallback com {next_engine} em 2s..."
                    )
                    self.stop_event.wait(2)

            if self.stop_event.is_set():
                break

            if cycle_produced:
                self._log(
                    f"Transmissão encerrada ou caiu. Reconectando em {self.reconnect_seconds}s..."
                )
            else:
                self._log(
                    f"Live offline ou indisponível neste ciclo. Nova tentativa em {self.reconnect_seconds}s..."
                )
            self.stop_event.wait(self.reconnect_seconds)

        self._log("Gravador finalizado.")


def _collect_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Resolve a lista de canais a gravar via CLI ou via canais.json."""
    cfg = load_channels_config()
    padroes = cfg.get("padroes", {})
    default_min = args.minutos or padroes.get("duracao_bloco_minutos", DEFAULT_SEGMENT_MINUTES)
    default_qual = args.qualidade or padroes.get("qualidade", DEFAULT_QUALITY)
    default_rec = args.reconectar or padroes.get(
        "intervalo_reconexao_segundos", DEFAULT_RECONNECT_SECONDS
    )

    targets: list[dict[str, Any]] = []

    if args.url:
        for idx, raw_item in enumerate(args.url, start=1):
            if "=" in raw_item and not raw_item.startswith(("http://", "https://")):
                raw_name, url = raw_item.split("=", 1)
                name = raw_name.strip()
            else:
                url = raw_item.strip()
                name = (
                    args.nome
                    if (args.nome and len(args.url) == 1)
                    else _infer_channel_name(url, idx)
                )
            targets.append(
                {
                    "nome": name,
                    "url": url,
                    "duracao_bloco_minutos": default_min,
                    "qualidade": default_qual,
                    "motor": args.motor or "auto",
                    "intervalo_reconexao_segundos": default_rec,
                    "cookies": args.cookies,
                }
            )
        return targets

    for entry in cfg.get("canais", []):
        if not isinstance(entry, dict) or not entry.get("ativo", False):
            continue
        url = str(entry.get("url") or "").strip()
        if not url or "EXEMPLO" in url.upper():
            continue
        targets.append(
            {
                "nome": str(entry.get("nome") or _infer_channel_name(url, len(targets) + 1)),
                "url": url,
                "duracao_bloco_minutos": entry.get("duracao_bloco_minutos", default_min),
                "qualidade": entry.get("qualidade", default_qual),
                "motor": entry.get("motor", args.motor or "auto"),
                "intervalo_reconexao_segundos": entry.get(
                    "intervalo_reconexao_segundos", default_rec
                ),
                "cookies": entry.get("cookies") or args.cookies,
            }
        )
    return targets


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grava lives da Twitch e YouTube dividindo em blocos .mkv."
    )
    parser.add_argument(
        "--url",
        action="append",
        help="URL da live (ou nome=URL). Pode repetir para gravar vários canais ao mesmo tempo.",
    )
    parser.add_argument(
        "--nome",
        help="Nome do canal (usado quando houver apenas um --url).",
    )
    parser.add_argument(
        "--minutos",
        type=int,
        default=None,
        help="Duração de cada bloco em minutos (padrão: 10).",
    )
    parser.add_argument(
        "--qualidade",
        default=None,
        help="Qualidade para Streamlink (padrão: best).",
    )
    parser.add_argument(
        "--motor",
        choices=("auto", "streamlink", "yt-dlp"),
        default="auto",
        help="Motor de captura (padrão: auto).",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Caminho opcional para arquivo cookies.txt (útil para YouTube).",
    )
    parser.add_argument(
        "--reconectar",
        type=int,
        default=None,
        help="Segundos de espera ao reconectar se a live cair (padrão: 30).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    targets = _collect_targets(args)

    if not targets:
        print(
            "Nenhum canal ativo encontrado.\n"
            "Edite 'canais.json' colocando \"ativo\": true e a URL da live,\n"
            "ou rode pela linha de comando:\n"
            "  py -3.11 scripts\\gravar_lives.py --url https://www.twitch.tv/SEU_CANAL"
        )
        return 1

    stop_event = threading.Event()

    def _handle_sigint(signum, frame) -> None:
        if not stop_event.is_set():
            with PRINT_LOCK:
                print("\n[SISTEMA] Sinal de parada recebido. Fechando blocos atuais...", flush=True)
            stop_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_sigint)

    recorders: list[ChannelRecorder] = []
    threads: list[threading.Thread] = []

    for item in targets:
        rec = ChannelRecorder(
            name=item["nome"],
            url=item["url"],
            segment_minutes=int(item["duracao_bloco_minutos"]),
            quality=str(item["qualidade"]),
            engine=str(item["motor"]),
            reconnect_seconds=int(item["intervalo_reconexao_segundos"]),
            cookies_file=item.get("cookies"),
            stop_event=stop_event,
        )
        recorders.append(rec)
        t = threading.Thread(target=rec.run_forever, name=f"rec-{rec.name}")
        t.start()
        threads.append(t)

    print(
        f"Monitorando {len(recorders)} canal(is). Pressione Ctrl+C para parar com segurança.\n",
        flush=True,
    )

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        _handle_sigint(None, None)

    for t in threads:
        t.join(timeout=12)

    print("Todos os gravadores foram encerrados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
