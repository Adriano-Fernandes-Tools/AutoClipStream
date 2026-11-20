"""Editor automático vertical 9:16 (1080x1920, 60 fps) para cortes de games.

Lê um arquivo JSON de especificação de cortes (--spec), gera os overlays da Headline
em Poppins Black MAIÚSCULA (Estilos 01 Solta, 02 Tarja Vermelha ou 03 Caixa Branca),
a marca d'água central configurável (30% de opacidade), transcreve palavra por palavra
via faster-whisper para legenda dinâmica POP de 1 palavra, normaliza o áudio em 2 passes
para -14 LUFS (padrão YouTube) e renderiza em corte seco usando FFmpeg (h264_nvenc com
fallback automático para libx264).

Uso:
    py -3.11 editar_vertical.py --spec caminho/para/especificacao.json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys
from typing import Any

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from PIL import Image, ImageDraw, ImageFont

from config import (
    ALUCINACOES_RE,
    FFMPEG_BIN,
    FILTRAR_ALUCINACOES,
    SUBPROCESS_FLAGS,
    VAD_ENABLED,
    VAD_MIN_SILENCE_MS,
    VAD_SPEECH_PAD_MS,
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)
from premiere_xml import probe_video

# ==============================================================================
# CONSTANTES VISUAIS E COORDENADAS CALIBRADAS (1920x1080 FONTE -> 1080x1920 9:16)
# ==============================================================================

CANVAS_W = 1080
CANVAS_H = 1920
TARGET_FPS = 60

STREAMER_PRESETS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "alanzoka": {
        "cam": (1485, 486, 421, 268),
        "cam_zoom": (1520, 495, 340, 225),
        "game": (400, 120, 1060, 960),
    },
    "bistecone": {
        "cam": (1450, 162, 468, 302),
        "cam_zoom": (1510, 195, 340, 235),
        "game": (320, 80, 1100, 1000),
    },
    "davijones": {
        "cam": (1505, 106, 405, 228),
        "cam_zoom": (1555, 115, 305, 195),
        "game": (320, 140, 1150, 940),
    },
    "mrfall": {
        "cam": (1280, 605, 635, 423),
        "cam_zoom": (1360, 620, 555, 370),
        "game": (220, 100, 1200, 980),
    },
}

STOPWORDS_PT: frozenset[str] = frozenset(
    {
        "O", "A", "OS", "AS", "UM", "UMA", "UNS", "UMAS",
        "DE", "DO", "DA", "DOS", "DAS", "EM", "NO", "NA", "NOS", "NAS",
        "NUM", "NUMA", "E", "É", "QUE", "PRA", "PRO", "POR", "PELO", "PELA",
        "SE", "ME", "TE", "LHE", "ELE", "ELA", "EU", "TU", "NÓS",
        "TÁ", "TÔ", "AI", "AÍ", "NE", "NÉ", "COM", "MAS", "JÁ", "OU", "AO", "AOS",
    }
)

# Cores ASS em formato BGR (&HBBGGRR&)
ASS_ACCENT_COLORS: dict[str, str] = {
    "amarelo": "&H0000D7FF&",  # #FFD700
    "verde": "&H0064FF50&",    # #50FF64
    "branco": "&H00FFFFFF&",   # #FFFFFF
}
ASS_WHITE = "&H00FFFFFF&"

COLOR_YELLOW = (255, 215, 0, 255)
COLOR_WHITE = (255, 255, 255, 255)
COLOR_BLACK = (12, 12, 12, 255)
COLOR_SHADOW = (0, 0, 0, 175)
COLOR_RED_BOX = (220, 22, 35, 248)
COLOR_WHITE_BOX = (255, 255, 255, 252)
COLOR_PILL_SHADOW = (0, 0, 0, 115)


# ==============================================================================
# DESCOBERTA DA FONTE E UTILITÁRIOS DE GEOMETRIA
# ==============================================================================


def resolve_poppins_black_path() -> Path:
    """Localiza Poppins-Black.ttf instalada no Windows."""
    candidates = [
        Path(r"C:\Windows\Fonts\Poppins-Black.ttf"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts" / "Poppins-Black.ttf",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Fonte obrigatória Poppins-Black.ttf não encontrada em C:\\Windows\\Fonts nem no usuário."
    )


def get_ass_font_name(font_path: Path) -> str:
    """Extrai o nome exato de família/estilo para que o libass selecione Poppins Black."""
    font = ImageFont.truetype(str(font_path), 40)
    family, style = font.getname()
    if style and style.strip().lower() not in ("regular", "normal"):
        return f"{family.strip()} {style.strip()}"
    return family.strip()


def resolve_preset(canal: str, clip: dict[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    """Carrega coordenadas calibradas do canal e aplica eventuais overrides do clipe."""
    key = re.sub(r"[^a-z0-9]", "", (canal or "").lower())
    base = STREAMER_PRESETS.get(key)
    if not base:
        raise ValueError(
            f"Canal '{canal}' sem preset calibrado. Disponíveis: {', '.join(STREAMER_PRESETS)}"
        )

    def _rect(override_key: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        raw = clip.get(override_key)
        if raw is None:
            return fallback
        if len(raw) != 4:
            raise ValueError(f"'{override_key}' deve conter 4 inteiros [x, y, w, h].")
        return tuple(int(v) for v in raw)  # type: ignore[return-value]

    return {
        "cam": _rect("cam_crop", base["cam"]),
        "cam_zoom": _rect("cam_zoom_crop", base["cam_zoom"]),
        "game": _rect("game_crop", base["game"]),
    }


def compute_cover_crop(
    rect: tuple[int, int, int, int],
    dst_w: int,
    dst_h: int,
    src_w: int = 1920,
    src_h: int = 1080,
) -> tuple[int, int, int, int]:
    """Calcula recorte com proporção exata de destino (cover) sem distorção."""
    rx, ry, rw, rh = rect
    target_aspect = dst_w / float(dst_h)
    src_aspect = rw / float(rh)

    if src_aspect > target_aspect:
        new_w = int(round(rh * target_aspect))
        rx += int(round((rw - new_w) / 2.0))
        rw = new_w
    elif src_aspect < target_aspect:
        new_h = int(round(rw / target_aspect))
        ry += int(round((rh - new_h) / 2.0))
        rh = new_h

    rw = max(2, min(src_w, rw) & ~1)
    rh = max(2, min(src_h, rh) & ~1)
    rx = max(0, min(src_w - rw, rx))
    ry = max(0, min(src_h - rh, ry))
    return (rx, ry, rw, rh)


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escapa caminho do Windows para uso seguro dentro de opções do filtergraph FFmpeg."""
    posix = path.resolve().as_posix()
    return posix.replace(":", r"\:")


