"""Geração de XML do Adobe Premiere Pro (xmeml v4) com marcadores In-Out.

Baseado no formato validado em referencia_original/modo_A_gerar_xml.py e no
modelo real exportado pelo Premiere Pro, estendido para:
- Colocar todos os blocos aprovados de uma rodada em uma única sequência contínua.
- Normalizar frame rates VFR de lives para taxas válidas de sequência no Premiere.
- Criar marcadores com duração (In e Out) na régua da timeline, com cores por tipo.
"""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape
import sys

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import FFPROBE_BIN, MARKER_TYPES, SUBPROCESS_FLAGS

TICKS_POR_SEGUNDO = 254_016_000_000

# Taxas aceitas pelo Premiere Pro em <rate>: (timebase, ntsc, Fraction exato)
STANDARD_RATES: tuple[tuple[int, str, Fraction], ...] = (
    (24, "TRUE", Fraction(24000, 1001)),
    (24, "FALSE", Fraction(24, 1)),
    (25, "FALSE", Fraction(25, 1)),
    (30, "TRUE", Fraction(30000, 1001)),
    (30, "FALSE", Fraction(30, 1)),
    (50, "FALSE", Fraction(50, 1)),
    (60, "TRUE", Fraction(60000, 1001)),
    (60, "FALSE", Fraction(60, 1)),
)


def _parse_fraction(raw: str | None) -> Fraction | None:
    """Converte string '30000/1001' ou '30/1' em Fraction positivo, se válida."""
    if not raw or "/" not in raw and not raw.isdigit():
        return None
    try:
        frac = Fraction(raw)
        if frac > 0:
            return frac
    except (ValueError, ZeroDivisionError):
        return None
    return None


def normalize_frame_rate(
    avg_raw: str | None, r_raw: str | None
) -> tuple[int, str, Fraction]:
    """Normaliza avg_frame_rate e r_frame_rate (inclusive VFR) para uma taxa Premiere."""
    avg_frac = _parse_fraction(avg_raw)
    r_frac = _parse_fraction(r_raw)

    avg_val = float(avg_frac) if avg_frac else None
    r_val = float(r_frac) if r_frac else None

    # Se r_frame_rate for razoável (<= 120 fps), verifica se bate exatamente com um padrão
    # e é compatível com avg_frame_rate (evita escolher 29.97 quando a live é 30 fps VFR).
    if r_val and 1.0 <= r_val <= 120.0:
        for tb, ntsc, std_frac in STANDARD_RATES:
            if abs(r_val - float(std_frac)) < 0.005:
                if avg_val is None or abs(avg_val - r_val) <= 2.5:
                    return tb, ntsc, std_frac

    target = avg_val if (avg_val and 1.0 <= avg_val <= 120.0) else None
    if target is None and r_val and 1.0 <= r_val <= 120.0:
        target = r_val

    if target is None:
        return 30, "FALSE", Fraction(30, 1)

    return min(STANDARD_RATES, key=lambda item: abs(float(item[2]) - target))


def _parse_duration_tag(tags: dict[str, Any] | None) -> float | None:
    """Extrai duração de tags MKV estilo '00:10:00.123000000' quando format.duration falta."""
    if not tags:
        return None
    for key in ("DURATION", "duration", "DURATION-eng"):
        val = tags.get(key)
        if not val or ":" not in str(val):
            continue
        try:
            hh, mm, ss = str(val).split(":", 2)
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        except ValueError:
            continue
    return None


