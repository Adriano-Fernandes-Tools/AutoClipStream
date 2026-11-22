"""Prepara uma rodada isolada de vídeos fechados para análise pela IA.

Fluxo executado:
1. Move os vídeos prontos de 02_para_processar/ para 03_em_processamento/<lote_id>/
   (isolando imediatamente o lote para não misturar com novas gravações).
2. Transcreve cada vídeo com faster-whisper em GPU (com fallback para CPU).
3. Extrai grades visuais de frames (contact sheets 4x5 com timecode queimado).
4. Consolida estado_live.md + contexto anterior (últimos 90s) + transcrições +
   caminhos das grades em _pacote_ia.md e _pacote_ia.json.

Uso:
    py -3.11 preparar_lote.py --canal NOME
    py -3.11 preparar_lote.py --todos
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import (
    ALUCINACOES_RE,
    CANAIS_DIR,
    FFMPEG_BIN,
    FILTRAR_ALUCINACOES,
    FRAME_INTERVAL_SECONDS,
    GRID_COLS,
    GRID_ROWS,
    MARKER_TYPES,
    OVERLAP_CONTEXT_SECONDS,
    PRE_TRANSCRICOES_DIRNAME,
    SUBPROCESS_FLAGS,
    THUMB_HEIGHT,
    THUMB_WIDTH,
    VAD_ENABLED,
    VAD_MIN_SILENCE_MS,
    VAD_SPEECH_PAD_MS,
    VIDEO_EXTENSIONS,
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
    ensure_channel_structure,
    format_hms,
    format_mmss,
    format_srt_time,
    list_channel_names,
    load_manual_markers,
    sanitize_channel_name,
)
from premiere_xml import probe_video


def _limpar_alucinacoes(texto: str) -> str:
    if not FILTRAR_ALUCINACOES or not ALUCINACOES_RE:
        return texto
    limpo = ALUCINACOES_RE.sub("", texto)
    limpo = re.sub(r"\s{2,}", " ", limpo).strip()
    if not re.search(r"\w", limpo):
        return ""
    return limpo


def _carregar_modelo_whisper(model_name: str):
    """Carrega faster-whisper em CUDA com fallback seguro de compute_type antes de cair para CPU."""
    from faster_whisper import WhisperModel

    if WHISPER_DEVICE == "cuda":
        candidatos = [WHISPER_COMPUTE_TYPE, "float16", "int8"]
        tentados = set()
        for ctype in candidatos:
            if not ctype or ctype in tentados:
                continue
            tentados.add(ctype)
            try:
                print(f"Carregando faster-whisper '{model_name}' em CUDA ({ctype})...")
                return WhisperModel(model_name, device="cuda", compute_type=ctype)
            except Exception as err:
                print(f"[AVISO] Tentativa CUDA ({ctype}) falhou: {err}")

    print(f"Carregando faster-whisper '{model_name}' em CPU (int8)...")
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _transcrever_video(
    model: Any,
    video_path: Path,
    language: str | None,
    duration_fallback: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Transcreve um bloco de vídeo e devolve (segmentos, metadados)."""
    raw_segments, info = model.transcribe(
        str(video_path),
        language=language,
        beam_size=WHISPER_BEAM_SIZE,
        condition_on_previous_text=False,
        word_timestamps=True,
        vad_filter=VAD_ENABLED,
        vad_parameters={
            "min_silence_duration_ms": VAD_MIN_SILENCE_MS,
            "speech_pad_ms": VAD_SPEECH_PAD_MS,
        },
    )

    segmentos: list[dict[str, Any]] = []
    for seg in raw_segments:
        txt = _limpar_alucinacoes(seg.text.strip())
        if txt:
            if getattr(seg, "words", None) and seg.words:
                s_start = round(float(seg.words[0].start), 2)
                s_end = round(float(seg.words[-1].end), 2)
            else:
                s_start = round(float(seg.start), 2)
                s_end = round(float(seg.end), 2)
            segmentos.append(
                {
                    "start": s_start,
                    "end": s_end,
                    "text": txt,
                }
            )

    meta = {
        "language": getattr(info, "language", language) or (language or "auto"),
        "language_probability": round(float(getattr(info, "language_probability", 1.0)), 3),
        "duration": round(float(getattr(info, "duration", duration_fallback) or duration_fallback), 2),
    }
    return segmentos, meta