# ==============================================================================
# GERADORES DE OVERLAY RGBA (HEADLINE E MARCA D'ÁGUA)
# ==============================================================================


def _fit_headline_fonts(
    lines: list[str],
    font_path: Path,
    base_size: int,
    max_width: int,
    stroke_w: int,
) -> list[ImageFont.FreeTypeFont]:
    """Reduz a fonte uniformemente caso qualquer linha ultrapasse a largura segura."""
    dummy = Image.new("RGBA", (CANVAS_W, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    size = base_size
    while size > 28:
        font = ImageFont.truetype(str(font_path), size)
        widths = [
            (draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)[2]
             - draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)[0])
            for ln in lines
        ]
        if max(widths, default=0) <= max_width:
            return [font for _ in lines]
        size -= 2
    font = ImageFont.truetype(str(font_path), 28)
    return [font for _ in lines]


def render_headline_png(
    lines: list[str],
    style: str,
    layout_mode: str,
    font_path: Path,
    out_path: Path,
) -> None:
    """Renderiza overlay RGBA 1080x1920 da Headline fixa (Estilos solta, vermelha, branca)."""
    clean_lines = [ln.strip().upper() for ln in lines if ln and ln.strip()]
    if not clean_lines:
        raise ValueError("Headline não pode ficar vazia.")

    style_key = style.strip().lower()
    if style_key not in ("solta", "vermelha", "branca"):
        raise ValueError(f"Estilo de Headline inválido: '{style}'. Use solta, vermelha ou branca.")

    anchor_y = 420 if layout_mode == "centro_blur" else 720
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if style_key == "solta":
        base_size, max_w, stroke_w, shadow_off, line_gap = 58, 960, 9, 6, 18
    elif style_key == "vermelha":
        base_size, max_w, stroke_w, shadow_off, line_gap = 54, 900, 7, 4, 16
    else:
        base_size, max_w, stroke_w, shadow_off, line_gap = 50, 880, 0, 0, 18

    fonts = _fit_headline_fonts(clean_lines, font_path, base_size, max_w, stroke_w)
    line_heights: list[int] = []
    for ln, fnt in zip(clean_lines, fonts):
        bbox = draw.textbbox((0, 0), ln, font=fnt, anchor="mm", stroke_width=stroke_w)
        line_heights.append(bbox[3] - bbox[1])

    total_text_h = sum(line_heights) + line_gap * max(0, len(clean_lines) - 1)

    if style_key == "vermelha":
        pad_y = 28
        box_h = total_text_h + pad_y * 2
        top = int(round(anchor_y - box_h / 2))
        bottom = top + box_h
        draw.rectangle(
            [55, top, 1025, bottom],
            fill=COLOR_RED_BOX,
            outline=COLOR_BLACK,
            width=7,
        )
    elif style_key == "branca":
        pad_y = 26
        box_h = total_text_h + pad_y * 2
        top = int(round(anchor_y - box_h / 2))
        bottom = top + box_h
        draw.rounded_rectangle([71, top + 8, 1021, bottom + 8], radius=26, fill=COLOR_PILL_SHADOW)
        draw.rounded_rectangle([65, top, 1015, bottom], radius=26, fill=COLOR_WHITE_BOX)

    cur_y = anchor_y - total_text_h / 2.0
    for idx, (ln, fnt, lh) in enumerate(zip(clean_lines, fonts, line_heights)):
        center_y = cur_y + lh / 2.0
        if style_key == "branca":
            text_color = COLOR_BLACK
        else:
            text_color = COLOR_YELLOW if idx == 0 else COLOR_WHITE

        if shadow_off > 0:
            draw.text(
                (540 + shadow_off, center_y + shadow_off),
                ln,
                font=fnt,
                fill=COLOR_SHADOW,
                anchor="mm",
                stroke_width=stroke_w,
                stroke_fill=COLOR_SHADOW,
            )

        draw.text(
            (540, center_y),
            ln,
            font=fnt,
            fill=text_color,
            anchor="mm",
            stroke_width=stroke_w,
            stroke_fill=COLOR_BLACK if stroke_w > 0 else None,
        )
        cur_y += lh + line_gap

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def render_watermark_png(
    text: str,
    layout_mode: str,
    font_path: Path,
    out_path: Path,
) -> None:
    """Renderiza overlay RGBA 1080x1920 da marca d'água central com 30% de opacidade."""
    wm_y = 1240 if layout_mode == "centro_blur" else 980
    layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.truetype(str(font_path), 38)

    # Desenha opaco primeiro e reduz alfa globalmente para 30% sem sobreposição contorno/miolo
    draw.text(
        (540, wm_y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        stroke_width=2,
        stroke_fill=(0, 0, 0, 180),
    )
    r, g, b, a = layer.split()
    a = a.point(lambda p: int(round(p * 0.30)))
    final_img = Image.merge("RGBA", (r, g, b, a))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(out_path, format="PNG")


# ==============================================================================
# TRANSCRIÇÃO PALAVRA A PALAVRA E GERAÇÃO DE LEGENDA POP (.ASS)
# ==============================================================================


def load_whisper_model():
    """Carrega faster-whisper em CUDA com fallback automático para CPU."""
    from faster_whisper import WhisperModel

    if WHISPER_DEVICE == "cuda":
        try:
            print(f"Carregando faster-whisper '{WHISPER_MODEL}' em CUDA ({WHISPER_COMPUTE_TYPE})...")
            return WhisperModel(WHISPER_MODEL, device="cuda", compute_type=WHISPER_COMPUTE_TYPE)
        except Exception as err:
            print(f"[AVISO] CUDA indisponível ({err}). Usando CPU (int8)...")

    print(f"Carregando faster-whisper '{WHISPER_MODEL}' em CPU (int8)...")
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def _apply_word_corrections(
    words: list[dict[str, Any]],
    correcoes: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Aplica substituições exatas de palavras ou frases curtas (case-insensitive)."""
    if not correcoes or not words:
        return words

    rules: list[tuple[list[str], list[str]]] = []
    for raw_k, raw_v in correcoes.items():
        k_tokens = [re.sub(r"[!?]+$", "", t).lower() for t in (raw_k or "").split() if t.strip()]
        v_tokens = [t.strip().upper() for t in (raw_v or "").split() if t.strip()]
        if k_tokens and v_tokens:
            rules.append((k_tokens, v_tokens))
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    if not rules:
        return words

    result: list[dict[str, Any]] = []
    idx = 0
    while idx < len(words):
        matched = False
        for k_tokens, v_tokens in rules:
            n = len(k_tokens)
            if idx + n <= len(words):
                window = [re.sub(r"[!?]+$", "", words[idx + j]["word"]).lower() for j in range(n)]
                if window == k_tokens:
                    last_raw = words[idx + n - 1]["word"]
                    punct_match = re.search(r"[!?]+$", last_raw)
                    trailing_punct = punct_match.group(0) if punct_match else ""
                    span_start = words[idx]["start"]
                    span_end = words[idx + n - 1]["end"]
                    step = max(0.08, (span_end - span_start) / len(v_tokens))
                    for j, new_tok in enumerate(v_tokens):
                        tok_text = new_tok + (trailing_punct if j == len(v_tokens) - 1 else "")
                        result.append(
                            {
                                "word": tok_text,
                                "start": span_start + j * step,
                                "end": span_end if j == len(v_tokens) - 1 else span_start + (j + 1) * step,
                            }
                        )
                    idx += n
                    matched = True
                    break
        if not matched:
            result.append(words[idx])
            idx += 1
    return result


def transcribe_clip_words(
    video_path: Path,
    in_seg: float,
    duration: float,
    model: Any,
    temp_dir: Path,
    correcoes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Extrai áudio (com 2s de contexto prévio quando possível) e retorna palavras cronometradas."""
    ctx_before = min(in_seg, 2.0)
    extract_start = max(0.0, in_seg - ctx_before)
    extract_dur = ctx_before + duration

    wav_path = temp_dir / "clip_audio.wav"
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{extract_start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{extract_dur:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    subprocess.run(cmd, check=True, creationflags=SUBPROCESS_FLAGS)

    raw_segments, _ = model.transcribe(
        str(wav_path),
        language=WHISPER_LANGUAGE,
        beam_size=WHISPER_BEAM_SIZE,
        condition_on_previous_text=False,
        vad_filter=VAD_ENABLED,
        vad_parameters={
            "min_silence_duration_ms": VAD_MIN_SILENCE_MS,
            "speech_pad_ms": VAD_SPEECH_PAD_MS,
        },
        word_timestamps=True,
    )

    words: list[dict[str, Any]] = []

    for seg in raw_segments:
        seg_text = (seg.text or "").strip()
        if FILTRAR_ALUCINACOES and seg_text and ALUCINACOES_RE.search(seg_text):
            continue

        seg_words = getattr(seg, "words", None) or []
        if not seg_words and seg_text:
            tokens = [t for t in seg_text.split() if t.strip()]
            span = max(0.15, float(seg.end) - float(seg.start))
            step = span / max(1, len(tokens))
            for i, tok in enumerate(tokens):
                seg_words.append(
                    type(
                        "WordObj",
                        (),
                        {
                            "word": tok,
                            "start": float(seg.start) + i * step,
                            "end": float(seg.start) + (i + 1) * step,
                        },
                    )()
                )

        for w in seg_words:
            raw_tok = (w.word or "").strip()
            if not raw_tok:
                continue

            rel_start = float(w.start) - ctx_before
            rel_end = float(w.end) - ctx_before
            if rel_end <= 0.02 or rel_start >= duration - 0.02:
                continue

            # Pontuação isolada (! ou ?) cola na palavra anterior
            if raw_tok in ("!", "?", "?!", "!?") and words:
                words[-1]["word"] += raw_tok
                words[-1]["end"] = max(words[-1]["end"], min(duration, rel_end))
                continue

            cleaned = raw_tok.strip(" ,.;:\"'()[]{}-—")
            if not cleaned or not re.search(r"[A-Za-z0-9À-ÿ]", cleaned):
                continue

            start = max(0.0, min(duration - 0.10, rel_start))
            end = max(start + 0.10, min(duration, rel_end))
            words.append({"word": cleaned.upper(), "start": start, "end": end})

    words = _apply_word_corrections(words, correcoes)

    # Garante zero sobreposição (estritamente 1 palavra por vez na tela)
    for i in range(len(words) - 1):
        curr = words[i]
        nxt = words[i + 1]
        if nxt["start"] < curr["end"]:
            curr["end"] = nxt["start"]
            if curr["end"] < curr["start"] + 0.06:
                new_bound = min(duration - 0.06, curr["start"] + 0.06)
                curr["end"] = new_bound
                nxt["start"] = new_bound
                nxt["end"] = max(nxt["end"], nxt["start"] + 0.06)

    for w in words:
        w["start"] = round(max(0.0, w["start"]), 3)
        w["end"] = round(min(duration, max(w["start"] + 0.05, w["end"])), 3)

    return words


def _format_ass_ts(seconds: float) -> str:
    """Converte segundos em timestamp ASS H:MM:SS.cs."""
    cs_total = max(0, int(round(seconds * 100)))
    hours, rem = divmod(cs_total, 360_000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def generate_pop_ass(
    words: list[dict[str, Any]],
    accent_color: str,
    layout_mode: str,
    font_name: str,
    out_ass_path: Path,
) -> None:
    """Gera legenda ASS dinâmica com estritamente 1 palavra por vez em estilo POP."""
    accent_bgr = ASS_ACCENT_COLORS.get((accent_color or "amarelo").lower(), ASS_ACCENT_COLORS["amarelo"])
    sub_y = 1470 if layout_mode == "centro_blur" else 1500

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: PopWord,{font_name},112,{accent_bgr},{ASS_WHITE},&H000A0A0A&,&HAA000000&,0,0,0,0,100,100,0,0,1,10,5,5,30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for item in words:
        word_text = item["word"].replace("{", "").replace("}", "").strip()
        if not word_text:
            continue
        start_ts = _format_ass_ts(item["start"])
        end_ts = _format_ass_ts(item["end"])
        if end_ts <= start_ts:
            end_ts = _format_ass_ts(item["start"] + 0.08)

        bare = re.sub(r"[^A-Z0-9À-Ÿ]", "", word_text)
        color_tag = ASS_WHITE if bare in STOPWORDS_PT else accent_bgr
        fs_tag = f"\\fs{max(72, int(round(112 * 9.5 / len(word_text))))}" if len(word_text) > 9 else ""

        tag = (
            f"{{\\an5\\pos(540,{sub_y}){fs_tag}"
            f"\\fscx82\\fscy82\\t(0,75,\\fscx112\\fscy112)\\t(75,150,\\fscx100\\fscy100)"
            f"\\c{color_tag}}}{word_text}"
        )
        lines.append(f"Dialogue: 0,{start_ts},{end_ts},PopWord,,0,0,0,,{tag}\n")

    out_ass_path.parent.mkdir(parents=True, exist_ok=True)
    out_ass_path.write_text("".join(lines), encoding="utf-8-sig")


# ==============================================================================
# NORMALIZAÇÃO DE ÁUDIO YOUTUBE (-14 LUFS) E FILTERGRAPH FFmpeg
# ==============================================================================


def build_loudnorm_filter(video_path: Path, in_seg: float, duration: float) -> str:
    """Executa passe de medição e retorna filtro loudnorm linear para -14 LUFS."""
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-y",
        "-ss",
        f"{in_seg:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=SUBPROCESS_FLAGS,
    )
    stderr = res.stderr or ""
    json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.DOTALL)
    if json_match:
        try:
            stats = json.loads(json_match.group(0))
            input_i = str(stats.get("input_i", "")).strip()
            if input_i and input_i != "-inf" and float(input_i) > -70.0:
                return (
                    f"loudnorm=I=-14:TP=-1.5:LRA=11:"
                    f"measured_I={stats['input_i']}:"
                    f"measured_TP={stats['input_tp']}:"
                    f"measured_LRA={stats['input_lra']}:"
                    f"measured_thresh={stats['input_thresh']}:"
                    f"offset={stats['target_offset']}:linear=true,"
                    f"aresample=48000"
                )
        except Exception:
            pass
    return "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"


def _normalize_segments(
    raw_segments: list[dict[str, Any]] | None,
    duration: float,
) -> list[dict[str, Any]]:
    """Garante ao menos um segmento visual válido cobrindo o clipe."""
    if not raw_segments:
        return [{"inicio": 0.0, "fim": duration, "modo": "split"}]
    cleaned: list[dict[str, Any]] = []
    for seg in raw_segments:
        modo = str(seg.get("modo", "split")).strip().lower()
        if modo not in ("split", "split_zoom", "fullscreen_game", "centro_blur"):
            raise ValueError(f"Modo visual desconhecido: '{modo}'")
        ini = max(0.0, float(seg.get("inicio", 0.0)))
        fim = min(duration, float(seg.get("fim", duration)))
        if fim > ini:
            cleaned.append({"inicio": ini, "fim": fim, "modo": modo})
    return cleaned or [{"inicio": 0.0, "fim": duration, "modo": "split"}]


def build_filtergraph(
    segments: list[dict[str, Any]],
    presets: dict[str, tuple[int, int, int, int]],
    src_w: int,
    src_h: int,
    headline_dur_seg: float,
    ass_path: Path,
    fonts_dir: Path,
    loudnorm_filter: str,
    has_audio: bool,
) -> str:
    """Constrói filter_complex determinístico para corte seco, overlays, ASS e áudio."""
    unique_modes: list[str] = []
    for seg in segments:
        if seg["modo"] not in unique_modes:
            unique_modes.append(seg["modo"])

    fc: list[str] = [f"[0:v]fps={TARGET_FPS},setpts=PTS-STARTPTS[vsrc]"]

    if len(unique_modes) == 1:
        mode_inputs = {unique_modes[0]: "[vsrc]"}
    else:
        split_pads = "".join(f"[vsrc_{m}]" for m in unique_modes)
        fc.append(f"[vsrc]split={len(unique_modes)}{split_pads}")
        mode_inputs = {m: f"[vsrc_{m}]" for m in unique_modes}

    cam_rect = compute_cover_crop(presets["cam"], CANVAS_W, 720, src_w, src_h)
    cam_zoom_rect = compute_cover_crop(presets["cam_zoom"], CANVAS_W, 720, src_w, src_h)
    game_rect = compute_cover_crop(presets["game"], CANVAS_W, 1200, src_w, src_h)

    for modo in unique_modes:
        src_pad = mode_inputs[modo]
        if modo in ("split", "split_zoom"):
            cx, cy, cw, ch = cam_zoom_rect if modo == "split_zoom" else cam_rect
            gx, gy, gw, gh = game_rect
            fc.append(f"{src_pad}split=2[{modo}_in_c][{modo}_in_g]")
            fc.append(
                f"[{modo}_in_c]crop={cw}:{ch}:{cx}:{cy},scale=1080:720:flags=lanczos,setsar=1[{modo}_cam]"
            )
            fc.append(
                f"[{modo}_in_g]crop={gw}:{gh}:{gx}:{gy},scale=1080:1200:flags=lanczos,setsar=1[{modo}_game]"
            )
            fc.append(f"[{modo}_cam][{modo}_game]vstack=inputs=2[mode_{modo}]")
        elif modo == "fullscreen_game":
            gx_raw, _, gw_raw, _ = presets["game"]
            fx, fy, fw, fh = compute_cover_crop((gx_raw, 0, gw_raw, src_h), CANVAS_W, CANVAS_H, src_w, src_h)
            fc.append(
                f"{src_pad}crop={fw}:{fh}:{fx}:{fy},scale=1080:1920:flags=lanczos,setsar=1[mode_{modo}]"
            )
        elif modo == "centro_blur":
            cx, cy, cw, ch = cam_rect
            gx_raw, _, gw_raw, _ = presets["game"]
            bx, by, bw, bh = compute_cover_crop((gx_raw, 0, gw_raw, src_h), CANVAS_W, CANVAS_H, src_w, src_h)
            fc.append(f"{src_pad}split=2[{modo}_in_bg][{modo}_in_c]")
            fc.append(
                f"[{modo}_in_bg]crop={bw}:{bh}:{bx}:{by},scale=1080:1920:flags=bilinear,"
                f"gblur=sigma=32,eq=saturation=1.25:brightness=-0.04,setsar=1[{modo}_bg]"
            )
            fc.append(
                f"[{modo}_in_c]crop={cw}:{ch}:{cx}:{cy},scale=1080:720:flags=lanczos,setsar=1[{modo}_cam]"
            )
            fc.append(f"[{modo}_bg][{modo}_cam]overlay=0:580[mode_{modo}]")

    cur_pad = f"[mode_{unique_modes[0]}]"
    for idx, other_mode in enumerate(unique_modes[1:], start=1):
        windows = [
            f"between(t,{seg['inicio']:.3f},{seg['fim']:.3f})"
            for seg in segments
            if seg["modo"] == other_mode
        ]
        enable_expr = "+".join(windows)
        next_pad = f"[v_mix_{idx}]"
        fc.append(f"{cur_pad}[mode_{other_mode}]overlay=0:0:enable='{enable_expr}'{next_pad}")
        cur_pad = next_pad

    # Headline (0..headline_dur_seg) e marca d'água permanente
    fc.append("[1:v]format=rgba[v_hl_img]")
    fc.append(
        f"{cur_pad}[v_hl_img]overlay=0:0:shortest=1:enable='between(t,0.000,{headline_dur_seg:.3f})'[v_hl]"
    )
    fc.append("[2:v]format=rgba[v_wm_img]")
    fc.append("[v_hl][v_wm_img]overlay=0:0:shortest=1[v_wm]")

    ass_esc = _escape_ffmpeg_filter_path(ass_path)
    fonts_esc = _escape_ffmpeg_filter_path(fonts_dir)
    fc.append(
        f"[v_wm]ass=filename='{ass_esc}':fontsdir='{fonts_esc}',format=yuv420p[vout]"
    )

    if has_audio:
        fc.append(f"[0:a]asetpts=PTS-STARTPTS,{loudnorm_filter}[aout]")

    return ";\n".join(fc)


def render_vertical_clip(
    clip: dict[str, Any],
    whisper_model: Any,
    poppins_path: Path,
    ass_font_name: str,
    out_dir: Path,
    encoder: str = "auto",
) -> Path:
    """Executa o pipeline completo de edição 9:16 para um único clipe especificado."""
    src_video = Path(clip["src"]).resolve()
    if not src_video.is_file():
        raise FileNotFoundError(f"Vídeo fonte não encontrado: {src_video}")

    info = probe_video(src_video)
    src_w = int(info.get("width") or 1920)
    src_h = int(info.get("height") or 1080)
    src_dur = float(info.get("duration") or 0.0)
    has_audio = len(info.get("audios") or []) > 0

    in_seg = max(0.0, float(clip["in_seg"]))
    out_seg = min(src_dur, float(clip["out_seg"])) if src_dur > 0 else float(clip["out_seg"])
    duration = round(out_seg - in_seg, 3)
    if duration < 10.0 - 1e-3:
        raise ValueError(
            f"Clipe '{clip.get('id')}' tem duração {duration:.2f}s (mínimo obrigatório é 10s)."
        )
    if duration > 90.0 + 1e-3:
        raise ValueError(
            f"Clipe '{clip.get('id')}' tem duração {duration:.2f}s (máximo permitido é 90s)."
        )

    presets = resolve_preset(str(clip["canal"]), clip)
    segments = _normalize_segments(clip.get("segmentos_visuais"), duration)
    primary_layout = segments[0]["modo"]

    headline_lines = list(clip["headline"])
    headline_style = str(clip.get("headline_estilo", "solta"))
    headline_dur = min(duration, max(5.0, float(clip.get("headline_duracao_seg", 7.0))))
    watermark_text = str(clip.get("watermark", "@user"))
    accent_color = str(clip.get("legenda_cor", "amarelo"))
    clip_id = str(clip["id"])

    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / f"{clip_id}.mp4"
    out_meta = out_dir / f"{clip_id}.meta.json"

    with tempfile.TemporaryDirectory(prefix=f"helix_{clip_id}_") as tmp:
        tmp_dir = Path(tmp)
        fonts_dir = tmp_dir / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(poppins_path, fonts_dir / poppins_path.name)

        headline_png = tmp_dir / "headline.png"
        watermark_png = tmp_dir / "watermark.png"
        ass_path = tmp_dir / "pop_words.ass"
        filter_script = tmp_dir / "filtergraph.txt"

        render_headline_png(headline_lines, headline_style, primary_layout, poppins_path, headline_png)
        render_watermark_png(watermark_text, primary_layout, poppins_path, watermark_png)

        print(f"[{clip_id}] Transcrevendo palavras ({duration:.2f}s)...")
        words = transcribe_clip_words(
            src_video,
            in_seg,
            duration,
            whisper_model,
            tmp_dir,
            correcoes=clip.get("correcoes_legenda"),
        )
        print(f"[{clip_id}] {len(words)} palavras cronometradas.")
        generate_pop_ass(words, accent_color, primary_layout, ass_font_name, ass_path)

        loudnorm_filter = build_loudnorm_filter(src_video, in_seg, duration) if has_audio else ""
        fg_text = build_filtergraph(
            segments=segments,
            presets=presets,
            src_w=src_w,
            src_h=src_h,
            headline_dur_seg=headline_dur,
            ass_path=ass_path,
            fonts_dir=fonts_dir,
            loudnorm_filter=loudnorm_filter,
            has_audio=has_audio,
        )
        filter_script.write_text(fg_text, encoding="utf-8")

        def _run_encode(codec: str) -> subprocess.CompletedProcess[str]:
            v_args = (
                [
                    "-c:v", "h264_nvenc",
                    "-preset", "p6",
                    "-cq", "19",
                    "-b:v", "0",
                    "-maxrate", "16M",
                    "-bufsize", "32M",
                    "-rc", "vbr",
                ]
                if codec == "nvenc"
                else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
            )
            a_args = (
                ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
                if has_audio
                else ["-an"]
            )
            cmd = [
                FFMPEG_BIN,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{in_seg:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(src_video),
                "-framerate",
                str(TARGET_FPS),
                "-loop",
                "1",
                "-t",
                f"{duration + 0.5:.3f}",
                "-i",
                str(headline_png),
                "-framerate",
                str(TARGET_FPS),
                "-loop",
                "1",
                "-t",
                f"{duration + 0.5:.3f}",
                "-i",
                str(watermark_png),
                "-filter_complex_script",
                str(filter_script),
                "-map",
                "[vout]",
                *a_args,
                *v_args,
                "-r",
                str(TARGET_FPS),
                "-shortest",
                "-movflags",
                "+faststart",
                str(out_mp4),
            ]
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=SUBPROCESS_FLAGS,
            )

        encoded = False
        if encoder in ("auto", "nvenc"):
            print(f"[{clip_id}] Renderizando 1080x1920 60fps (h264_nvenc)...")
            res = _run_encode("nvenc")
            if res.returncode == 0:
                encoded = True
            elif encoder == "nvenc":
                raise RuntimeError(f"Falha no h264_nvenc em '{clip_id}': {res.stderr.strip()}")
            else:
                print(f"  [AVISO] h264_nvenc falhou ({res.stderr.strip()[:140]}). Usando libx264...")

        if not encoded:
            print(f"[{clip_id}] Renderizando 1080x1920 60fps (libx264)...")
            res_x264 = _run_encode("x264")
            if res_x264.returncode != 0:
                raise RuntimeError(f"Falha no libx264 em '{clip_id}': {res_x264.stderr.strip()}")

        meta_payload = {
            "id": clip_id,
            "arquivo_saida": str(out_mp4),
            "arquivo_fonte": str(src_video),
            "canal": clip["canal"],
            "in_seg": in_seg,
            "out_seg": out_seg,
            "duracao_seg": duration,
            "formato_marketing": clip.get("formato_marketing", ""),
            "headline": [ln.upper() for ln in headline_lines],
            "headline_estilo": headline_style,
            "headline_duracao_seg": headline_dur,
            "legenda_cor": accent_color,
            "titulo_post": clip.get("titulo_post", ""),
            "segmentos_visuais": segments,
            "presets_usados": presets,
            "loudnorm": loudnorm_filter,
            "palavras": words,
        }
        out_meta.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return out_mp4


def process_spec(spec_path: Path, encoder: str = "auto") -> list[Path]:
    """Lê a especificação JSON, gera os clipes e libera a GPU antes do NVENC se necessário."""
    data = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    clips = data.get("clips") or []
    if not clips:
        raise ValueError("Especificação não possui itens em 'clips'.")

    out_dir = Path(data["output_dir"])
    poppins_path = resolve_poppins_black_path()
    ass_font_name = get_ass_font_name(poppins_path)

    whisper_model = load_whisper_model()
    rendered: list[Path] = []
    try:
        for clip in clips:
            mp4 = render_vertical_clip(
                clip=clip,
                whisper_model=whisper_model,
                poppins_path=poppins_path,
                ass_font_name=ass_font_name,
                out_dir=out_dir,
                encoder=encoder,
            )
            rendered.append(mp4)
    finally:
        del whisper_model
        gc.collect()

    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Renderiza cortes verticais 9:16 (1080x1920 60fps)."
    )
    parser.add_argument("--spec", required=True, help="Caminho para o arquivo JSON de especificação.")
    parser.add_argument(
        "--encoder",
        choices=("auto", "nvenc", "x264"),
        default="auto",
        help="Encoder de vídeo (padrão: auto).",
    )
    args = parser.parse_args()

    spec_file = Path(args.spec).resolve()
    if not spec_file.is_file():
        raise FileNotFoundError(f"Arquivo --spec não encontrado: {spec_file}")

    outputs = process_spec(spec_file, encoder=args.encoder)
    print("\nConcluído! Arquivos renderizados:")
    for p in outputs:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
