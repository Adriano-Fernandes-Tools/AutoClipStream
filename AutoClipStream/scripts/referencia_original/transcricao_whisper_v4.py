import os
import re
import time
from collections import namedtuple
from faster_whisper import WhisperModel

# =============================================================================
#  PAINEL DE CONTROLE — no dia a dia, mexa só nesta parte
# =============================================================================

# --- PASTA -------------------------------------------------------------------
# None = usa a pasta onde este script está salvo (automático)
# Caminho fixo: r"C:\Users\Adriano Fernandes\Desktop\Audios"
PASTA_FIXA = None

# --- IDIOMA ------------------------------------------------------------------
# None = automático | "pt" = português | "es" = espanhol | "en" = inglês
IDIOMA = "pt"

# --- FORMATO DO ARQUIVO GERADO -----------------------------------------------
# ".txt" = texto corrido simples
# ".md"  = texto com título e duração (Markdown)
# ".srt" = legenda com tempos (para Premiere etc.)
FORMATO_SAIDA = ".txt"

# --- MODO DE EXPORTAÇÃO ------------------------------------------------------
# "separado" = um arquivo para cada áudio/vídeo (bom para edição)
# "junto"    = tudo em um arquivo só (bom para jogar em IA / NotebookLM)
# Obs.: SRT sempre sai separado, senão os tempos ficam fora de sincronia.
MODO_EXPORTACAO = "separado"
NOME_ARQUIVO_JUNTO = "Dataset_Transcricoes"   # usado só no modo "junto"

# Só vale para o modo "junto":
# False = sempre o mesmo arquivo (cada rodada vai acumulando no final dele)
# True  = arquivo novo a cada rodada, com data e hora no nome (nunca mistura)
NOME_COM_DATA_HORA = False

# --- QUALIDADE E DESEMPENHO --------------------------------------------------
MODELO = "large-v3"            # "large-v3" = melhor | "medium" / "small" = mais rápido
DISPOSITIVO = "cuda"           # "cuda" = placa de vídeo | "cpu" = processador
COMPUTE_TYPE = "int8_float16"  # na placa de vídeo: "int8_float16" | na CPU: "int8"
BEAM_SIZE = 5                  # 5 = mais preciso | 1 = mais rápido

# --- FILTRO DE SILÊNCIO (VAD) ------------------------------------------------
# Detecta onde tem voz e ignora silêncio/ruído antes de transcrever.
USAR_VAD = True
SILENCIO_MINIMO_MS = 500       # pausa mínima para contar como silêncio
                               # 500 = padrão desta versão | 2000 = mais cuidadoso, corta menos fala
FOLGA_FALA_MS = 400            # folga mantida antes e depois de cada fala (400 = padrão do Whisper)

# --- LIMPEZA DE FRASES INVENTADAS --------------------------------------------
# Em trechos de silêncio, o Whisper às vezes inventa créditos de legenda que
# vieram do treino dele. Com True, essas frases são removidas e listadas no console.
FILTRAR_ALUCINACOES = False
ALUCINACOES = [                # dá para acrescentar outras frases aqui
    r"legendas?\s+(por\s+|pela\s+)?adriana\s+zanotto",
    r"adriana\s+zanotto",
    r"legendas?\s+pela\s+comunidade\s+amara\.?\s*org",
    r"amara\.org",
]

# --- ARQUIVOS ----------------------------------------------------------------
EXTENSOES = (".mp3", ".m4a", ".wav", ".mp4", ".flac")
PULAR_JA_PROCESSADOS = True    # True = consulta o log e pula o que já foi feito
NOME_LOG = "log_transcricoes_concluidas.txt"

# =============================================================================
#  DAQUI PARA BAIXO É O MOTOR DO SCRIPT — não precisa mexer
# =============================================================================

PASTA_AUDIOS = PASTA_FIXA or os.path.dirname(os.path.abspath(__file__))
LOG_PROCESSADOS = os.path.join(PASTA_AUDIOS, NOME_LOG)

_sufixo_data = f"_{time.strftime('%Y-%m-%d_%H-%M-%S')}" if NOME_COM_DATA_HORA else ""
ARQUIVO_MASTER = os.path.join(PASTA_AUDIOS, f"{NOME_ARQUIVO_JUNTO}{_sufixo_data}{FORMATO_SAIDA}")