def probe_video(video_path: Path) -> dict[str, Any]:
    """Lê dimensões, faixas de áudio, duração e taxa de quadros via ffprobe."""
    cmd = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video_path),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        creationflags=SUBPROCESS_FLAGS,
    )
    dados = json.loads(res.stdout)
    streams = dados.get("streams", [])
    fmt = dados.get("format", {})

    imagem = next((s for s in streams if s.get("codec_type") == "video"), None)
    if imagem is None:
        raise ValueError(f"Nenhuma faixa de vídeo encontrada em {video_path.name}")

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    dur_raw = fmt.get("duration") or imagem.get("duration")
    if dur_raw is not None:
        duration = float(dur_raw)
    else:
        duration = _parse_duration_tag(imagem.get("tags")) or _parse_duration_tag(
            fmt.get("tags")
        )
        if duration is None:
            raise ValueError(f"Não foi possível determinar a duração de {video_path.name}")

    timebase, ntsc, fps_exact = normalize_frame_rate(
        imagem.get("avg_frame_rate"), imagem.get("r_frame_rate")
    )

    return {
        "width": int(imagem["width"]),
        "height": int(imagem["height"]),
        "audios": audios,
        "duration": duration,
        "timebase": timebase,
        "ntsc": ntsc,
        "fps": fps_exact,
    }


def _rate_block(timebase: int, ntsc: str) -> str:
    return f"<rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate>"


def _path_to_pathurl(path: Path) -> str:
    return "file://localhost/" + quote(
        path.resolve().as_posix(), safe="/"
    ).replace(":", "%3a")


def _audio_track_layout(audios: list[dict[str, Any]]) -> list[tuple[int, str, int, int]]:
    """Mapeia faixas de áudio para o formato explodido do Premiere Pro."""
    faixas: list[tuple[int, str, int, int]] = []
    for idx, audio in enumerate(audios, start=1):
        channels = int(audio.get("channels") or 2)
        if channels >= 2:
            faixas.append((idx, "Stereo", 0, 2))
            faixas.append((idx, "Stereo", 1, 2))
        else:
            faixas.append((idx, "Mono", 0, 1))
    return faixas


