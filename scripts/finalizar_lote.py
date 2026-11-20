"""Finaliza um lote analisado pela IA, organiza pastas e gera o XML do Premiere Pro.

Ordem garantida de execução (evita Media Offline no Premiere):
1. Valida decisao_lote.json em 03_em_processamento/<lote_id>/.
2. Move blocos aprovados (+ .srt) para 04_para_edicao/<lote_id>/ e descartados
   para 06_arquivo_descartados/<lote_id>/.
3. Gera o XML do Premiere Pro apontando para os caminhos definitivos em 04_para_edicao/.
4. Gera resumo_lote.md, atualiza estado_live.md e _ultimo_contexto.json.
5. Arquiva metadados em _historico_lotes/<lote_id>/, limpa frames e remove a pasta do lote
   de 03_em_processamento/.

Uso:
    py -3.11 finalizar_lote.py --canal NOME --lote lote_YYYYMMDD_HHMMSS
    py -3.11 finalizar_lote.py --todos
"""

from __future__ import annotations

import argparse
import json
import subprocess
import re
import shutil
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

# Ensure script directory is available in module search path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import (
    CANAIS_DIR,
    MARKER_TYPES,
    VIDEO_EXTENSIONS,
    ensure_channel_structure,
    format_mmss,
    list_channel_names,
    load_manual_markers,
    sanitize_channel_name,
    save_manual_markers,
)
from premiere_xml import gerar_xml_individual, gerar_xml_sequencia