_ALUCINACOES_RE = re.compile("|".join(ALUCINACOES), re.IGNORECASE) if ALUCINACOES else None

# Um trecho de fala já limpo: início, fim e texto
Trecho = namedtuple("Trecho", "start end text")


# --- Checagem das configurações ----------------------------------------------
def validar_configuracoes() -> str:
    """Confere o painel de controle e devolve o modo de exportação final."""
    if FORMATO_SAIDA not in (".txt", ".md", ".srt"):
        raise SystemExit(f"[CONFIG] FORMATO_SAIDA inválido: {FORMATO_SAIDA!r}. Use .txt, .md ou .srt")

    if MODO_EXPORTACAO not in ("separado", "junto"):
        raise SystemExit(f"[CONFIG] MODO_EXPORTACAO inválido: {MODO_EXPORTACAO!r}. Use 'separado' ou 'junto'")

    if MODO_EXPORTACAO == "junto" and FORMATO_SAIDA == ".srt":
        print("[AVISO] SRT não pode ser agrupado num arquivo só. Usando modo 'separado'.\n")
        return "separado"

    return MODO_EXPORTACAO


# --- Log de arquivos já processados ------------------------------------------
def carregar_log() -> set:
    if os.path.exists(LOG_PROCESSADOS):
        with open(LOG_PROCESSADOS, "r", encoding="utf-8") as f:
            return {linha.strip() for linha in f if linha.strip()}
    return set()


def registrar_no_log(nome_arquivo: str):
    with open(LOG_PROCESSADOS, "a", encoding="utf-8") as f:
        f.write(f"{nome_arquivo}\n")


# --- Limpeza de frases inventadas --------------------------------------------
def limpar_alucinacoes(texto: str):
    """Remove só as frases da lista. Retorna (texto_limpo, trechos_removidos)."""
    if not _ALUCINACOES_RE:
        return texto, []
    removidos = [m.group(0) for m in _ALUCINACOES_RE.finditer(texto)]
    if not removidos:
        return texto, []
    limpo = _ALUCINACOES_RE.sub("", texto)
    limpo = re.sub(r"\s{2,}", " ", limpo).strip()
    if not re.search(r"\w", limpo):  # sobrou só pontuação
        limpo = ""
    return limpo, removidos


def preparar_trechos(segmentos):
    """Lê os segmentos do Whisper, aplica a limpeza (se ligada) e descarta os vazios."""
    trechos = []
    removidos_total = 0
    for seg in segmentos:
        texto = seg.text.strip()
        if FILTRAR_ALUCINACOES:
            texto, removidos = limpar_alucinacoes(texto)
            for r in removidos:
                removidos_total += 1
                print(f'   [removido em {seg.start:.1f}s] "{r}"')
        if texto:
            trechos.append(Trecho(seg.start, seg.end, texto))
    return trechos, removidos_total


