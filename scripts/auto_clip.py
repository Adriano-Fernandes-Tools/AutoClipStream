"""Engine de detecção automática de momentos interessantes em blocos de live.

Calcula um score de 0 a 100 por segmento combinando:
  - audio_score: picos de volume RMS via ffmpeg loudnorm
  - text_score:  densidade de palavras-chave de impacto na transcrição
  - ai_score:    (opcional) análise por LLM via API ou Ollama local

Uso direto (linha de comando):
    py scripts/auto_clip.py --canal NomeCanal --bloco caminho/arquivo.mkv
    py scripts/auto_clip.py --canal NomeCanal --todos
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import (
    CANAIS_DIR,
    FFMPEG_BIN,
    FFPROBE_BIN,
    IMPACT_KEYWORDS_RE,
    MARKETING_DIR,
    SUBPROCESS_FLAGS,
    VIDEO_EXTENSIONS,
    ensure_channel_structure,
    format_mmss,
    load_channels_config,
    sanitize_channel_name,
)


# ==============================================================================
# PONTUAÇÃO DE ÁUDIO
# ==============================================================================

def score_audio(video_path: Path) -> float:
    """Retorna score de 0-100 baseado no pico de volume integrado do bloco.

    Usa ffmpeg -af loudnorm para obter o integrated loudness (LUFS).
    Quanto mais alto (menos negativo), mais animado o áudio.
    Referência: live calma ≈ -24 LUFS, momento épico ≈ -12 LUFS.
    """
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-i", str(video_path),
        "-af", "loudnorm=print_format=json",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=SUBPROCESS_FLAGS,
        )
        # loudnorm prints JSON to stderr
        stderr = result.stderr
        json_match = re.search(r"\{[^{}]+\}", stderr, re.DOTALL)
        if not json_match:
            return 30.0
        data = json.loads(json_match.group())
        lufs = float(data.get("input_i", "-24"))
        # Map: -30 LUFS → 0 pts, -6 LUFS → 100 pts (linear clamp)
        score = max(0.0, min(100.0, (lufs + 30) / 24 * 100))
        return round(score, 1)
    except Exception:
        return 30.0


def score_audio_peaks(video_path: Path, segment_seconds: int = 10) -> list[dict[str, Any]]:
    """Detecta picos de volume por segmento de N segundos dentro do bloco.

    Retorna lista de {'segundo': float, 'score': float} para cada janela de tempo
    com score acima de 50 — útil para localizar o momento exato dentro do bloco.
    """
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-i", str(video_path),
        "-af", f"astats=metadata=1:reset={segment_seconds},ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=SUBPROCESS_FLAGS,
        )
        peaks: list[dict[str, Any]] = []
        current_ts: float | None = None
        for line in result.stderr.splitlines():
            m_ts = re.search(r"pts_time:([\d.]+)", line)
            if m_ts:
                current_ts = float(m_ts.group(1))
            m_rms = re.search(r"RMS_level=([-\d.]+)", line)
            if m_rms and current_ts is not None:
                rms = float(m_rms.group(1))
                # Mapeia -50 dB -> 0, -6 dB -> 100
                score = max(0.0, min(100.0, (rms + 50) / 44 * 100))
                if score >= 50:
                    peaks.append({"segundo": round(current_ts, 2), "score": round(score, 1)})
        return peaks
    except Exception:
        return []


# ==============================================================================
# PONTUAÇÃO DE TEXTO (TRANSCRIÇÃO)
# ==============================================================================

def score_transcript(transcript_segments: list[dict[str, Any]]) -> float:
    """Retorna score de 0-100 baseado na densidade de palavras-chave de impacto.

    Recebe lista de segmentos Whisper com chave 'text'.
    """
    if not transcript_segments:
        return 0.0

    all_text = " ".join(str(s.get("text", "")) for s in transcript_segments)
    total_words = max(1, len(all_text.split()))
    hits = len(IMPACT_KEYWORDS_RE.findall(all_text))
    # 1 hit em 50 palavras ≈ 20 pts, 5 hits em 50 palavras ≈ 100 pts
    density = hits / total_words * 100
    score = min(100.0, density * 20)
    return round(score, 1)


def score_transcript_peaks(
    transcript_segments: list[dict[str, Any]],
    window_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    """Detecta janelas de N segundos com maior densidade de palavras de impacto.

    Retorna lista de {'segundo': float, 'score': float, 'texto': str} para usar
    como marcadores de posição dentro do bloco.
    """
    if not transcript_segments:
        return []

    peaks: list[dict[str, Any]] = []
    segs = [s for s in transcript_segments if "start" in s and "text" in s]
    if not segs:
        return []

    total_duration = max(s.get("end", s["start"] + 5) for s in segs)
    step = window_seconds / 2
    t = 0.0

    while t < total_duration:
        window_segs = [
            s for s in segs
            if s["start"] >= t and s["start"] < t + window_seconds
        ]
        if window_segs:
            text = " ".join(str(s["text"]) for s in window_segs)
            hits = len(IMPACT_KEYWORDS_RE.findall(text))
            words = max(1, len(text.split()))
            density = hits / words * 100
            window_score = min(100.0, density * 20)
            if window_score >= 30:
                peaks.append({
                    "segundo": round(t, 2),
                    "score": round(window_score, 1),
                    "texto": text[:200],
                })
        t += step

    return peaks


# ==============================================================================
# PONTUAÇÃO POR AI (OPCIONAL)
# ==============================================================================

def _load_marketing_guidelines(jogo: str | None = None) -> str:
    """Carrega diretrizes editoriais resumidas de Marketing/<jogo>/ ou Marketing/geral/."""
    try:
        tag = (jogo or "geral").strip().lower()
        if not MARKETING_DIR.exists():
            return ""

        # Mapeia sinônimos comuns para as pastas reais em Marketing/
        pasta_alvo = "geral"
        if "gta" in tag:
            pasta_alvo = "gta"
        elif "fortnite" in tag:
            pasta_alvo = "fortnite"

        target_dir = MARKETING_DIR / pasta_alvo
        if not target_dir.exists():
            target_dir = MARKETING_DIR / "geral"

        if not target_dir.exists():
            return ""

        lines: list[str] = []
        # Lê arquivos principais de gatilhos ou checklist
        for doc_name in ("gatilhos-e-hooks-gta.md", "retencao-edicao-e-checklist-gta.md", "README.md", "edicao-e-publicacao.md"):
            doc_path = target_dir / doc_name
            if doc_path.exists():
                text = doc_path.read_text(encoding="utf-8", errors="replace")
                # Extrai tópicos curtos (linhas com bullet points relevantes)
                for line in text.splitlines():
                    clean = line.strip()
                    if (clean.startswith("- ") or clean.startswith("* ") or clean.startswith("1.") or clean.startswith("2.")) and len(clean) > 8:
                        if not any(stop in clean.lower() for stop in ("http", "licença", "tabela", "código s0")):
                            lines.append(clean[:120])
                    if len(lines) >= 8:
                        break
            if len(lines) >= 8:
                break

        if lines:
            return "Diretrizes de retenção e ganchos virais (" + pasta_alvo.upper() + "):\n" + "\n".join(lines[:8])
    except Exception:
        pass
    return ""


def format_transcript_with_timestamps(segments: list[dict[str, Any]], max_chars: int = 12000) -> str:
    """Formata transcrição com marcações de tempo [MM:SS] cobrindo todo o vídeo."""
    lines: list[str] = []
    for s in segments:
        start_sec = float(s.get("start", 0))
        m = int(start_sec // 60)
        sec = int(start_sec % 60)
        text = str(s.get("text", "")).strip()
        if text:
            lines.append(f"[{m:02d}:{sec:02d}] {text}")
    full = "\n".join(lines)
    if len(full) > max_chars:
        return full[:max_chars] + "\n... [transcrição truncada no limite]"
    return full


def _build_ai_prompt(canal: str, transcript_text: str, context: str = "", jogo: str | None = None) -> str:
    guidelines = _load_marketing_guidelines(jogo)
    guide_section = f"\n{guidelines}\n" if guidelines else ""
    return (
        f"Você é um editor de vídeo especialista em cortes virais de lives de games no TikTok, Reels e Shorts.\n"
        f"Canal: {canal}\n"
        + (f"Jogo/Categoria: {jogo}\n" if jogo else "")
        + (f"Contexto da live: {context}\n" if context else "")
        + guide_section
        + f"\nTranscrição com minutagem do bloco de vídeo (início ao fim):\n{transcript_text}\n\n"
        "Avalie este bloco com um score de 0 a 100 indicando o potencial viral.\n"
        "Identifique de 3 a 6 dos momentos mais interessantes e virais distribuídos ao longo de TODO o vídeo (início, meio e fim do bloco), sem concentrar apenas nos primeiros minutos.\n"
        "Para cada momento, indique o segundo exato (em segundos desde o início do bloco, ex: 1m30s = 90) e uma breve descrição do gancho ou clímax.\n"
        "Responda APENAS em JSON no seguinte formato:\n"
        '{"score": 75, "momentos": [{"segundo": 95, "descricao": "reação épica ao clutch"}, {"segundo": 340, "descricao": "fuga insana da polícia"}]}'
    )


def score_ai_api(
    canal: str,
    transcript_text: str,
    api_url: str,
    api_key: str,
    modelo: str,
    context: str = "",
    jogo: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Chama endpoint OpenAI-compatible e retorna {'score': int, 'momentos': [...]}."""
    prompt = _build_ai_prompt(canal, transcript_text, context, jogo=jogo)
    payload = json.dumps({
        "model": modelo or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1000,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _parse_ai_response(content)
    except Exception as exc:
        return {"score": 0, "momentos": [], "error": str(exc)}


def score_ai_ollama(
    canal: str,
    transcript_text: str,
    modelo: str = "gemma3:4b",
    ollama_url: str = "http://localhost:11434",
    context: str = "",
    jogo: str | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    """Chama Ollama local via /api/chat e retorna {'score': int, 'momentos': [...], 'success': bool}."""
    target_model = modelo.strip() if modelo and modelo.strip() else "gemma3:4b"
    prompt = _build_ai_prompt(canal, transcript_text, context, jogo=jogo)
    payload = json.dumps({
        "model": target_model,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"num_gpu": 0},
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("message", {}).get("content", "")
        parsed = _parse_ai_response(content)
        parsed["success"] = bool(parsed.get("score", 0) > 0 or parsed.get("momentos"))
        return parsed
    except Exception as exc:
        return {"score": None, "momentos": [], "error": str(exc), "success": False}


def _parse_ai_response(text: str) -> dict[str, Any]:
    """Extrai JSON da resposta do LLM, tolerando texto antes/depois."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"score": 0, "momentos": [], "raw": text[:300]}
    try:
        data = json.loads(match.group())
        return {
            "score": int(data.get("score", 0)),
            "momentos": data.get("momentos", []),
        }
    except Exception:
        return {"score": 0, "momentos": [], "raw": text[:300]}


# ==============================================================================
# ENGINE PRINCIPAL
# ==============================================================================

def analyze_block(
    canal: str,
    video_path: Path,
    transcript_path: Path | None = None,
    config: dict[str, Any] | None = None,
    jogo: str | None = None,
) -> dict[str, Any]:
    """Analisa um bloco e retorna score final + lista de momentos detectados.

    Args:
        canal:            Nome do canal.
        video_path:       Caminho do .mkv a analisar.
        transcript_path:  JSON de transcrição (gerado por preparar_lote.py). Opcional.
        config:           Dicionário de padroes do canais.json. Se None, lê do arquivo.

    Returns:
        {
          "score": float,          # 0-100, score final ponderado
          "audio_score": float,
          "text_score": float,
          "ai_score": int | None,
          "momentos": [...],       # lista de {segundo, score, descricao, tipo}
          "video": str,            # nome do arquivo
          "canal": str,
        }
    """
    if config is None:
        cfg = load_channels_config()
        config = cfg.get("padroes", {})

    ai_backend = str(config.get("ai_backend", "desabilitado")).lower()
    min_score = float(config.get("auto_clip_min_score", 65))

    # --- Audio ---
    audio_score = score_audio(video_path)
    audio_peaks = score_audio_peaks(video_path)

    # --- Transcript ---
    text_score = 0.0
    text_peaks: list[dict[str, Any]] = []
    transcript_segs: list[dict[str, Any]] = []

    if transcript_path and transcript_path.exists():
        try:
            raw = json.loads(transcript_path.read_text(encoding="utf-8"))
            transcript_segs = raw if isinstance(raw, list) else raw.get("segments", [])
            text_score = score_transcript(transcript_segs)
            text_peaks = score_transcript_peaks(transcript_segs)
        except Exception:
            pass

    # --- AI (opcional) ---
    ai_score: int | None = None
    ai_momentos: list[dict[str, Any]] = []

    if ai_backend in ("api", "local", "local_custom") and transcript_segs:
        transcript_formatted = format_transcript_with_timestamps(transcript_segs)
        if ai_backend == "api":
            result = score_ai_api(
                canal=canal,
                transcript_text=transcript_formatted,
                api_url=str(config.get("ai_api_url", "")),
                api_key=str(config.get("ai_api_key", "")),
                modelo=str(config.get("ai_modelo", "")),
                jogo=jogo,
            )
        else:  # local
            result = score_ai_ollama(
                canal=canal,
                transcript_text=transcript_formatted,
                modelo=str(config.get("ai_modelo", "gemma3:4b")),
                ollama_url=str(config.get("ai_local_url", "http://localhost:11434")),
                jogo=jogo,
            )
        if result.get("success") and result.get("score") is not None:
            ai_score = int(result["score"])
            ai_momentos = result.get("momentos", [])
        else:
            err = result.get("error", "resposta vazia")
            print(f"[AUTO-CLIP] [AVISO] IA ({ai_backend}) não respondeu ({err}). Usando pontuação áudio/texto.", file=sys.stderr)

    # --- Score final ponderado ---
    if ai_score is not None:
        final_score = audio_score * 0.3 + text_score * 0.3 + ai_score * 0.4
    elif text_score > 0:
        # Transcricao disponivel: peso igual entre audio e texto
        final_score = audio_score * 0.5 + text_score * 0.5
    else:
        # Sem transcricao: score baseado no audio; se ha picos bons individualmente,
        # eleva o score global para refletir que o video tem momentos clipaveis.
        peak_scores = [p["score"] for p in audio_peaks] if audio_peaks else []
        if peak_scores:
            best_peak = max(peak_scores)
            final_score = max(audio_score, best_peak * 0.75)
        else:
            final_score = audio_score
    final_score = round(min(100.0, final_score), 1)

    # --- Consolidar momentos ---
    # Coleta todos os picos de audio com score >= 50 como candidatos.
    # O score global (final_score) decide se o video e clipavel; nao filtramos
    # candidatos por min_score aqui para nao perder momentos quando so ha audio.
    momentos: list[dict[str, Any]] = []

    for peak in audio_peaks:
        if peak["score"] >= 50:
            seg = int(peak["segundo"])
            m_label = f"{seg // 60}m{seg % 60:02d}s"
            momentos.append({
                "segundo": peak["segundo"],
                "score": peak["score"],
                "tipo": "audio_peak",
                "descricao": f"momento_{m_label}",
            })

    for peak in text_peaks:
        # text_peaks já vem filtrado com window_score >= 30 em score_transcript_peaks
        if peak["score"] >= 50:
            momentos.append({
                "segundo": peak["segundo"],
                "score": peak["score"],
                "tipo": "text_peak",
                "descricao": peak.get("texto", "")[:100],
            })

    for m in ai_momentos:
        momentos.append({
            "segundo": float(m.get("segundo", 0)),
            "score": float(m.get("score", ai_score or 50)),
            "tipo": "ai",
            "descricao": str(m.get("descricao", "")),
        })

    # --- Plugins (OCR na tela, Gemma Vision, etc) ---
    try:
        from plugins import PluginManager
        pm = PluginManager()
        frames_list: list[Path] = []
        stem = video_path.stem
        for p_dir in (video_path.parent, video_path.parent.parent):
            f_dir = p_dir / "frames"
            if f_dir.exists():
                frames_list = sorted(f_dir.glob(f"*{stem}*.jpg"))
                if frames_list:
                    break
        p_moments = pm.run_all(
            canal=canal,
            video_path=video_path,
            frames=frames_list,
            transcript_text=transcript_text if "transcript_text" in locals() else "",
            config=config,
        )
        for pm_item in p_moments:
            momentos.append(pm_item)
            if pm_item.get("score", 0) > final_score:
                final_score = round(max(final_score, pm_item["score"] * 0.9), 1)
    except Exception:
        pass

    # Remove duplicatas próximas (< 10s de distância), mantém maior score
    momentos.sort(key=lambda x: x["segundo"])
    deduped: list[dict[str, Any]] = []
    for m in momentos:
        if deduped and abs(m["segundo"] - deduped[-1]["segundo"]) < 10:
            if m["score"] > deduped[-1]["score"]:
                deduped[-1] = m
        else:
            deduped.append(m)

    return {
        "score": final_score,
        "audio_score": audio_score,
        "text_score": text_score,
        "ai_score": ai_score,
        "momentos": deduped,
        "video": video_path.name,
        "canal": canal,
    }


def save_auto_markers(canal: str, block_stem: str, analysis: dict[str, Any]) -> Path:
    """Salva o resultado da análise como marcadores_automaticos/<stem>.json no canal."""
    safe = sanitize_channel_name(canal)
    paths = ensure_channel_structure(safe)
    markers_dir = paths["root"] / "marcadores_automaticos"
    markers_dir.mkdir(exist_ok=True)
    out = markers_dir / f"{block_stem}.json"
    out.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ==============================================================================
# CLI
# ==============================================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detecta momentos interessantes em blocos de live.")
    p.add_argument("--canal", required=True, help="Nome do canal.")
    p.add_argument("--bloco", help="Caminho completo do arquivo .mkv a analisar.")
    p.add_argument("--todos", action="store_true", help="Analisa todos os blocos em 02_para_processar/.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_channels_config()
    padroes = cfg.get("padroes", {})

    canal = sanitize_channel_name(args.canal)
    paths = ensure_channel_structure(canal)
    fila = paths["02_para_processar"]
    pre_dir = fila / "_pre_transcricoes"

    targets: list[Path] = []
    if args.bloco:
        targets = [Path(args.bloco)]
    elif args.todos:
        targets = [p for p in sorted(fila.iterdir()) if p.suffix.lower() in VIDEO_EXTENSIONS]
    else:
        print("Informe --bloco ou --todos.")
        return 1

    for video_path in targets:
        transcript_path = pre_dir / f"{video_path.stem}.json" if pre_dir.exists() else None
        print(f"Analisando: {video_path.name}")
        result = analyze_block(canal, video_path, transcript_path, padroes)
        out = save_auto_markers(canal, video_path.stem, result)
        score_str = f"{result['score']:.0f}/100"
        momentos_count = len(result["momentos"])
        print(f"  Score: {score_str} | Momentos detectados: {momentos_count} → {out.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