def gerar_xml_sequencia(
    nome_sequencia: str,
    itens: list[dict[str, Any]],
    destino_xml: Path,
) -> tuple[Path, int]:
    """Gera um XML de sequência única do Premiere Pro contendo vários blocos em sequência.

    Cada item em `itens` deve ser:
        {
            "video": Path (caminho FINAL já movido para 04_para_edicao),
            "marcadores": [
                {
                    "in_seg": float,
                    "out_seg": float | None,
                    "titulo": str,
                    "comentario": str,
                    "tipo": str,
                },
                ...
            ],
        }
    Retorna (destino_xml, total_marcadores_gravados).
    """
    if not itens:
        raise ValueError("Lista de vídeos vazia ao gerar XML da sequência.")

    probed_items: list[dict[str, Any]] = []
    for item in itens:
        video_path = Path(item["video"])
        info = probe_video(video_path)
        probed_items.append(
            {
                "video": video_path,
                "info": info,
                "faixas": _audio_track_layout(info["audios"]),
                "marcadores": item.get("marcadores") or [],
            }
        )

    primeiro_info = probed_items[0]["info"]
    seq_tb: int = primeiro_info["timebase"]
    seq_ntsc: str = primeiro_info["ntsc"]
    seq_fps: Fraction = primeiro_info["fps"]
    seq_rate = _rate_block(seq_tb, seq_ntsc)
    seq_width: int = primeiro_info["width"]
    seq_height: int = primeiro_info["height"]

    # Descobre a taxa de amostragem e o maior layout de faixas de áudio entre os clipes
    sample_rate = 48000
    max_faixas: list[tuple[int, str, int, int]] = []
    for p in probed_items:
        audios = p["info"]["audios"]
        if audios and audios[0].get("sample_rate"):
            sample_rate = int(audios[0]["sample_rate"])
        if len(p["faixas"]) > len(max_faixas):
            max_faixas = p["faixas"]

    video_clipitems_xml: list[str] = []
    audio_tracks_items: list[list[str]] = [[] for _ in range(len(max_faixas))]
    xml_marcadores: list[str] = []
    total_marcadores = 0
    cursor = 0

    for clip_idx, entry in enumerate(probed_items, start=1):
        video_path: Path = entry["video"]
        info = entry["info"]
        faixas = entry["faixas"]
        duracao = float(info["duration"])

        quadros_seq = max(1, round(duracao * seq_fps))
        quadros_file = max(1, round(duracao * info["fps"]))
        ticks_fim = round(quadros_seq * TICKS_POR_SEGUNDO / seq_fps)

        start_frame = cursor
        end_frame = cursor + quadros_seq

        nome_esc = escape(video_path.name)
        url_esc = escape(_path_to_pathurl(video_path))
        file_rate = _rate_block(info["timebase"], info["ntsc"])
        master_id = f"masterclip-{clip_idx}"
        file_id = f"file-{clip_idx}"
        video_clip_id = f"clipitem-v-{clip_idx}"

        midia_audio = "".join(
            f"<audio><samplecharacteristics><depth>16</depth>"
            f"<samplerate>{int(a.get('sample_rate') or 48000)}</samplerate>"
            f"</samplecharacteristics><channelcount>{int(a.get('channels') or 2)}</channelcount></audio>"
            for a in info["audios"]
        )
        file_block = (
            f'<file id="{file_id}"><name>{nome_esc}</name><pathurl>{url_esc}</pathurl>'
            f"{file_rate}<duration>{quadros_file}</duration><media>"
            f"<video><duration>{quadros_file}</duration>"
            f"<samplecharacteristics>{file_rate}"
            f"<width>{info['width']}</width><height>{info['height']}</height>"
            f"</samplecharacteristics></video>{midia_audio}</media></file>"
        )

        # Ligações entre o clipe de vídeo e suas respectivas faixas de áudio
        links = [
            f"<link><linkclipref>{video_clip_id}</linkclipref>"
            f"<mediatype>video</mediatype><trackindex>1</trackindex>"
            f"<clipindex>{clip_idx}</clipindex></link>"
        ]
        for track_idx in range(1, len(faixas) + 1):
            audio_clip_id = f"clipitem-a-{clip_idx}-{track_idx}"
            links.append(
                f"<link><linkclipref>{audio_clip_id}</linkclipref>"
                f"<mediatype>audio</mediatype><trackindex>{track_idx}</trackindex>"
                f"<clipindex>{clip_idx}</clipindex><groupindex>1</groupindex></link>"
            )
        ligacoes_str = "".join(links)

        video_clipitems_xml.append(
            f'<clipitem id="{video_clip_id}">'
            f"<masterclipid>{master_id}</masterclipid>"
            f"<name>{nome_esc}</name><enabled>TRUE</enabled>"
            f"<duration>{quadros_seq}</duration>{seq_rate}"
            f"<start>{start_frame}</start><end>{end_frame}</end>"
            f"<in>0</in><out>{quadros_seq}</out>"
            f"<pproTicksIn>0</pproTicksIn><pproTicksOut>{ticks_fim}</pproTicksOut>"
            f"<alphatype>none</alphatype><pixelaspectratio>square</pixelaspectratio>"
            f"<anamorphic>FALSE</anamorphic>{file_block}{ligacoes_str}</clipitem>"
        )

        for track_pos, (stream_idx, tipo_track, _parte, _total) in enumerate(faixas):
            track_num = track_pos + 1
            audio_clip_id = f"clipitem-a-{clip_idx}-{track_num}"
            canal_attr = f' premiereChannelType="{tipo_track.lower()}"'
            audio_clip = (
                f'<clipitem id="{audio_clip_id}"{canal_attr}>'
                f"<masterclipid>{master_id}</masterclipid>"
                f"<name>{nome_esc}</name><enabled>TRUE</enabled>"
                f"<duration>{quadros_seq}</duration>{seq_rate}"
                f"<start>{start_frame}</start><end>{end_frame}</end>"
                f"<in>0</in><out>{quadros_seq}</out>"
                f"<pproTicksIn>0</pproTicksIn><pproTicksOut>{ticks_fim}</pproTicksOut>"
                f'<file id="{file_id}"/>'
                f"<sourcetrack><mediatype>audio</mediatype>"
                f"<trackindex>{stream_idx}</trackindex></sourcetrack>"
                f"{ligacoes_str}</clipitem>"
            )
            audio_tracks_items[track_pos].append(audio_clip)

        # Marcadores do bloco atual deslocados para o cursor da sequência
        for marca in entry["marcadores"]:
            in_seg = float(marca.get("in_seg", 0.0))
            out_raw = marca.get("out_seg")
            out_seg = float(out_raw) if out_raw is not None else None

            if in_seg < 0:
                in_seg = 0.0
            if in_seg > duracao:
                continue

            frame_in_rel = round(in_seg * seq_fps)
            max_in_rel = max(0, quadros_seq - 1)
            frame_in = cursor + min(max(0, frame_in_rel), max_in_rel)

            if out_seg is not None and out_seg > in_seg:
                out_clamped_seg = min(out_seg, duracao)
                frame_out_rel = round(out_clamped_seg * seq_fps)
                frame_out = min(end_frame, max(frame_in + 1, cursor + frame_out_rel))
            else:
                frame_out = -1

            tipo_key = str(marca.get("tipo") or "corte_forte")
            label_padrao, cor = MARKER_TYPES.get(
                tipo_key, MARKER_TYPES["corte_forte"]
            )
            titulo = str(marca.get("titulo") or label_padrao).strip()
            comentario = str(marca.get("comentario") or "").strip()
            tag_cor = f"<pproColor>{cor}</pproColor>" if cor else ""

            xml_marcadores.append(
                f"<marker><comment>{escape(comentario)}</comment>"
                f"<name>{escape(titulo)}</name>"
                f"<in>{frame_in}</in><out>{frame_out}</out>"
                f"{tag_cor}</marker>"
            )
            total_marcadores += 1

        cursor = end_frame

    total_quadros = max(1, cursor)

    saidas = "".join(
        f"<group><index>{c}</index><numchannels>1</numchannels><downmix>0</downmix>"
        f"<channel><index>{c}</index></channel></group>"
        for c in (1, 2)
    )

    if max_faixas:
        faixas_audio_xml = "".join(
            f'<track PannerCurrentValue="0.5" PannerIsInverted="true" PannerName="Balance" '
            f'currentExplodedTrackIndex="{parte}" totalExplodedTrackCount="{total}" '
            f'premiereTrackType="{tipo_track}">'
            + "".join(audio_tracks_items[idx])
            + "</track>"
            for idx, (_stream, tipo_track, parte, total) in enumerate(max_faixas)
        )
    else:
        faixas_audio_xml = (
            '<track PannerCurrentValue="0.5" PannerIsInverted="true" PannerName="Balance" '
            'currentExplodedTrackIndex="0" totalExplodedTrackCount="2" premiereTrackType="Stereo"></track>'
        )

    xml_str = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE xmeml>\n"
        '<xmeml version="4">'
        f'<sequence id="sequence-1" explodedTracks="true">'
        f"<duration>{total_quadros}</duration>{seq_rate}"
        f"<name>{escape(nome_sequencia)}</name>"
        "<media>"
        "<video>"
        f"<format><samplecharacteristics>{seq_rate}"
        f"<width>{seq_width}</width><height>{seq_height}</height>"
        "<pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>"
        f"<track>{''.join(video_clipitems_xml)}</track>"
        "</video>"
        "<audio>"
        "<numOutputChannels>2</numOutputChannels>"
        f"<format><samplecharacteristics><depth>16</depth><samplerate>{sample_rate}</samplerate>"
        "</samplecharacteristics></format>"
        f"<outputs>{saidas}</outputs>"
        f"{faixas_audio_xml}"
        "</audio>"
        "</media>"
        f"{''.join(xml_marcadores)}"
        "</sequence>"
        "</xmeml>\n"
    )

    destino_xml.parent.mkdir(parents=True, exist_ok=True)
    destino_xml.write_text(xml_str, encoding="utf-8")
    return destino_xml, total_marcadores


def gerar_xml_individual(
    video: Path,
    marcadores: list[dict[str, Any]],
    destino_xml: Path,
) -> tuple[Path, int]:
    """Gera um XML de sequência única contendo apenas 1 bloco de vídeo."""
    return gerar_xml_sequencia(
        nome_sequencia=video.stem,
        itens=[{"video": video, "marcadores": marcadores}],
        destino_xml=destino_xml,
    )
