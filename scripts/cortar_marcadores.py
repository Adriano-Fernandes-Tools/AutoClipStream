"""Exporta clipes .mp4 individuais para os marcadores de um lote já finalizado.

Utilitário opcional para gerar cortes físicos em 05_cortes_extraidos/<lote_id>/
a partir de _historico_lotes/<lote_id>/decisao_lote.json e dos vídeos em
04_para_edicao/<lote_id>/.

Uso:
    py -3.11 cortar_marcadores.py --canal NOME --lote lote_YYYYMMDD_HHMMSS
    py -3.11 cortar_marcadores.py --canal NOME --lote lote_YYYYMMDD_HHMMSS --folga 2.0
    py -3.11 cortar_marcadores.py --canal NOME --lote lote_YYYYMMDD_HHMMSS --copia
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from pathlib import Path
import sys
from typing import Any

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import FFMPEG_BIN, SUBPROCESS_FLAGS, ensure_channel_structure, format_mmss, load_channels_config
from premiere_xml import probe_video


def _slugify(text: str, max_len: int = 48) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_txt = norm.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", ascii_txt).strip("_")
    return (cleaned[:max_len].strip("_")) or "corte"


def _cortar_trecho(
    src_video: Path,
    dst_mp4: Path,
    start_seg: float,
    duracao_seg: float,
    modo_copia: bool,
    encoder: str,
) -> None:
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)

    if modo_copia:
        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seg:.3f}",
            "-i",
            str(src_video),
            "-t",
            f"{duracao_seg:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(dst_mp4),
        ]
        subprocess.run(cmd, check=True, creationflags=SUBPROCESS_FLAGS)
        return

    def _run_encode(codec: str) -> subprocess.CompletedProcess[str]:
        v_args = (
            ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
            if codec == "nvenc"
            else ["-c:v", "libx264", "-preset", "fast", "-crf", "19"]
        )
        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seg:.3f}",
            "-i",
            str(src_video),
            "-t",
            f"{duracao_seg:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            *v_args,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dst_mp4),
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=SUBPROCESS_FLAGS,
        )

    if encoder in ("auto", "nvenc"):
        res = _run_encode("nvenc")
        if res.returncode == 0:
            return
        if encoder == "nvenc":
            raise RuntimeError(f"Falha no h264_nvenc: {res.stderr.strip()}")
        print(f"  [AVISO] h264_nvenc indisponível ({res.stderr.strip()[:120]}). Usando libx264...")

    res_x264 = _run_encode("x264")
    if res_x264.returncode != 0:
        raise RuntimeError(f"Falha ao cortar com libx264: {res_x264.stderr.strip()}")


def exportar_cortes_lote(
    channel_name: str,
    lote_id: str,
    folga_seg: float = 2.0,
    modo_copia: bool = False,
    encoder: str = "auto",
    output_dir: str | None = None,
) -> list[Path]:
    paths = ensure_channel_structure(channel_name)
    safe_name = paths["name"].name

    decisao_path = paths["_historico_lotes"] / lote_id / "decisao_lote.json"
    if not decisao_path.exists():
        raise FileNotFoundError(f"decisao_lote.json não encontrado em {decisao_path}")

    decisao: dict[str, Any] = json.loads(decisao_path.read_text(encoding="utf-8-sig"))
    edicao_dir = paths["04_para_edicao"] / lote_id

    # Resolve pasta de saída: output_dir > padroes.output_dir > 05_cortes_extraidos/lote_id
    if not output_dir:
        cfg = load_channels_config()
        output_dir = str(cfg.get("padroes", {}).get("output_dir", "")).strip()

    if output_dir:
        saida_dir = Path(output_dir) / safe_name / lote_id
    else:
        saida_dir = paths["05_cortes_extraidos"] / lote_id

    gerados: list[Path] = []
    indice = 1

    for bloco in decisao.get("blocos", []):
        marcadores = bloco.get("marcadores") or []
        if not marcadores:
            continue
        video_path = edicao_dir / str(bloco["arquivo"])
        if not video_path.exists():
            print(f"[AVISO] Vídeo não encontrado em 04_para_edicao: {video_path}")
            continue

        duracao_video = float(probe_video(video_path)["duration"])

        for m in marcadores:
            in_seg = float(m.get("in_seg", 0.0))
            out_raw = m.get("out_seg")
            out_seg = float(out_raw) if out_raw is not None else None

            inicio = max(0.0, in_seg - max(0.0, folga_seg))
            if out_seg is not None and out_seg > in_seg:
                fim = min(duracao_video, out_seg + max(0.0, folga_seg))
            else:
                fim = min(duracao_video, in_seg + 30.0)

            dur_corte = fim - inicio
            if dur_corte <= 0.2:
                continue

            titulo = str(m.get("titulo") or f"corte_{indice}")
            nome_arquivo = f"{indice:02d}_{_slugify(titulo)}.mp4"
            destino = saida_dir / nome_arquivo

            print(
                f"[{safe_name}] Cortando #{indice:02d} ({format_mmss(inicio)} -> {format_mmss(fim)}): {nome_arquivo}"
            )
            _cortar_trecho(video_path, destino, inicio, dur_corte, modo_copia, encoder)
            gerados.append(destino)
            indice += 1

    print(f"\n[{safe_name}] {len(gerados)} corte(s) salvo(s) em: {saida_dir}")
    return gerados


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Corta clipes .mp4 individuais a partir dos marcadores de um lote."
    )
    parser.add_argument("--canal", required=True, help="Nome do canal.")
    parser.add_argument("--lote", required=True, help="ID do lote (ex: lote_20261119_120000).")
    parser.add_argument(
        "--folga",
        type=float,
        default=2.0,
        help="Segundos extras adicionados antes e depois de cada marcador (padrão: 2.0).",
    )
    parser.add_argument(
        "--copia",
        action="store_true",
        help="Usa -c copy sem re-encodar (instantâneo, mas limitado aos keyframes).",
    )
    parser.add_argument(
        "--encoder",
        choices=("auto", "nvenc", "x264"),
        default="auto",
        help="Encoder ao cortar com precisão no frame (padrão: auto).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Pasta de saída dos clips. Se omitido, usa 05_cortes_extraidos/ ou o padrão configurado.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exportar_cortes_lote(
        channel_name=args.canal,
        lote_id=args.lote,
        folga_seg=args.folga,
        modo_copia=args.copia,
        encoder=args.encoder,
        output_dir=getattr(args, "output_dir", None),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