def _montar_srt(segmentos: list[dict[str, Any]]) -> str:
    if not segmentos:
        return ""
    blocos = [
        f"{idx}\n{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}\n{seg['text']}\n"
        for idx, seg in enumerate(segmentos, start=1)
    ]
    return "\n".join(blocos) + "\n"


def _extrair_grades_frames(
    video_path: Path,
    frames_dir: Path,
    intervalo_seg: int,
) -> list[Path]:
    """Extrai contact sheets 4x5 com o timecode HH:MM:SS queimado em cada frame."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = frames_dir / f"{video_path.stem}_grid_%02d.jpg"

    vf_parts = [
        f"fps=1/{max(1, intervalo_seg)}:start_time=0:round=down:eof_action=pass",
        f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=decrease",
        f"pad={THUMB_WIDTH}:{THUMB_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black",
        (
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
            "text='%{pts\\:hms}':x=8:y=8:fontsize=20:fontcolor=white:"
            "box=1:boxcolor=black@0.75:boxborderw=4"
        ),
        f"tile={GRID_COLS}x{GRID_ROWS}",
    ]
    vf = ",".join(vf_parts)

    def _run_ffmpeg(use_skip_nokey: bool) -> subprocess.CompletedProcess[str]:
        cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y"]
        if use_skip_nokey:
            cmd.extend(["-skip_frame", "nokey"])
        cmd.extend(
            [
                "-i",
                str(video_path),
                "-vf",
                vf,
                "-fps_mode",
                "vfr",
                "-q:v",
                "4",
                str(out_pattern),
            ]
        )
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=SUBPROCESS_FLAGS,
        )

    res = _run_ffmpeg(use_skip_nokey=True)
    gerados = sorted(frames_dir.glob(f"{video_path.stem}_grid_*.jpg"))
    if res.returncode != 0 or not gerados:
        res_fallback = _run_ffmpeg(use_skip_nokey=False)
        gerados = sorted(frames_dir.glob(f"{video_path.stem}_grid_*.jpg"))
        if res_fallback.returncode != 0 and not gerados:
            print(
                f"  [AVISO] Não foi possível extrair grades de {video_path.name}: "
                f"{res_fallback.stderr.strip() or res.stderr.strip()}"
            )
    return gerados


def _extrair_overlap_final(
    segmentos: list[dict[str, Any]],
    duracao: float,
    arquivo: str,
    janela_seg: int = OVERLAP_CONTEXT_SECONDS,
) -> dict[str, Any]:
    """Extrai os últimos ~90s de fala de um bloco para servir de contexto ao próximo."""
    limite_inicio = max(0.0, duracao - janela_seg)
    trechos = [seg for seg in segmentos if float(seg["end"]) >= limite_inicio]
    return {
        "arquivo_origem": arquivo,
        "duracao_origem": round(duracao, 2),
        "janela_segundos": janela_seg,
        "segmentos": trechos,
    }


def _criar_pasta_lote(dir_processando: Path) -> tuple[str, Path]:
    base_id = f"lote_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    lote_id = base_id
    sufixo = 2
    while (dir_processando / lote_id).exists():
        lote_id = f"{base_id}_{sufixo}"
        sufixo += 1
    lote_dir = dir_processando / lote_id
    lote_dir.mkdir(parents=True, exist_ok=True)
    return lote_id, lote_dir


def _montar_pacote_md(pacote: dict[str, Any], estado_live_texto: str) -> str:
    """Gera o arquivo _pacote_ia.md legível para análise editorial pela IA."""
    linhas: list[str] = [
        f"# Pacote de Análise — Canal `{pacote['canal']}` | Lote `{pacote['lote_id']}`",
        "",
        f"- **Criado em:** {pacote['criado_em']}",
        f"- **Quantidade de blocos:** {len(pacote['blocos'])}",
        f"- **Duração total do lote:** {format_hms(pacote['duracao_total_seg'])} ({pacote['duracao_total_seg']:.1f}s)",
        "",
        "---",
        "",
        "## 1. Estado Vivo da Live (`estado_live.md`)",
        "",
        estado_live_texto.strip(),
        "",
        "---",
        "",
        "## 2. Contexto Imediato Anterior (últimos ~90s do último lote)",
        "",
    ]

    ctx = pacote.get("contexto_anterior") or {}
    ctx_segs = ctx.get("segmentos") or []
    if ctx_segs:
        linhas.append(f"*Origem:* `{ctx.get('arquivo_origem', '')}`")
        linhas.append("")
        for seg in ctx_segs:
            linhas.append(
                f"- `[{format_mmss(seg['start'])} - {format_mmss(seg['end'])}]` {seg['text']}"
            )
    else:
        linhas.append("*(Sem lote anterior — este é o primeiro lote ou não havia falas no final do anterior.)*")

    linhas.extend(["", "---", "", "## 3. Blocos do Lote (em ordem cronológica)", ""])

    for idx, bloco in enumerate(pacote["blocos"], start=1):
        linhas.extend(
            [
                f"### Bloco {idx}: `{bloco['arquivo']}`",
                f"- **Duração:** {format_mmss(bloco['duracao_seg'])} ({bloco['duracao_seg']:.2f}s)",
                f"- **Vídeo:** `{bloco['largura']}x{bloco['altura']}` @ {bloco['fps_label']} | Faixas de áudio: {bloco['num_audios']}",
            ]
        )

        grades = bloco.get("frames_grades") or []
        if grades:
            linhas.append("- **Grades visuais (`frames/`):**")
            for g in grades:
                linhas.append(f"  - `{g}`")
        else:
            linhas.append("- **Grades visuais:** *(nenhuma gerada)*")

        manuais = bloco.get("marcadores_manuais") or []
        if manuais:
            linhas.append("")
            linhas.append("**⭐ Marcadores manuais do editor neste bloco (PRIORIDADE MÁXIMA):**")
            for m in manuais:
                seg = float(m.get("segundo") or 0.0)
                nota = str(m.get("nota") or "").strip()
                nota_txt = f' "{nota}"' if nota else " (sem nota)"
                linhas.append(f"- `[{format_mmss(seg)}]` ({seg:.2f}s){nota_txt}")

        linhas.append("")
        linhas.append("**Transcrição:**")
        linhas.append("")
        segs = bloco.get("transcricao") or []
        if segs:
            for seg in segs:
                linhas.append(
                    f"- `[{format_mmss(seg['start'])} - {format_mmss(seg['end'])}]` "
                    f"({seg['start']:.2f}s -> {seg['end']:.2f}s) {seg['text']}"
                )
        else:
            linhas.append("*(Nenhuma fala detectada neste bloco.)*")

        linhas.extend(["", "---", ""])

    tipos_validos = ", ".join(f"`{k}`" for k in MARKER_TYPES)
    linhas.extend(
        [
            "## 4. Formato Esperado de `decisao_lote.json`",
            "",
            f"Tipos de marcador aceitos: {tipos_validos}.",
            "",
            "```json",
            json.dumps(
                {
                    "resumo_rodada": "Resumo curto do que aconteceu neste lote.",
                    "estado_atual_live": "O que o streamer está fazendo agora.",
                    "arcos_em_aberto": ["Missão/arco em andamento"],
                    "blocos": [
                        {
                            "arquivo": pacote["blocos"][0]["arquivo"],
                            "status": "aprovado",
                            "motivo_descarte": "",
                            "marcadores": [
                                {
                                    "in_seg": 12.0,
                                    "out_seg": 54.5,
                                    "titulo": "Título curto do corte",
                                    "comentario": "Contexto rápido para o editor",
                                    "tipo": "corte_forte",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            "```",
            "",
        ]
    )

    return "\n".join(linhas)


def pre_transcrever_arquivos(
    video_paths: list[Path],
    whisper_model_name: str,
    language: str | None,
) -> int:
    """Pré-transcreve blocos fechados na fila salvando cache atômico em _pre_transcricoes/."""
    agora = time.time()
    pendentes: list[Path] = []
    for raw_path in video_paths:
        vp = Path(raw_path)
        if not vp.is_file() or vp.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if (agora - vp.stat().st_mtime) < 3.0:
            continue
        cache_json = vp.parent / PRE_TRANSCRICOES_DIRNAME / f"{vp.stem}.json"
        if cache_json.exists():
            continue
        pendentes.append(vp)

    if not pendentes:
        print("Nenhum bloco pendente para pré-transcrição.")
        return 0

    model = _carregar_modelo_whisper(whisper_model_name)
    concluidos = 0

    for idx, vp in enumerate(pendentes, start=1):
        if not vp.exists():
            print(f"  [PULANDO] '{vp.name}' foi movido antes da pré-transcrição.")
            continue

        cache_dir = vp.parent / PRE_TRANSCRICOES_DIRNAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_json = cache_dir / f"{vp.stem}.json"
        cache_srt = cache_dir / f"{vp.stem}.srt"
        if cache_json.exists():
            continue

        print(f"\n[Pré-transcrição {idx}/{len(pendentes)}] {vp.name}")
        size_bytes = vp.stat().st_size
        probe = probe_video(vp)
        duracao = float(probe["duration"])

        if len(probe["audios"]) == 0:
            segmentos: list[dict[str, Any]] = []
            idioma_detectado = language or "desconhecido"
            print("  [AVISO] Vídeo sem faixa de áudio.")
        else:
            t0 = time.time()
            segmentos, w_meta = _transcrever_video(model, vp, language, duracao)
            idioma_detectado = w_meta["language"]
            print(
                f"  Whisper: {len(segmentos)} fala(s) em {time.time() - t0:.1f}s "
                f"(idioma: {idioma_detectado})"
            )

        if not vp.exists():
            print(f"  [AVISO] '{vp.name}' foi movido para lote durante o processo; descartando cache.")
            continue

        srt_tmp = cache_srt.with_suffix(".srt.tmp")
        srt_tmp.write_text(_montar_srt(segmentos), encoding="utf-8")
        os.replace(str(srt_tmp), str(cache_srt))

        json_tmp = cache_json.with_suffix(".json.tmp")
        json_tmp.write_text(
            json.dumps(
                {
                    "arquivo": vp.name,
                    "tamanho_bytes": size_bytes,
                    "duration": round(duracao, 2),
                    "language": idioma_detectado,
                    "segments": segmentos,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(str(json_tmp), str(cache_json))
        concluidos += 1

    return concluidos


def parse_srt_to_segments(srt_path: Path) -> list[dict[str, Any]]:
    """Converte arquivo .srt existente em lista de segmentos Whisper."""
    if not srt_path.exists():
        return []
    try:
        content = srt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    segments: list[dict[str, Any]] = []
    time_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        match = None
        text_lines: list[str] = []
        for i, line in enumerate(lines):
            m = time_re.search(line)
            if m:
                match = m
                text_lines = lines[i + 1:]
                break
        if match:
            h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
            start_sec = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
            end_sec = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
            text = " ".join(text_lines).strip()
            if text:
                segments.append(
                    {
                        "start": round(start_sec, 2),
                        "end": round(end_sec, 2),
                        "text": text,
                    }
                )
    return segments


def _carregar_pre_transcricao(
    pre_dir: Path, video_path: Path, language: str | None
) -> tuple[list[dict[str, Any]], str] | None:
    """Tenta reaproveitar cache de pré-transcrição de 02_para_processar/_pre_transcricoes/."""
    pre_json = pre_dir / f"{video_path.stem}.json"
    pre_srt = pre_dir / f"{video_path.stem}.srt"
    if pre_json.exists():
        try:
            data = json.loads(pre_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("segments"), list):
                segmentos = data["segments"]
                idioma = str(data.get("language") or language or "pt")
                if segmentos:
                    return segmentos, idioma
        except Exception:
            pass

    if pre_srt.exists():
        parsed = parse_srt_to_segments(pre_srt)
        if parsed:
            return parsed, language or "pt"

    return None


def _buscar_transcricao_existente(
    channel_paths: dict[str, Path],
    video_path: Path,
    language: str | None,
) -> tuple[list[dict[str, Any]], str] | None:
    """Busca exaustivamente se este vídeo já foi transcrito antes em qualquer pasta do canal."""
    stem = video_path.stem
    dir_entrada = channel_paths.get("02_para_processar")
    if dir_entrada:
        pre_dir = dir_entrada / PRE_TRANSCRICOES_DIRNAME
        res = _carregar_pre_transcricao(pre_dir, video_path, language)
        if res is not None:
            return res

    parent_pre = video_path.parent / PRE_TRANSCRICOES_DIRNAME
    if parent_pre.exists():
        res = _carregar_pre_transcricao(parent_pre, video_path, language)
        if res is not None:
            return res

    canal_root = channel_paths.get("root", video_path.parent.parent)
    candidatos_busca = [
        canal_root / "03_em_processamento",
        canal_root / "04_para_edicao",
        canal_root / "_historico_lotes",
    ]
    for pasta_base in candidatos_busca:
        if not pasta_base.exists():
            continue
        # Busca em pastas de transcricoes de cada lote existente
        for srt_cand in pasta_base.glob(f"*/transcricoes/{stem}.srt"):
            if srt_cand.is_file():
                parsed = parse_srt_to_segments(srt_cand)
                if parsed:
                    return parsed, language or "pt"
        for json_cand in pasta_base.glob(f"*/transcricoes/{stem}.json"):
            if json_cand.is_file():
                try:
                    data = json.loads(json_cand.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and isinstance(data.get("segments"), list) and data["segments"]:
                        return data["segments"], str(data.get("language") or language or "pt")
                except Exception:
                    pass

    # Verifica marcadores automáticos salvos
    marker_auto = canal_root / "marcadores_automaticos" / f"{stem}.json"
    if marker_auto.exists():
        try:
            m_data = json.loads(marker_auto.read_text(encoding="utf-8"))
            if isinstance(m_data, dict) and isinstance(m_data.get("segments"), list) and m_data["segments"]:
                return m_data["segments"], str(m_data.get("language") or language or "pt")
        except Exception:
            pass

    return None


def preparar_canal(
    channel_name: str,
    model_holder: dict[str, Any],
    whisper_model_name: str,
    language: str | None,
    intervalo_frames: int,
    sem_whisper: bool,
    sem_frames: bool,
    arquivos_alvo: list[str] | None = None,
) -> Path | None:
    """Isola os arquivos pendentes de um canal e prepara um novo lote em 03_em_processamento/."""
    paths = ensure_channel_structure(channel_name)
    safe_name = paths["name"].name
    dir_entrada = paths["02_para_processar"]

    agora = time.time()
    if arquivos_alvo:
        alvo_set = {Path(a).name for a in arquivos_alvo}
        candidatos = sorted(
            p
            for p in dir_entrada.iterdir()
            if p.is_file() and p.name in alvo_set and p.suffix.lower() in VIDEO_EXTENSIONS
        )
    else:
        candidatos = sorted(
            p
            for p in dir_entrada.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        )
    prontos = [p for p in candidatos if (agora - p.stat().st_mtime) >= 3.0]

    if not prontos:
        if candidatos:
            print(f"[{safe_name}] Arquivo(s) recém-modificados (<3s); tente novamente em instantes.")
        else:
            print(f"[{safe_name}] Nenhum vídeo pendente em 02_para_processar/.")
        return None

    lote_id, lote_dir = _criar_pasta_lote(paths["03_em_processamento"])
    transcricoes_dir = lote_dir / "transcricoes"
    frames_dir = lote_dir / "frames"
    transcricoes_dir.mkdir(exist_ok=True)
    frames_dir.mkdir(exist_ok=True)

    print(f"\n=== [{safe_name}] Lote '{lote_id}': isolando {len(prontos)} vídeo(s) ===")

    videos_no_lote: list[Path] = []
    for src in prontos:
        dst = lote_dir / src.name
        shutil.move(str(src), str(dst))
        videos_no_lote.append(dst)
        print(f"  -> Movido para processamento: {dst.name}")

    blocos_info: list[dict[str, Any]] = []
    duracao_total = 0.0
    todos_manuais = load_manual_markers(safe_name)

    for idx, video_path in enumerate(videos_no_lote, start=1):
        print(f"\n[{idx}/{len(videos_no_lote)}] Analisando mídia: {video_path.name}")
        probe = probe_video(video_path)
        duracao = float(probe["duration"])
        duracao_total += duracao
        fps_label = f"{float(probe['fps']):.2f} fps"
        num_audios = len(probe["audios"])

        segmentos: list[dict[str, Any]] = []
        idioma_detectado = language or "desconhecido"

        if not sem_whisper:
            reaproveitado = _buscar_transcricao_existente(paths, video_path, language)
            if reaproveitado is not None:
                segmentos, idioma_detectado = reaproveitado
                print(
                    f"  Whisper: transcrição existente reaproveitada com sucesso "
                    f"({len(segmentos)} fala(s) | idioma: {idioma_detectado}) - Whisper evitado!"
                )
            elif num_audios == 0:
                print("  [AVISO] Vídeo sem faixa de áudio; pulando Whisper.")
            else:
                if model_holder.get("model") is None:
                    model_holder["model"] = _carregar_modelo_whisper(whisper_model_name)
                t0 = time.time()
                segmentos, w_meta = _transcrever_video(
                    model_holder["model"], video_path, language, duracao
                )
                idioma_detectado = w_meta["language"]
                print(
                    f"  Whisper: {len(segmentos)} fala(s) em {time.time() - t0:.1f}s "
                    f"(idioma: {idioma_detectado})"
                )

        srt_path = transcricoes_dir / f"{video_path.stem}.srt"
        srt_path.write_text(_montar_srt(segmentos), encoding="utf-8")

        json_trans_path = transcricoes_dir / f"{video_path.stem}.json"
        json_trans_path.write_text(
            json.dumps(
                {
                    "arquivo": video_path.name,
                    "duration": round(duracao, 2),
                    "language": idioma_detectado,
                    "segments": segmentos,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        grades_rel: list[str] = []
        if not sem_frames:
            grades = _extrair_grades_frames(video_path, frames_dir, intervalo_frames)
            grades_rel = [f"frames/{g.name}" for g in grades]
            print(f"  Frames: {len(grades_rel)} grade(s) extraída(s) (1 a cada {intervalo_frames}s)")

        manuais_bloco = sorted(
            (m for m in todos_manuais if m.get("arquivo") == video_path.name),
            key=lambda item: float(item.get("segundo") or 0.0),
        )
        if manuais_bloco:
            print(f"  Marcadores manuais encontrados: {len(manuais_bloco)}")

        blocos_info.append(
            {
                "arquivo": video_path.name,
                "duracao_seg": round(duracao, 2),
                "largura": probe["width"],
                "altura": probe["height"],
                "fps_label": fps_label,
                "num_audios": num_audios,
                "idioma": idioma_detectado,
                "srt_rel": f"transcricoes/{srt_path.name}",
                "frames_grades": grades_rel,
                "marcadores_manuais": manuais_bloco,
                "transcricao": segmentos,
            }
        )

    contexto_anterior: dict[str, Any] = {}
    if paths["ultimo_contexto"].exists():
        try:
            contexto_anterior = json.loads(
                paths["ultimo_contexto"].read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            contexto_anterior = {}

    ultimo_bloco = blocos_info[-1]
    overlap_saida = _extrair_overlap_final(
        ultimo_bloco["transcricao"],
        float(ultimo_bloco["duracao_seg"]),
        ultimo_bloco["arquivo"],
    )

    pacote = {
        "canal": safe_name,
        "lote_id": lote_id,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
        "duracao_total_seg": round(duracao_total, 2),
        "intervalo_frames_seg": intervalo_frames,
        "contexto_anterior": contexto_anterior,
        "overlap_para_proximo_lote": overlap_saida,
        "blocos": blocos_info,
    }

    pacote_json_path = lote_dir / "_pacote_ia.json"
    pacote_json_path.write_text(
        json.dumps(pacote, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    estado_texto = paths["estado_live"].read_text(encoding="utf-8")
    pacote_md_path = lote_dir / "_pacote_ia.md"
    pacote_md_path.write_text(
        _montar_pacote_md(pacote, estado_texto), encoding="utf-8"
    )

    print(f"\n[{safe_name}] Lote pronto para análise da IA:")
    print(f"  Pacote MD: {pacote_md_path}")
    return lote_dir


def _canais_com_pendencias() -> list[str]:
    pendentes: list[str] = []
    for name in list_channel_names():
        dir_entrada = CANAIS_DIR / name / "02_para_processar"
        if not dir_entrada.exists():
            continue
        has_video = any(
            p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            for p in dir_entrada.iterdir()
        )
        if has_video:
            pendentes.append(name)
    return pendentes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Isola blocos fechados, transcreve com Whisper e gera grades de frames."
    )
    parser.add_argument("--canal", help="Nome do canal a preparar.")
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Processa todos os canais que tiverem arquivos em 02_para_processar/.",
    )
    parser.add_argument(
        "--modelo",
        default=WHISPER_MODEL,
        help=f"Modelo do faster-whisper (padrão: {WHISPER_MODEL}).",
    )
    parser.add_argument(
        "--idioma",
        default=WHISPER_LANGUAGE,
        help="Idioma (ex: pt, en, es ou 'auto').",
    )
    parser.add_argument(
        "--intervalo-frames",
        type=int,
        default=FRAME_INTERVAL_SECONDS,
        help=f"Intervalo em segundos entre frames da grade (padrão: {FRAME_INTERVAL_SECONDS}).",
    )
    parser.add_argument(
        "--sem-whisper",
        action="store_true",
        help="Pula a transcrição Whisper (útil para testes rápidos ou lives sem fala).",
    )
    parser.add_argument(
        "--sem-frames",
        action="store_true",
        help="Pula a extração das grades de frames.",
    )
    parser.add_argument(
        "--arquivos",
        nargs="*",
        metavar="ARQUIVO",
        help="Nomes de arquivos específicos em 02_para_processar/ para isolar no lote.",
    )
    parser.add_argument(
        "--pre-transcrever",
        nargs="*",
        metavar="VIDEO",
        help="Pré-transcreve vídeos na fila (02_para_processar/) salvando cache em _pre_transcricoes/.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    language = None if (args.idioma is None or args.idioma.lower() == "auto") else args.idioma

    if args.pre_transcrever is not None:
        if args.pre_transcrever:
            target_videos = [Path(p) for p in args.pre_transcrever]
        else:
            # Se chamado sem arquivos específicos, resolve da fila dos canais indicados
            canais_alvo = []
            if args.canal:
                canais_alvo = [sanitize_channel_name(args.canal)]
            elif args.todos:
                canais_alvo = list_channel_names()
            else:
                canais_alvo = list_channel_names()

            target_videos = []
            for ch in canais_alvo:
                paths = ensure_channel_structure(ch)
                fila_dir = paths["02_para_processar"]
                if fila_dir.exists():
                    for v in sorted(fila_dir.iterdir()):
                        if v.is_file() and v.suffix.lower() in VIDEO_EXTENSIONS:
                            target_videos.append(v)

        if not target_videos:
            print("Nenhum vídeo encontrado na fila para pré-transcrição.")
            return 0

        pre_transcrever_arquivos(
            video_paths=target_videos,
            whisper_model_name=args.modelo,
            language=language,
        )
        return 0

    if args.canal:
        alvos = [sanitize_channel_name(args.canal)]
    elif args.todos:
        alvos = _canais_com_pendencias()
        if not alvos:
            print("Nenhum canal tem vídeos pendentes em 02_para_processar/.")
            return 0
    else:
        pendentes = _canais_com_pendencias()
        if not pendentes:
            print("Nenhum canal tem vídeos pendentes em 02_para_processar/.")
            return 0
        if len(pendentes) == 1:
            alvos = pendentes
        else:
            print("Há mais de um canal com vídeos pendentes:")
            for c in pendentes:
                print(f"  - {c}")
            print("Informe --canal <nome> ou rode com --todos.")
            return 1

    model_holder: dict[str, Any] = {"model": None}
    for canal in alvos:
        preparar_canal(
            channel_name=canal,
            model_holder=model_holder,
            whisper_model_name=args.modelo,
            language=language,
            intervalo_frames=max(1, args.intervalo_frames),
            sem_whisper=args.sem_whisper,
            sem_frames=args.sem_frames,
            arquivos_alvo=args.arquivos,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
