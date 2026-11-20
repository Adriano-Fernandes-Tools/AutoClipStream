# AGENTS.md — Guia Completo e Autocontido do Projeto `Stream-clipping`

> **Para qualquer agente em um chat novo (sem histórico anterior):** leia este arquivo inteiro antes de agir. Ele contém todo o contexto, caminhos, regras editoriais, schemas e comandos necessários para operar o projeto do zero.

---

## 1. Visão Geral e Perfil do Usuário

- **O que este sistema faz:** grava lives da **Twitch** e do **YouTube** (inclusive vários canais ao mesmo tempo), divide continuamente em blocos `.mkv` de **10 em 10 minutos**, transcreve áudio com `faster-whisper` na GPU, extrai grades visuais de frames com timecode queimado, apoia a análise editorial da IA usando memória viva da live (`estado_live.md` + overlap de 90s), separa blocos aprovados de descartados e gera **1 único arquivo `.xml` por rodada** para importar no **Adobe Premiere Pro** com **marcadores In–Out coloridos**.
- **Quem usa:** Adriano Fernandes é **editor de vídeo, não programador**.
  - Sempre responda em **português simples e direto**, sem jargão técnico desnecessário.
  - Sempre entregue **links clicáveis** (`file:///...`) para os arquivos gerados (`.xml`, `resumo_lote.md`, `estado_live.md`).
- **Decisões técnicas já validadas (não rediscutir nem desfazer):**
  - O **Premiere Pro v27** do usuário aceita **`.mkv`** e **VFR** nativamente. Os blocos são gravados e entregues direto em `.mkv` (sem remux para `.mp4`).
  - O formato de importação validado no Premiere é **Final Cut Pro XML (`xmeml version="4"`)** com faixas de áudio estéreo explodidas (`explodedTracks="true"`), gerado por `scripts/premiere_xml.py` (normalizando VFR para a taxa padrão mais próxima aceita pelo Premiere).
  - Sidecar **`.xmp` NÃO funciona** no Premiere para esse fluxo (já testado e descartado). Não gere `.xmp`.
  - O padrão é **1 sequência `.xml` única por rodada** empilhando todos os blocos aprovados em ordem cronológica (com opção `--xml-por-bloco` caso pedido).

---

## 2. Ambiente, Caminhos e Regras de Execução

- **Pasta raiz do projeto:** `E:\Antigravity\Stream-clipping`
- **Python obrigatório:** `py -3.11` (`C:\Users\Adriano Fernandes\AppData\Local\Python\pythoncore-3.11-64\python.exe`)
  - Pacotes instalados nesse Python: `faster-whisper`, `streamlink`, `yt-dlp`.
  - GPU da máquina: NVIDIA GeForce RTX 4060 Ti (8 GB VRAM).