def _carregar_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _extrair_ordem_blocos(lote_dir: Path, decisao: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordena os blocos da decisão preservando a ordem cronológica de _pacote_ia.json."""
    blocos_decisao = decisao.get("blocos")
    if not isinstance(blocos_decisao, list):
        raise ValueError("Campo 'blocos' ausente ou inválido em decisao_lote.json.")

    por_nome: dict[str, dict[str, Any]] = {}
    for item in blocos_decisao:
        nome = str(item.get("arquivo") or "").strip()
        if not nome:
            raise ValueError("Bloco sem campo 'arquivo' em decisao_lote.json.")
        por_nome[nome] = item

    # Confere se todos os arquivos de vídeo da pasta foram contemplados na decisão
    videos_na_pasta = sorted(
        p.name
        for p in lote_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    faltantes = [v for v in videos_na_pasta if v not in por_nome]
    if faltantes:
        raise ValueError(
            "Os seguintes vídeos do lote não estão listados em decisao_lote.json: "
            + ", ".join(faltantes)
        )

    pacote_path = lote_dir / "_pacote_ia.json"
    if pacote_path.exists():
        pacote = _carregar_json(pacote_path)
        ordem_nomes = [b["arquivo"] for b in pacote.get("blocos", []) if b.get("arquivo") in por_nome]
        for nome in por_nome:
            if nome not in ordem_nomes:
                ordem_nomes.append(nome)
        return [por_nome[n] for n in ordem_nomes]

    return list(blocos_decisao)


def _atualizar_estado_live(
    estado_path: Path,
    canal: str,
    lote_id: str,
    decisao: dict[str, Any],
    aprovados_count: int,
    descartados_count: int,
    total_marcadores: int,
) -> None:
    """Atualiza o documento vivo estado_live.md com o resumo e o estado atual da rodada."""
    texto_atual = (
        estado_path.read_text(encoding="utf-8")
        if estado_path.exists()
        else ""
    )

    # Extrai o log cronológico anterior, se já existir
    marker_log = "## Log Cronológico por Rodada"
    log_anterior = ""
    if marker_log in texto_atual:
        _, resto = texto_atual.split(marker_log, 1)
        resto = resto.strip()
        if resto and "*(O histórico das rodadas aparecerá aqui.)*" not in resto:
            log_anterior = resto

    estado_novo = str(decisao.get("estado_atual_live") or "").strip()
    if not estado_novo:
        estado_novo = str(decisao.get("resumo_rodada") or "Sem resumo informado.").strip()

    arcos = decisao.get("arcos_em_aberto") or []
    if isinstance(arcos, list) and arcos:
        arcos_md = "\n".join(f"- {str(a).strip()}" for a in arcos if str(a).strip())
    else:
        arcos_md = "- *(Nenhum arco em aberto registrado.)*"

    resumo_rodada = str(decisao.get("resumo_rodada") or "Sem resumo.").strip()
    data_hora = datetime.now().strftime("%Y-%m-%d %H:%M")
    nova_entrada_log = (
        f"### `{lote_id}` ({data_hora})\n"
        f"- **Resultado:** {aprovados_count} aprovado(s) ({total_marcadores} marcador(es)) | "
        f"{descartados_count} descartado(s)\n"
        f"- **Resumo:** {resumo_rodada}"
    )

    bloco_log = (
        f"{log_anterior}\n\n{nova_entrada_log}".strip()
        if log_anterior
        else nova_entrada_log
    )

    conteudo_final = (
        f"# Estado da Live — {canal}\n\n"
        "> Documento vivo atualizado a cada lote processado. Serve de contexto contínuo para a IA entender missões, piadas recorrentes e arcos em andamento entre blocos de 10 minutos.\n\n"
        "## Estado Atual da Live\n\n"
        f"{estado_novo}\n\n"
        "## Arcos / Missões em Aberto\n\n"
        f"{arcos_md}\n\n"
        f"{marker_log}\n\n"
        f"{bloco_log}\n"
    )
    estado_path.write_text(conteudo_final, encoding="utf-8")


def _montar_resumo_lote_md(
    canal: str,
    lote_id: str,
    decisao: dict[str, Any],
    itens_aprovados: list[dict[str, Any]],
    itens_descartados: list[dict[str, Any]],
    xmls_gerados: list[Path],
) -> str:
    linhas: list[str] = [
        f"# Resumo do Lote `{lote_id}` — Canal `{canal}`",
        "",
        f"- **Finalizado em:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Blocos aprovados:** {len(itens_aprovados)}",
        f"- **Blocos descartados:** {len(itens_descartados)}",
        "",
        "## Resumo Editorial da Rodada",
        "",
        str(decisao.get("resumo_rodada") or "*(Sem resumo.)*"),
        "",
    ]

    if xmls_gerados:
        linhas.append("## XML(s) para o Premiere Pro (`File > Import`)")
        linhas.append("")
        for xp in xmls_gerados:
            linhas.append(f"- `{xp.name}`")
        linhas.append("")

    linhas.extend(
        [
            "## Marcadores In–Out Gerados",
            "",
        ]
    )

    tem_marcador = any(item["marcadores"] for item in itens_aprovados)
    if tem_marcador:
        linhas.append("| Bloco | Início | Fim | Duração | Tipo | Título | Comentário |")
        linhas.append("| :--- | :---: | :---: | :---: | :--- | :--- | :--- |")
        for item in itens_aprovados:
            nome_arq = item["video"].name
            for m in item["marcadores"]:
                in_seg = float(m.get("in_seg", 0.0))
                out_raw = m.get("out_seg")
                out_seg = float(out_raw) if out_raw is not None else None
                fim_str = format_mmss(out_seg) if out_seg is not None else "pontual"
                dur_str = (
                    f"{round(out_seg - in_seg)}s"
                    if (out_seg is not None and out_seg > in_seg)
                    else "-"
                )
                tipo_key = str(m.get("tipo") or "corte_forte")
                tipo_label = MARKER_TYPES.get(tipo_key, (tipo_key, None))[0]
                titulo = str(m.get("titulo") or tipo_label).replace("|", "/")
                comentario = str(m.get("comentario") or "").replace("|", "/")
                linhas.append(
                    f"| `{nome_arq}` | `{format_mmss(in_seg)}` | `{fim_str}` | {dur_str} | "
                    f"{tipo_label} | {titulo} | {comentario} |"
                )
    else:
        linhas.append("*(Nenhum marcador neste lote.)*")

    linhas.extend(["", "## Blocos Descartados (`06_arquivo_descartados`)", ""])
    if itens_descartados:
        for d in itens_descartados:
            motivo = str(d.get("motivo_descarte") or "Sem momentos de interesse.").strip()
            linhas.append(f"- `{d['arquivo']}` — {motivo}")
    else:
        linhas.append("*(Nenhum bloco descartado neste lote.)*")

    linhas.append("")
    return "\n".join(linhas)


def finalizar_lote(
    channel_name: str,
    lote_id: str,
    xml_por_bloco: bool = False,
    gerar_xml: bool = True,
) -> dict[str, Any]:
    """Processa decisao_lote.json, move vídeos, gera XML(s) e arquiva metadados."""
    paths = ensure_channel_structure(channel_name)
    safe_name = paths["name"].name
    lote_dir = paths["03_em_processamento"] / lote_id
    if not lote_dir.exists():
        raise FileNotFoundError(f"Pasta do lote não encontrada: {lote_dir}")

    decisao_path = lote_dir / "decisao_lote.json"
    if not decisao_path.exists():
        raise FileNotFoundError(f"Arquivo decisao_lote.json não encontrado em {lote_dir}")

    decisao = _carregar_json(decisao_path)
    blocos_ordenados = _extrair_ordem_blocos(lote_dir, decisao)

    # Reúne marcadores manuais do lote (de _pacote_ia.json + marcadores_manuais.json)
    arquivos_do_lote = {str(b["arquivo"]).strip() for b in blocos_ordenados}
    manuais_por_arquivo: dict[str, list[dict[str, Any]]] = {}

    pacote_path_pre = lote_dir / "_pacote_ia.json"
    if pacote_path_pre.exists():
        pacote_pre = _carregar_json(pacote_path_pre)
        for bp in pacote_pre.get("blocos", []):
            arq_nome = str(bp.get("arquivo") or "").strip()
            for mm in bp.get("marcadores_manuais") or []:
                manuais_por_arquivo.setdefault(arq_nome, []).append(mm)

    todos_manuais_canal = load_manual_markers(safe_name)
    for mm in todos_manuais_canal:
        arq_nome = str(mm.get("arquivo") or "").strip()
        if arq_nome not in arquivos_do_lote:
            continue
        existentes = manuais_por_arquivo.setdefault(arq_nome, [])
        mm_id = mm.get("id")
        if mm_id and any(ex.get("id") == mm_id for ex in existentes):
            continue
        existentes.append(mm)

    transcricoes_src = lote_dir / "transcricoes"
    destino_edicao_lote = paths["04_para_edicao"] / lote_id
    destino_descarte_lote = paths["06_arquivo_descartados"] / lote_id
    destino_historico_lote = paths["_historico_lotes"] / lote_id

    itens_aprovados: list[dict[str, Any]] = []
    itens_descartados: list[dict[str, Any]] = []

    print(f"\n=== [{safe_name}] Finalizando lote '{lote_id}' ===")

    # 1. PRIMEIRO move os arquivos de vídeo e .srt para os destinos definitivos
    for bloco in blocos_ordenados:
        nome_arquivo = str(bloco["arquivo"]).strip()
        src_video = lote_dir / nome_arquivo
        if not src_video.exists():
            raise FileNotFoundError(f"Vídeo listado em decisao_lote.json não existe: {src_video}")

        marcadores = list(bloco.get("marcadores") or [])
        manuais_bloco = manuais_por_arquivo.get(nome_arquivo, [])
        for mm in manuais_bloco:
            seg_manual = float(mm.get("segundo") or 0.0)
            ja_existe = any(
                str(m.get("tipo") or "") == "manual"
                and abs(float(m.get("in_seg", -999.0)) - seg_manual) < 0.5
                for m in marcadores
            )
            if not ja_existe:
                nota = str(mm.get("nota") or "").strip()
                titulo_m = f"★ {nota}" if nota else "★ Marcador manual"
                criado_em = str(mm.get("criado_em") or "").strip()
                marcadores.append(
                    {
                        "in_seg": seg_manual,
                        "out_seg": None,
                        "titulo": titulo_m,
                        "comentario": f"Marcado ao vivo em {criado_em}" if criado_em else "Marcado ao vivo",
                        "tipo": "manual",
                    }
                )

        status = str(bloco.get("status") or "").strip().lower()
        eh_aprovado = (status == "aprovado") or (len(marcadores) > 0) or bool(manuais_bloco)

        stem = Path(nome_arquivo).stem
        src_srt = transcricoes_src / f"{stem}.srt"

        if eh_aprovado:
            destino_edicao_lote.mkdir(parents=True, exist_ok=True)
            dst_video = destino_edicao_lote / nome_arquivo
            shutil.move(str(src_video), str(dst_video))
            if src_srt.exists():
                shutil.move(str(src_srt), str(destino_edicao_lote / src_srt.name))
            itens_aprovados.append({"video": dst_video, "marcadores": marcadores})
            print(f"  [APROVADO]   -> 04_para_edicao/{lote_id}/{nome_arquivo} ({len(marcadores)} marcador(es))")
        else:
            destino_descarte_lote.mkdir(parents=True, exist_ok=True)
            dst_video = destino_descarte_lote / nome_arquivo
            shutil.move(str(src_video), str(dst_video))
            if src_srt.exists():
                shutil.move(str(src_srt), str(destino_descarte_lote / src_srt.name))
            itens_descartados.append(
                {
                    "arquivo": nome_arquivo,
                    "motivo_descarte": bloco.get("motivo_descarte", ""),
                }
            )
            print(f"  [DESCARTADO] -> 06_arquivo_descartados/{lote_id}/{nome_arquivo}")
            cortes_desc = list(bloco.get("cortes_descartados") or [])
            if cortes_desc:
                conf_dir = destino_descarte_lote / "cortes_para_conferencia"
                conf_dir.mkdir(parents=True, exist_ok=True)
                for idx_c, cd in enumerate(cortes_desc, 1):
                    in_s = float(cd.get("in_seg", 0.0))
                    out_s = float(cd.get("out_seg", 0.0))
                    if out_s > in_s:
                        raw_title = str(cd.get("titulo") or f"{dst_video.stem}_corte_{idx_c}").strip()
                        safe_title = "".join(c for c in raw_title if c not in '<>:"/\\|?*').strip() or f"corte_{idx_c}"
                        out_mp4 = conf_dir / f"{safe_title}.mp4"
                        cmd = [
                            "ffmpeg", "-y", "-ss", f"{in_s:.3f}", "-to", f"{out_s:.3f}",
                            "-i", str(dst_video), "-c", "copy", "-avoid_negative_ts", "make_zero",
                            str(out_mp4),
                        ]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        print(f"    [CONFERÊNCIA] -> cortes_para_conferencia/{out_mp4.name}")

    # 2. DEPOIS gera o(s) XML(s) com o caminho definitivo em 04_para_edicao/<lote_id>/ (se --gerar-xml ou --xml-por-bloco)
    xmls_gerados: list[Path] = []
    total_marcadores = sum(len(item["marcadores"]) for item in itens_aprovados)
    if itens_aprovados and (gerar_xml or xml_por_bloco):
        total_marcadores = 0
        if xml_por_bloco:
            for item in itens_aprovados:
                xml_dest = destino_edicao_lote / f"{item['video'].stem}.xml"
                xp, count = gerar_xml_individual(item["video"], item["marcadores"], xml_dest)
                xmls_gerados.append(xp)
                total_marcadores += count
        else:
            xml_dest = destino_edicao_lote / f"{safe_name}_{lote_id}.xml"
            xp, count = gerar_xml_sequencia(
                nome_sequencia=f"{safe_name}_{lote_id}",
                itens=itens_aprovados,
                destino_xml=xml_dest,
            )
            xmls_gerados.append(xp)
            total_marcadores += count

        for xp in xmls_gerados:
            print(f"  [XML]        -> {xp}")

    # 3. Gera resumo_lote.md e atualiza estado_live.md + _ultimo_contexto.json
    resumo_md = _montar_resumo_lote_md(
        safe_name, lote_id, decisao, itens_aprovados, itens_descartados, xmls_gerados
    )
    if itens_aprovados:
        (destino_edicao_lote / "resumo_lote.md").write_text(resumo_md, encoding="utf-8")
    elif itens_descartados:
        (destino_descarte_lote / "resumo_lote.md").write_text(resumo_md, encoding="utf-8")

    _atualizar_estado_live(
        paths["estado_live"],
        safe_name,
        lote_id,
        decisao,
        len(itens_aprovados),
        len(itens_descartados),
        total_marcadores,
    )

    pacote_path = lote_dir / "_pacote_ia.json"
    if pacote_path.exists():
        pacote = _carregar_json(pacote_path)
        overlap = pacote.get("overlap_para_proximo_lote")
        if overlap:
            paths["ultimo_contexto"].write_text(
                json.dumps(overlap, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    # 4. Arquiva metadados e limpa frames / 03_em_processamento/<lote_id>/
    destino_historico_lote.mkdir(parents=True, exist_ok=True)
    (destino_historico_lote / "resumo_lote.md").write_text(resumo_md, encoding="utf-8")
    for meta_name in ("_pacote_ia.json", "_pacote_ia.md", "decisao_lote.json"):
        src_meta = lote_dir / meta_name
        if src_meta.exists():
            shutil.move(str(src_meta), str(destino_historico_lote / meta_name))

    if transcricoes_src.exists():
        dst_trans = destino_historico_lote / "transcricoes"
        if dst_trans.exists():
            shutil.rmtree(dst_trans)
        shutil.move(str(transcricoes_src), str(dst_trans))

    frames_dir = lote_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)

    # Remove de marcadores_manuais.json apenas os marcadores pertencentes a este lote
    if todos_manuais_canal:
        restantes = [
            mm
            for mm in todos_manuais_canal
            if str(mm.get("arquivo") or "").strip() not in arquivos_do_lote
        ]
        if len(restantes) != len(todos_manuais_canal):
            save_manual_markers(safe_name, restantes)

    shutil.rmtree(lote_dir, ignore_errors=True)
    print(f"[{safe_name}] Lote '{lote_id}' finalizado e arquivado em _historico_lotes/{lote_id}/.")

    return {
        "canal": safe_name,
        "lote_id": lote_id,
        "aprovados": len(itens_aprovados),
        "descartados": len(itens_descartados),
        "total_marcadores": total_marcadores,
        "xmls": [str(p) for p in xmls_gerados],
    }


def _lotes_prontos_do_canal(channel_name: str) -> list[str]:
    dir_proc = CANAIS_DIR / sanitize_channel_name(channel_name) / "03_em_processamento"
    if not dir_proc.exists():
        return []
    return sorted(
        p.name
        for p in dir_proc.iterdir()
        if p.is_dir() and (p / "decisao_lote.json").exists()
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finaliza lotes analisados (move arquivos, gera XML do Premiere e atualiza estado)."
    )
    parser.add_argument("--canal", help="Nome do canal.")
    parser.add_argument("--lote", help="ID específico do lote em 03_em_processamento/.")
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Finaliza todos os lotes que já possuem decisao_lote.json em todos os canais.",
    )
    parser.add_argument(
        "--gerar-xml",
        action="store_true",
        default=True,
        help="Gera arquivo(s) XML do Premiere para os blocos aprovados (ativado por padrão).",
    )
    parser.add_argument(
        "--sem-xml",
        action="store_true",
        help="Não gera arquivo(s) XML do Premiere.",
    )
    parser.add_argument(
        "--xml-por-bloco",
        action="store_true",
        help="Gera 1 XML separado por bloco aprovado em vez de 1 sequência única por rodada.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    deve_gerar_xml = False if args.sem_xml else True

    pares: list[tuple[str, str]] = []
    if args.canal:
        canal = sanitize_channel_name(args.canal)
        if args.lote:
            pares.append((canal, args.lote))
        else:
            for lid in _lotes_prontos_do_canal(canal):
                pares.append((canal, lid))
    elif args.todos:
        for canal in list_channel_names():
            for lid in _lotes_prontos_do_canal(canal):
                pares.append((canal, lid))
    else:
        canais_prontos = [c for c in list_channel_names() if _lotes_prontos_do_canal(c)]
        if len(canais_prontos) == 1:
            c = canais_prontos[0]
            for lid in _lotes_prontos_do_canal(c):
                pares.append((c, lid))
        elif len(canais_prontos) > 1:
            print("Há mais de um canal com lotes prontos para finalizar:")
            for c in canais_prontos:
                print(f"  - {c}: {', '.join(_lotes_prontos_do_canal(c))}")
            print("Informe --canal <nome> ou rode com --todos.")
            return 1

    if not pares:
        print("Nenhum lote com 'decisao_lote.json' encontrado em 03_em_processamento/.")
        return 0

    for canal, lid in pares:
        finalizar_lote(canal, lid, xml_por_bloco=args.xml_por_bloco, gerar_xml=deve_gerar_xml)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
