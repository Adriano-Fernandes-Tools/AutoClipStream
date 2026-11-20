# Referência dos Scripts — `Stream-clipping`

Documentação prática de todos os scripts Python, interface web, atalhos `.bat` e arquivos de referência em `E:\Antigravity\Stream-clipping\`.

---

## 1. Índice Rápido

| Arquivo | Papel | Como usar |
| :--- | :--- | :--- |
| [scripts/config.py](scripts/config.py) | Configurações centrais, caminhos, presets do Whisper/frames e cores do Premiere | Importado pelos demais scripts (não roda direto) |
| [scripts/gravar_lives.py](scripts/gravar_lives.py) | Grava 1 ou vários canais (Twitch / YouTube) splitando em `.mkv` | `1_GRAVAR_LIVES.bat`, CLI ou via App |
| [scripts/preparar_lote.py](scripts/preparar_lote.py) | Isola blocos fechados, transcreve (Whisper GPU) e gera grades 4x5 | `2_PREPARAR_LOTE.bat`, CLI ou via App |
| [scripts/finalizar_lote.py](scripts/finalizar_lote.py) | Separa aprovados/descartados, gera o XML único e atualiza `estado_live.md` | `3_FINALIZAR_LOTE.bat`, CLI ou via App |
| [scripts/premiere_xml.py](scripts/premiere_xml.py) | Motor gerador de `xmeml v4` para o Premiere Pro com marcadores In–Out | Importado por `finalizar_lote.py` |
| [scripts/cortar_marcadores.py](scripts/cortar_marcadores.py) | Utilitário opcional: exporta cada marcador como `.mp4` separado | CLI ou botão `Cortar MP4s` no App |
| [scripts/stream_app.py](scripts/stream_app.py) | Servidor HTTP local (`127.0.0.1:8795`) que orquestra gravação, fila e interface | `Abrir Stream-clipping App.bat` |
| [scripts/app_ui.html](scripts/app_ui.html) | Interface web dark-mode do mini app | Servida pelo `stream_app.py` |
| [Abrir Stream-clipping App.bat](Abrir%20Stream-clipping%20App.bat) | Abre o mini app em janela desktop (Edge `--app`) | Duplo clique |
| [1_GRAVAR_LIVES.bat](1_GRAVAR_LIVES.bat) | Atalho para rodar apenas o gravador no terminal | Duplo clique |
| [2_PREPARAR_LOTE.bat](2_PREPARAR_LOTE.bat) | Atalho para preparar todos os canais pendentes | Duplo clique |
| [3_FINALIZAR_LOTE.bat](3_FINALIZAR_LOTE.bat) | Atalho para finalizar lotes que já têm `decisao_lote.json` | Duplo clique |
| `scripts/referencia_original/` | Cópias de origem (`transcricao_whisper_v4.py`, `modo_A_gerar_xml.py` e `.bat`s) | Arquivo histórico de referência |

---

## 2. Fluxo de Dados Entre os Scripts

```text
[canais.json / URLs Twitch & YouTube]
          │
          ▼
   gravar_lives.py  (ou botão Gravar no stream_app.py)
   ├─ Grava em 01_gravando/<canal>_YYYY-MM-DD_HH-MM-SS.mkv (10 em 10 min)
   └─ Move blocos fechados ──► 02_para_processar/ + registro_gravacoes.jsonl
          │
          ▼ (disparo manual quando quiser processar uma leva)
   preparar_lote.py
   ├─ Isola blocos prontos ──► 03_em_processamento/<lote_id>/
   ├─ Transcreve com faster-whisper (CUDA) ──► transcricoes/*.srt e *.json
   ├─ Extrai contact sheets 4x5 com timecode ──► frames/*_grid_*.jpg
   └─ Junta estado_live.md + _ultimo_contexto.json ──► _pacote_ia.md e _pacote_ia.json
          │
          ▼
   [Análise da IA no chat]
   └─ Lê _pacote_ia.md + frames/*.jpg ──► grava decisao_lote.json
          │
          ▼
   finalizar_lote.py
   ├─ Move aprovados (+ .srt) ──► 04_para_edicao/<lote_id>/
   ├─ Move descartados        ──► 06_arquivo_descartados/<lote_id>/
   ├─ Chama premiere_xml.py   ──► 04_para_edicao/<lote_id>/<canal>_<lote_id>.xml
   ├─ Gera resumo_lote.md e atualiza estado_live.md + _ultimo_contexto.json
   └─ Arquiva metadados em _historico_lotes/<lote_id>/ e limpa frames/
          │
          ▼ (opcional)
   cortar_marcadores.py
   └─ Lê decisao_lote.json arquivado ──► exporta MP4s em 05_cortes_extraidos/<lote_id>/
```

---

## 3. Detalhamento de Cada Script

### `scripts/config.py`
- **Função:** centraliza caminhos (`PROJECT_ROOT`, `SCRIPTS_DIR`, `CANAIS_DIR`, `CANAIS_CONFIG_FILE`), binários (`FFMPEG_BIN`, `FFPROBE_BIN`), subpastas padrão (`CHANNEL_SUBDIRS`), parâmetros do Whisper (`large-v3`, `cuda`, `int8_float16`, VAD e regex anti-alucinação), parâmetros de grade (`FRAME_INTERVAL_SECONDS = 15`, `4x5`, `320x180`), `OVERLAP_CONTEXT_SECONDS = 90` e cores dos marcadores (`MARKER_TYPES`).
- **Helpers principais:** `sanitize_channel_name()`, `ensure_channel_structure()`, `load_channels_config()`, `format_hms()`, `format_mmss()`, `format_srt_time()`, `list_channel_names()`.
- **Cuidados:** se mudar o modelo padrão do Whisper, o intervalo de frames ou as cores do Premiere, basta alterar neste único arquivo.

---

### `scripts/gravar_lives.py`
- **Função:** captura lives da Twitch e do YouTube (inclusive vários canais em paralelo usando uma thread `ChannelRecorder` por canal) e segmenta continuamente via FFmpeg em Matroska (`.mkv`).
- **Entradas:** `canais.json` (todos com `"ativo": true`) ou URLs via CLI.
- **Saídas:** blocos `.mkv` em `01_gravando/` enquanto gravam; assim que o FFmpeg registra o fechamento no `_segmentos_<sessao>.csv`, o bloco é movido atomicamente para `02_para_processar/` e registrado em `registro_gravacoes.jsonl`.
- **Flags CLI:**
  - `--url URL`: URL da live ou `nome=URL` (pode repetir para vários canais).
  - `--nome NOME`: nome do canal quando houver só um `--url`.
  - `--minutos N`: tamanho do bloco em minutos (padrão: `10`).
  - `--qualidade QUAL`: qualidade passada ao Streamlink (padrão: `best`).
  - `--motor {auto,streamlink,yt-dlp}`: motor de captura (padrão: `auto`, que prioriza `streamlink` com `--twitch-disable-ads` e faz fallback para `yt-dlp`, ou usa `yt-dlp` primeiro no YouTube se houver `cookies`).
  - `--cookies CAMINHO`: arquivo Netscape `cookies.txt` (útil para YouTube).
  - `--reconectar SEG`: espera entre tentativas caso a live caia (padrão: `30`).
- **Dependências:** `streamlink` e/ou `yt-dlp`, `ffmpeg`, `ffprobe`.
- **Cuidados:** sempre pare com `Ctrl+C` (ou pelo botão **Parar** no App) para que o capturador envie EOF ao FFmpeg e feche o trailer do `.mkv` atual sem corromper o último bloco.

---

### `scripts/preparar_lote.py`
- **Função:** congela uma rodada de vídeos de `02_para_processar/` em `03_em_processamento/<lote_id>/` (evitando mistura com novas gravações), roda `faster-whisper` (ou reaproveita automaticamente o cache de `02_para_processar/_pre_transcricoes/`) e extrai contact sheets visuais com timecode queimado.
- **Entradas:** arquivos em `02_para_processar/` modificados há pelo menos 3 segundos, cache opcional em `02_para_processar/_pre_transcricoes/`, mais `estado_live.md` e `_ultimo_contexto.json`.
- **Saídas:**
  - `03_em_processamento/<lote_id>/transcricoes/<stem>.srt` e `.json`
  - `03_em_processamento/<lote_id>/frames/<stem>_grid_%02d.jpg` (grade 4x5 com timecode `HH:MM:SS.mmm`)
  - `03_em_processamento/<lote_id>/_pacote_ia.md` e `_pacote_ia.json`
- **Flags CLI:**
  - `--canal NOME` / `--todos`
  - `--modelo NOME`: modelo `faster-whisper` (padrão: `large-v3`)
  - `--idioma COD`: idioma ou `auto` (padrão: `pt`)
  - `--intervalo-frames SEG`: intervalo entre thumbs (padrão: `15`)
  - `--sem-whisper`: pula transcrição (útil para lives sem fala ou testes rápidos)
  - `--sem-frames`: pula geração de grades
  - `--pre-transcrever VIDEO [VIDEO ...]`: pré-transcreve blocos ainda em `02_para_processar/` salvando `<stem>.json` e `<stem>.srt` em `_pre_transcricoes/` sem movê-los para um lote (usado pelo modo automático do App).
- **Dependências:** `faster-whisper` (CUDA com fallback automático para CPU `int8`), `ffmpeg` (com filtro `drawtext` usando `C:\Windows\Fonts\arial.ttf`) e `premiere_xml.probe_video`.

---

### `scripts/finalizar_lote.py`
- **Função:** executa a decisão editorial gravada em `decisao_lote.json`, preservando obrigatoriamente marcadores manuais feitos ao vivo.
- **Ordem obrigatória (evita Media Offline no Premiere):**
  1. Valida que **todos** os vídeos do lote estão listados em `decisao_lote.json`.
  2. Injeta os pontos de `marcadores_manuais.json` daquele lote como marcadores pontuais (`tipo: "manual"`, cor azul) e garante que nenhum bloco com marcador manual seja descartado.
  3. Move aprovados (+ `.srt`) para `04_para_edicao/<lote_id>/` e descartados (+ `.srt`) para `06_arquivo_descartados/<lote_id>/`.
  4. Chama `premiere_xml.py` apontando para os caminhos definitivos em `04_para_edicao/<lote_id>/`.
  5. Grava `resumo_lote.md`, atualiza `estado_live.md` e salva os últimos 90s em `_ultimo_contexto.json`.
  6. Move `_pacote_ia.*`, `decisao_lote.json` e `transcricoes/` para `_historico_lotes/<lote_id>/`, apaga `frames/`, limpa os marcadores manuais já consumidos de `marcadores_manuais.json` e remove a pasta do lote de `03_em_processamento/`.
- **Flags CLI:** `--canal NOME`, `--lote LOTE_ID`, `--todos`, `--xml-por-bloco`.

---

### `scripts/premiere_xml.py`
- **Função:** lê propriedades de mídia via `ffprobe` (`probe_video`) e gera XML do Final Cut Pro (`xmeml version="4"`) no formato exato aceito pelo Adobe Premiere Pro.
- **Recursos principais:**
  - `normalize_frame_rate()`: converte taxas VFR de lives para taxas aceitas pelo Premiere (`23.976`, `24`, `25`, `29.97`, `30`, `50`, `59.94`, `60`).
  - `gerar_xml_sequencia()`: empilha todos os blocos aprovados na mesma timeline (`start` e `end` acumulativos), mantém faixas de áudio estéreo explodidas (`explodedTracks="true"`) e converte `in_seg`/`out_seg` relativos de cada bloco em marcadores na régua da sequência (`<in>`, `<out>` e `<pproColor>`).
  - `gerar_xml_individual()`: gera XML de um único bloco (usado quando `--xml-por-bloco` é acionado).

---

### `scripts/cortar_marcadores.py`
- **Função (opcional):** recorta arquivos `.mp4` individuais para cada marcador de um lote já finalizado.
- **Entradas:** `_historico_lotes/<lote_id>/decisao_lote.json` e vídeos em `04_para_edicao/<lote_id>/`.
- **Saídas:** `05_cortes_extraidos/<lote_id>/<indice>_<titulo>.mp4`.
- **Flags CLI:** `--canal NOME` (obrigatório), `--lote LOTE_ID` (obrigatório), `--folga SEG` (padrão `2.0`), `--copia` (corte rápido sem re-encode), `--encoder {auto,nvenc,x264}` (padrão `auto`: tenta `h264_nvenc` e faz fallback para `libx264`).

---

### `scripts/stream_app.py` e `scripts/app_ui.html`
- **Função:** servidor local `ThreadingHTTPServer` em `http://127.0.0.1:8795` + interface web dark-mode.
- **Arquitetura:**
  - `RecorderManager`: roda `ChannelRecorder` em threads dentro do servidor (permite ligar/desligar canal por canal ou todos os ativos de uma vez, e encerra todos com segurança no shutdown).
  - `JobManager`: roda `preparar_lote.py`, `finalizar_lote.py` e `cortar_marcadores.py` em **subprocessos únicos** (assim que termina, a VRAM da RTX 4060 Ti é 100% liberada) e impede rodar dois jobs pesados simultâneos (HTTP 409).
  - `AutoTranscribeWatcher`: quando `padroes.modo_transcricao == "automatico"`, monitora `02_para_processar/` a cada 5s e dispara `preparar_lote.py --pre-transcrever` em segundo plano a cada bloco fechado.
- **Endpoints da API:**
  - `GET /` — entrega `app_ui.html`
  - `GET /api/state` — estado geral, canais, contagens do funil, lotes, job atual e logs
  - `GET /api/channel/details?channel=<nome>` — `estado_live.md`, arquivos da fila (com status `pre_transcribed`) e `resumo_lote.md`
  - `POST /api/channels/save` | `POST /api/channels/delete` — gerencia `canais.json` (sem apagar vídeos)
  - `POST /api/settings` — salva `modo_transcricao` (`manual` ou `automatico`) e padrões em `canais.json`
  - `POST /api/record/start` | `POST /api/record/stop` — controla gravações
  - `POST /api/prepare` | `POST /api/finalize` | `POST /api/cut` — dispara jobs
  - `POST /api/channel/estado` — salva edição manual de `estado_live.md`
  - `POST /api/markers/add` | `POST /api/markers/delete` — marca ou remove pontos ao vivo em `marcadores_manuais.json` usando o segundo exato do bloco em gravação (atalho `M` na interface)
  - `POST /api/open-folder` — abre subpasta do projeto no Windows Explorer
  - `POST /api/logs/clear` | `POST /api/shutdown` — limpa console ou encerra o servidor com segurança
- **Flags CLI:** `--host` (padrão `127.0.0.1`), `--port` (padrão `8795`). Se a porta já estiver em uso, sai com código `0` para o `.bat` apenas reabrir a janela.

---

### Lançadores `.bat` e `scripts/referencia_original/`
- **`Abrir Stream-clipping App.bat`:** inicia `scripts\stream_app.py` minimizado via `pyw -3.11` (fallback `py -3.11`) e abre o Microsoft Edge em modo janela de app (`--app=http://127.0.0.1:8795 --window-size=1240,780`).
- **`1_GRAVAR_LIVES.bat`, `2_PREPARAR_LOTE.bat`, `3_FINALIZAR_LOTE.bat`:** atalhos diretos em terminal usando `py -3.11` e UTF-8 (`chcp 65001`) caso queira rodar etapas isoladas sem abrir o App.
- **`scripts/referencia_original/`:** cópias preservadas de `transcricao_whisper_v4.py`, `rodar_transcricao_whisper_v4.bat`, `modo_A_gerar_xml.py` e `modo_A_gerar_xml.bat`, mantidas apenas como referência do motor Whisper e do formato XML originalmente validados.