- **FFmpeg / FFprobe:** `C:\ffmpeg\bin\ffmpeg.exe` e `C:\ffmpeg\bin\ffprobe.exe` (mapeados em `scripts/config.py`).
- **Mini App Desktop:** [Abrir Stream-clipping App.bat](file:///e:/Antigravity/Stream-clipping/Abrir%20Stream-clipping%20App.bat) sobe `scripts/stream_app.py` na porta `8795` (`http://127.0.0.1:8795`) no Microsoft Edge `--app`.
- **Nota operacional no sandbox do Windows:**
  - No sandbox padrão do terminal, `py.exe` costuma falhar com `ResourceUnavailable` (`The file cannot be accessed by the system`). Tente primeiro normalmente e, se falhar com esse erro, repita o comando `py -3.11 ...` com `BypassSandbox: true` (mantendo o mesmo `toolSummary`).
- **Instalação do zero (caso mude de máquina):**
  ```bat
  py -3.11 -m pip install -U streamlink yt-dlp faster-whisper
  ```

---

## 3. Mapa Completo de Pastas e Arquivos

```text
E:\Antigravity\Stream-clipping\
├── Abrir Stream-clipping App.bat   # Abre a interface desktop (porta 8795)
├── 1_GRAVAR_LIVES.bat              # Inicia gravação via terminal dos canais ativos
├── 2_PREPARAR_LOTE.bat             # Prepara fila pendente (Whisper + frames) de todos os canais
├── 3_FINALIZAR_LOTE.bat            # Finaliza lotes que já têm decisao_lote.json
├── canais.json                     # Cadastro e configurações por canal (Twitch / YouTube)
├── AGENTS.md                       # Este guia completo para qualquer agente/chat
├── GEMINI.md                       # Regras rápidas de workspace
├── README.md                       # Manual do usuário
├── scripts.md                      # Documentação detalhada de cada script
├── Marketing\                      # Acervo estratégico GTA 6 (@helixgamess) — CONSULTA OBRIGATÓRIA
│   ├── README.md                   # Índice e regra central (por que clipe solto perde)
│   ├── formatos-e-estruturas-gta.md# 6 formatos vencedores (F1–F6) e esqueletos em segundos
│   ├── gatilhos-e-hooks-gta.md     # Ranking de gatilhos mentais e escolha do Ponto IN (0–3,5s)
│   ├── banco-headlines-e-titulos-gta.md # 3 estilos, 64 moldes de Headline e 15 moldes de título
│   └── retencao-edicao-e-checklist-gta.md # Padrão técnico 9:16, coordenadas e checklist
├── exemplos_layout_9x16\           # 16 telas aprovadas (1080x1920) dos 4 streamers atuais
├── scripts\
│   ├── config.py                   # Caminhos, constantes, cores de marcadores e helpers
│   ├── gravar_lives.py             # Gravador multi-canal contínuo em .mkv
│   ├── preparar_lote.py            # Isola lote, roda faster-whisper e extrai grades 4x5
│   ├── finalizar_lote.py           # Move vídeos, gera XML do Premiere e atualiza estado_live.md
│   ├── premiere_xml.py             # Gerador xmeml v4 validado com marcadores In–Out
│   ├── cortar_marcadores.py        # Utilitário opcional: exporta cortes .mp4 individuais
│   ├── stream_app.py               # Servidor HTTP local da interface (porta 8795)
│   ├── app_ui.html                 # Interface web dark-mode do mini app
│   └── referencia_original\        # Cópias originais intocadas (Whisper v4 e Modo A XML)
└── canais\
    └── <nome_do_canal>\
        ├── 01_gravando\            # Bloco .mkv sendo gravado agora — NÃO MEXER
        ├── 02_para_processar\      # Blocos .mkv fechados aguardando próxima leva
        ├── 03_em_processamento\    # Lote congelado: <lote_id>\ (+ transcricoes\, frames\, _pacote_ia.*)
        ├── 04_para_edicao\         # Blocos aprovados + .srt + .xml único + resumo_lote.md
        ├── 05_cortes_extraidos\    # Clipes .mp4 individuais (quando cortar_marcadores.py for rodado)
        ├── 06_arquivo_descartados\ # Blocos sem cortes relevantes (arquivo morto)
        ├── _historico_lotes\       # Auditoria dos lotes finalizados (_pacote_ia.*, decisao_lote.json, jsons)
        ├── estado_live.md          # Diário vivo da live (lido e atualizado a cada rodada)
        ├── _ultimo_contexto.json   # Últimos ~90s de transcrição do lote anterior
        └── registro_gravacoes.jsonl
```

---

## 4. Protocolos Passo a Passo (O que fazer em cada pedido)

### Protocolo A — Quando o usuário pedir: *"processa o que temos"*, *"analisa o canal X"* ou *"gera os marcadores"*

Siga exatamente nesta ordem:

#### Passo 1: Conferir fila e preparar lote (se necessário)
1. Liste os canais em `E:\Antigravity\Stream-clipping\canais\`.
2. Verifique se já existe algum lote aberto em `03_em_processamento\<lote_id>\` sem `decisao_lote.json` (por exemplo, se o usuário clicou em "Preparar Lote" no App antes de chamar o chat).
3. Se houver arquivos `.mkv` (ou `.mp4`/`.ts`/`.mov`) em `02_para_processar\`, rode:
   ```powershell
   py -3.11 "E:\Antigravity\Stream-clipping\scripts\preparar_lote.py" --canal <nome_do_canal>
   ```
   *(Ou use `--todos` se o usuário pedir todos os canais pendentes. Se o usuário estiver com o modo **Automático** ligado no App, os blocos em `02_para_processar\_pre_transcricoes\` serão reaproveitados na hora sem precisar transcrever de novo.)*

#### Passo 2: Ler contexto, transcrições e frames visuais
Para cada lote pendente em `03_em_processamento\<lote_id>\` (que ainda não tenha `decisao_lote.json`):
1. Leia `<lote_dir>/_pacote_ia.md` **por inteiro** usando `read_file`. Ele já reúne:
   - `estado_live.md` (o que o streamer vem fazendo na live).
   - Os últimos ~90s do lote anterior (`_ultimo_contexto.json`), para capturar falas ou situações que continuaram na virada de bloco.
   - As transcrições bloco a bloco com `[MM:SS - MM:SS] (start -> end)`.
2. Inspecione **todas** as imagens de grade listadas em `<lote_dir>/frames/*.jpg` usando `read_file`.
   - Cada grade tem quadrinhos `4x5` (1 frame a cada 15s por padrão) com o timecode exato `HH:MM:SS.mmm` queimado no canto superior esquerdo.
   - Isso permite achar momentos puramente visuais (gameplay, reações, bugs, tela mudando) mesmo quando o streamer não falou nada.

#### Passo 3: Aplicar critérios editoriais e escrever `decisao_lote.json`
O objetivo do Adriano é ter **apenas o In e Out onde há algo realmente interessante** — **não marque conversa morna, espera de fila, menu ou tempo morto**.

**Consulta Obrigatória ao Acervo de Marketing (`Marketing/`) — LEIA ANTES DE DECIDIR CORTES, HEADLINES OU VÍDEOS:**
- Antes de selecionar os cortes finais, definir `in_seg`/`out_seg`, escrever headlines/títulos ou gerar vídeos verticais 9:16, **sempre consulte os documentos de `E:\Antigravity\Stream-clipping\Marketing\`**, em especial:
  - [Marketing/formatos-e-estruturas-gta.md](Marketing/formatos-e-estruturas-gta.md) (para encaixar o momento em um dos **6 formatos vencedores `F1`–`F6`**);
  - [Marketing/gatilhos-e-hooks-gta.md](Marketing/gatilhos-e-hooks-gta.md) (para identificar os gatilhos mais fortes — polêmica, contraintuitivo, alerta, pergunta, curiosidade, relato — e ajustar o `in_seg` exatamente na frase-gancho dos primeiros `0–3,5s` ou recuar `2–4s` em reação pura);
  - [Marketing/banco-headlines-e-titulos-gta.md](Marketing/banco-headlines-e-titulos-gta.md) (para escolher entre os estilos **01 Solta**, **02 Tarja Vermelha** ou **03 Caixa Branca** usando os 64 moldes validados para GTA 6 e regras de título);
  - [Marketing/retencao-edicao-e-checklist-gta.md](Marketing/retencao-edicao-e-checklist-gta.md) (para revisar o checklist de 7 pontos e o padrão visual `@helixgamess`).
- Sempre procure usar o melhor formato e gatilho possível que se aplique ao trecho da live e já registre no `"titulo"` / `"comentario"` do marcador o **formato (`F1`–`F6`)**, o **estilo de Headline**, a **sugestão de Headline em MAIÚSCULA** e eventuais trocas visuais (`Zoom "olha isso"`, `Centro Blur` ou `Tela Cheia do Game`).

**O que vale marcar:**
- **Marcadores manuais do editor (`marcadores_manuais` em `_pacote_ia.md` / `marcadores_manuais.json`):** têm **PRIORIDADE MÁXIMA**. Quando houver um marcador manual no bloco (feito pelo botão **★ Marcar ponto / `M`** durante a live), você deve obrigatoriamente aprovar o bloco e criar um marcador editorial In–Out ajustado ao redor daquele momento (além de o `finalizar_lote.py` já preservar o marcador pontual azul original no XML).
- História completa, opinião forte ou dica valiosa com começo, meio e fim naturais (`corte_forte`).
- Virada intensa, susto, risada genuína, clutch, rage ou conquista difícil (`climax`).
- Trecho curioso/bom que talvez precise de ajuste fino na edição (`possivel_corte`).
- Acontecimento marcante na tela sem fala (`momento_visual`).
- Assunto/jogada boa que começou no fim de um bloco (ou no overlap do lote anterior) e terminou no começo do bloco seguinte (`continuacao`).

**Tabela oficial de tipos e cores no Premiere (`scripts/config.py`):**

| Chave `tipo` | Nome padrão | Cor no Premiere | `pproColor` |
| :--- | :--- | :--- | :--- |
| `corte_forte` | Corte forte | Verde | padrão (`None`) |
| `climax` | Clímax / Imperdível | Vermelho | `4281740498` |
| `possivel_corte` | Possível corte | Amarelo | `4281049552` |
| `momento_visual` | Destaque visual | Azul | `4294741314` |
| `manual` | Marcador manual | Azul | `4294741314` |
| `continuacao` | Continua / Emenda | Laranja | `4280578025` |

**Schema obrigatório de `<lote_dir>/decisao_lote.json`:**
```json
{
  "resumo_rodada": "Resumo curto e objetivo do que aconteceu nesta rodada.",
  "estado_atual_live": "O que o streamer está fazendo agora ao final deste lote.",
  "arcos_em_aberto": [
    "Missão, desafio ou assunto em andamento"
  ],
  "blocos": [
    {
      "arquivo": "canal_2026-11-19_12-00-00.mkv",
      "status": "aprovado",
      "motivo_descarte": "",
      "marcadores": [
        {
          "in_seg": 124.5,
          "out_seg": 198.0,
          "titulo": "Título curto e claro do corte",
          "comentario": "Por que vale cortar / dica rápida para o editor",
          "tipo": "corte_forte"
        }
      ]
    },
    {
      "arquivo": "canal_2026-11-19_12-10-00.mkv",
      "status": "descartado",
      "motivo_descarte": "Só tempo de fila e conversa sem gancho.",
      "marcadores": []
    }
  ]
}
```

**Regras de validação do `decisao_lote.json`:**
1. **Todos** os arquivos de vídeo do lote devem aparecer em `"blocos"` na **mesma ordem** de `_pacote_ia.json` (se faltar algum arquivo, `finalizar_lote.py` aborta por segurança).
2. `0 <= in_seg < out_seg <= duracao_seg` do respectivo bloco (nunca inventar segundos maiores que `duracao_seg`).
3. Dê um respiro natural de ~1–3s antes/depois da fala quando ajustar `in_seg` e `out_seg` (sem cortar no meio da palavra).
4. Se um corte cruzar a divisa entre dois blocos do mesmo lote, marque os dois trechos com `"tipo": "continuacao"` e mantenha ambos como `"status": "aprovado"`. Se continuar do lote anterior (via `_ultimo_contexto.json`), sinalize no título/comentário e use `"continuacao"`.
5. Sempre preencha `"resumo_rodada"`, `"estado_atual_live"` e `"arcos_em_aberto"` para alimentar o `estado_live.md` da próxima rodada.

#### Passo 4: Finalizar e entregar ao usuário
1. Rode:
   ```powershell
   py -3.11 "E:\Antigravity\Stream-clipping\scripts\finalizar_lote.py" --canal <nome_do_canal> --lote <lote_id>
   ```
2. Confirme que os arquivos saíram de `03_em_processamento/` e foram para `04_para_edicao/<lote_id>/` (aprovados + `.srt` + `resumo_lote.md`; o `.xml` do Premiere agora é opcional via `--gerar-xml` e fica **desativado por padrão** — só gere quando o usuário pedir) e `06_arquivo_descartados/<lote_id>/` (descartados). Se algum bloco descartado tiver trechos em `"cortes_descartados"` (`in_seg`, `out_seg`, `titulo`), o script recorta esses trechos em stream copy (`-c copy`) para `06_arquivo_descartados/<lote_id>/cortes_para_conferencia/` para conferência rápida do usuário.
3. Responda ao usuário com links clicáveis `file:///...` para o `.xml` pronto (`File > Import` no Premiere), `resumo_lote.md` e `estado_live.md`, listando os principais cortes encontrados e quantos blocos foram descartados.

---

### Protocolo B — Cadastrar ou iniciar gravação de 1 ou vários canais

- **Via arquivo `canais.json`:** adicione um objeto em `"canais"`:
  ```json
  {
    "nome": "nome_do_canal",
    "url": "https://www.twitch.tv/nome_do_canal",
    "ativo": true,
    "motor": "auto",
    "duracao_bloco_minutos": 10,
    "qualidade": "best",
    "cookies": ""
  }
  ```
- **Via App visual:** o usuário pode cadastrar, ativar e clicar em **Gravar** direto no [Abrir Stream-clipping App.bat](file:///e:/Antigravity/Stream-clipping/Abrir%20Stream-clipping%20App.bat).
- **Via CLI (múltiplos canais simultâneos):**
  ```powershell
  py -3.11 "E:\Antigravity\Stream-clipping\scripts\gravar_lives.py" --url canal1=https://www.twitch.tv/CANAL1 --url canal2=https://www.youtube.com/@CANAL2/live
  ```

---

### Protocolo C — Exportar cortes físicos em `.mp4` (opcional)

Se o usuário pedir os vídeos já recortados além do XML do Premiere:
```powershell
py -3.11 "E:\Antigravity\Stream-clipping\scripts\cortar_marcadores.py" --canal <nome_do_canal> --lote <lote_id> --folga 2.0
```
Os clipes saem em `canais/<canal>/05_cortes_extraidos/<lote_id>/` (usa `h264_nvenc` com fallback automático para `libx264`; aceita `--copia` para corte instantâneo em keyframe).

---

## 5. Padrão de Edição Vertical 9:16 (`@helixgamess` — GTA 6) e Acervo de Marketing

Além do XML horizontal para o Premiere, está em definição o sistema/padrão de **cortes verticais `1080x1920` (9:16, 60 fps)** para GTA 6 (`@helixgamess`), cujas referências visuais aprovadas ficam em `exemplos_layout_9x16\` (16 telas para `Alanzoka`, `Bistecone`, `DaviJones` e `MrFall`) e cujas regras de roteiro/gancho ficam em `Marketing\`.

### Especificações Visuais e Editoriais Fechadas
- **Regra obrigatória de pré-produção:** antes de gerar qualquer vídeo, headline ou sugestão de corte, **sempre consulte `Marketing\`** (`README.md`, `formatos-e-estruturas-gta.md`, `gatilhos-e-hooks-gta.md`, `banco-headlines-e-titulos-gta.md` e `retencao-edicao-e-checklist-gta.md`) para escolher o melhor formato (`F1`–`F6`), gatilho mental e molde de Headline.
- **Duração e Silêncios:** mínimo **`10s`**, máximo **`90s` (`1:30`)**, foco no menor tempo possível (`20s–60s`). **NÃO cortar silêncios automaticamente** (evita cortar suspense ou momentos importantes do jogo).
- **4 Modos Visuais (corte seco, sem linha divisória):**
  1. **Split-Screen `38%` topo / `62%` base (`1080x720` streamer / `1080x1200` gameplay):** padrão principal (`_01_split_headline_solta.jpg`, `_02_split_headline_vermelha.jpg`, `_03_split_headline_branca.jpg`).
  2. **Zoom na Câmera do Streamer:** aplicar **apenas** quando o streamer falar *"olha isso"*, *"repara nisso"* ou equivalente.
  3. **Troca para Tela Inteira do Game (`1080x1920`):** aplicar **apenas** se houver algo muito engraçado acontecendo no jogo, revezando em corte seco com a reação do streamer. *(Obs.: o antigo layout de game estático com bordas superior/inferior embaçadas foi descartado.)*
  4. **Modo Streamer Conversando (`_04_streamer_centro_blur.jpg`):** quando o streamer estiver apenas conversando sem ação importante no jogo — fundo 9:16 do game com blur (`gblur`) e câmera centralizada (`1080x720` em `y=580..1300`).
- **Tipografia, Headline, Legenda, Marca d'Água e Áudio:**
  - **Fonte oficial:** `Poppins Black` (`C:\Windows\Fonts\Poppins-Black.ttf`), **100% EM MAIÚSCULA**.
  - **Headline (3 estilos aprovados):** fixa sempre nos **primeiros `5s` a `10s`** (01 Solta amarelo/branco com contorno preto; 02 Tarja Vermelha com borda preta; 03 Caixa Branca arredondada com texto preto).
  - **Legenda dinâmica:** sempre **1 palavra por vez em estilo POP** (`y ≈ 1470–1500`).
  - **Marca d'água:** `@helixgamess` centralizada com **30% de opacidade**.
  - **Áudio:** normalizado no padrão YouTube (**`-14 LUFS`**).

### Coordenadas Calibradas dos 4 Canais (`1920x1080` fonte)

| Canal | Webcam Normal (`x, y, w, h`) | Webcam Zoom *"Olha isso"* (`x, y, w, h`) | Região Principal do Game (`x, y, w, h`) |
| :--- | :--- | :--- | :--- |
| **Alanzoka** | `1485, 486, 421, 268` | `1520, 495, 340, 225` | `400, 120, 1060, 960` |
| **Bistecone** | `1450, 162, 468, 302` | `1510, 195, 340, 235` | `320, 80, 1100, 1000` |
| **DaviJones** | `1505, 106, 405, 228` | `1555, 115, 305, 195` | `320, 140, 1150, 940` |
| **MrFall** | `1280, 605, 635, 423` | `1360, 620, 555, 370` | `220, 100, 1200, 980` |

---

## 6. Regras Invioláveis e Solução de Problemas

### Regras invioláveis
1. **Nunca mexer em `01_gravando/`** enquanto o gravador estiver ativo (há um arquivo `.mkv` sendo escrito em tempo real lá dentro).
2. **Nunca apagar vídeos brutos** (nem os de `06_arquivo_descartados/`) sem o usuário pedir explicitamente.
3. **Nunca gerar XML antes de mover os vídeos:** o `<pathurl>` do XML aponta para o caminho absoluto final. `finalizar_lote.py` sempre move primeiro para `04_para_edicao/` e gera o XML depois.
4. **Não reutilizar `_pacote_ia.md` sem ler os frames:** os frames contêm contexto visual que o áudio não mostra.
5. **Nunca gerar cortes finais, headlines ou vídeos 9:16 sem consultar `Marketing\`:** siga sempre os formatos, hooks e moldes validados no acervo antes de escrever a cópia final.

### Solução rápida de problemas comuns
- **Live da Twitch caindo ou com tela de anúncio:** o `gravar_lives.py` já passa `--twitch-disable-ads` no `streamlink` e reconecta automaticamente a cada 30s (ou cai para `yt-dlp` no modo `auto`).
- **YouTube bloqueando ou pedindo login/idade (`429 / Sign in to confirm`):** tente primeiro trocar o **Motor** do canal para `yt-dlp` (sem cookies funciona em vários casos) ou informe `browser:firefox` no campo Cookies; se ainda exigir, exporte `cookies.txt` (formato Netscape) e informe o caminho em `"cookies"` no `canais.json` (ou `--cookies`). Não tente ler cookies direto do Edge aberto no Windows (trava de arquivo SQLite).
- **Falta de VRAM / erro CUDA no Whisper:** `preparar_lote.py` já faz fallback automático para CPU (`int8`). Se o Premiere estiver consumindo muita VRAM, feche projetos pesados ou rode `--modelo medium` ou `--modelo small`.
- **Arquivo `.mkv` preso em `01_gravando/` após queda de energia/PC desligado:** confirme que `gravar_lives.py` e o App não estão rodando; verifique se o `.mkv` lê no `ffprobe`; estando íntegro, mova manualmente para `02_para_processar/`.
- **App não abre ou porta `8795` já ocupada:** se já houver uma instância rodando, `stream_app.py` sai com código `0` e o `.bat` abre a janela normalmente. Se travou, rode `POST /api/shutdown` ou encerre o processo `pyw`/`pythonw`.