# --- Montagem do conteúdo de cada formato ------------------------------------
def formatar_tempo_srt(segundos: float) -> str:
    """Converte segundos (ex: 125.4) para o timecode do SRT (00:02:05,400)."""
    ms_total = int(round(segundos * 1000))
    horas, resto = divmod(ms_total, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    segs, ms = divmod(resto, 1000)
    return f"{horas:02}:{minutos:02}:{segs:02},{ms:03}"


def linha_idioma(info) -> str:
    """No modo automático, registra qual idioma o Whisper detectou."""
    if IDIOMA is None:
        return f"Idioma detectado: {info.language} ({info.language_probability:.0%})\n"
    return ""


def montar_srt(trechos) -> str:
    blocos = []
    for i, t in enumerate(trechos, start=1):
        blocos.append(f"{i}\n{formatar_tempo_srt(t.start)} --> {formatar_tempo_srt(t.end)}\n{t.text}\n")
    return "\n".join(blocos) + "\n"


def montar_md(trechos, nome_base: str, info) -> str:
    texto = " ".join(t.text for t in trechos)
    duracao = round(info.duration, 2)
    idioma = linha_idioma(info)
    idioma_md = f"**{idioma.strip()}**\n" if idioma else ""
    return (
        f"## 🎬 Transcrição: {nome_base}\n"
        f"**Duração:** {duracao}s\n"
        f"{idioma_md}\n"
        f"{texto}\n\n---\n\n"
    )


def montar_txt(trechos, nome_base: str, info) -> str:
    texto = " ".join(t.text for t in trechos)
    return (
        f"TÍTULO DO ARQUIVO: {nome_base}\n"
        f"{linha_idioma(info)}\n"
        f"{texto}\n\n" + "=" * 50 + "\n\n"
    )


def montar_conteudo(trechos, nome_base: str, info) -> str:
    if FORMATO_SAIDA == ".srt":
        return montar_srt(trechos)
    if FORMATO_SAIDA == ".md":
        return montar_md(trechos, nome_base, info)
    return montar_txt(trechos, nome_base, info)


# --- Fluxo principal ---------------------------------------------------------
def main():
    modo = validar_configuracoes()

    print(f"Pasta:   {PASTA_AUDIOS}")
    print(f"Idioma:  {IDIOMA or 'automático'} | Formato: {FORMATO_SAIDA} | Modo: {modo}")
    print(f"VAD: {'ligado' if USAR_VAD else 'desligado'} | "
          f"Limpeza de frases inventadas: {'ligada' if FILTRAR_ALUCINACOES else 'desligada'}\n")

    arquivos = [f for f in os.listdir(PASTA_AUDIOS) if f.lower().endswith(EXTENSOES)]
    if not arquivos:
        print("Nenhum arquivo de áudio/vídeo encontrado na pasta.")
        return

    print(f"Carregando o modelo {MODELO}...")
    model = WhisperModel(MODELO, device=DISPOSITIVO, compute_type=COMPUTE_TYPE)
    print("Modelo carregado.\n")

    ja_processados = carregar_log() if PULAR_JA_PROCESSADOS else set()

    for arquivo in arquivos:
        if arquivo in ja_processados:
            print(f"PULANDO: '{arquivo}' (já está no log)")
            continue

        caminho_audio = os.path.join(PASTA_AUDIOS, arquivo)
        nome_base = os.path.splitext(arquivo)[0]
        caminho_individual = os.path.join(PASTA_AUDIOS, f"{nome_base}{FORMATO_SAIDA}")

        print(f"\nProcessando: {arquivo}...")
        inicio = time.time()

        try:
            segmentos, info = model.transcribe(
                caminho_audio,
                language=IDIOMA,
                beam_size=BEAM_SIZE,
                condition_on_previous_text=False,  # evita loops/alucinações
                vad_filter=USAR_VAD,
                vad_parameters=dict(
                    min_silence_duration_ms=SILENCIO_MINIMO_MS,
                    speech_pad_ms=FOLGA_FALA_MS,
                ),
            )

            if IDIOMA is None:
                print(f"  Idioma detectado: {info.language} ({info.language_probability:.0%})")

            trechos, removidos_total = preparar_trechos(segmentos)

            if removidos_total:
                print(f"   {removidos_total} frase(s) inventada(s) removida(s).")

            if not trechos:
                print(f"AVISO: nenhuma fala detectada em '{arquivo}'.")
                registrar_no_log(arquivo)
                ja_processados.add(arquivo)
                continue

            conteudo = montar_conteudo(trechos, nome_base, info)

            if modo == "separado":
                with open(caminho_individual, "w", encoding="utf-8") as f:
                    f.write(conteudo)
            else:
                with open(ARQUIVO_MASTER, "a", encoding="utf-8") as f:
                    f.write(conteudo)

            tempo_total = round(time.time() - inicio, 2)
            print(f"CONCLUÍDO: '{arquivo}' em {tempo_total}s.")

            registrar_no_log(arquivo)
            ja_processados.add(arquivo)

        except Exception as e:
            print(f"ERRO ao processar '{arquivo}': {e}")

    if modo == "junto":
        print(f"\nFila finalizada. Tudo salvo em: {ARQUIVO_MASTER}")
    else:
        print("\nFila finalizada.")


if __name__ == "__main__":
    main()
