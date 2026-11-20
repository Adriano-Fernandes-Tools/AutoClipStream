# Stream-clipping — Gravação Contínua e Marcadores para Premiere Pro

Fluxo feito para **capturar lives da Twitch e do YouTube**, dividir automaticamente em blocos de **10 minutos em `.mkv`** e gerar **1 arquivo `.xml` por rodada** com marcadores **In–Out** já posicionados para o **Adobe Premiere Pro**.

> **Novo — Painel Visual (jeito mais fácil de usar):** dê dois cliques em [Abrir Stream-clipping App.bat](Abrir%20Stream-clipping%20App.bat) para cadastrar canais, gravar várias lives juntas, preparar lotes, editar o `estado_live.md` e gerar o `.xml` por botões.
>
> **Documentação auxiliar:** [AGENTS.md](AGENTS.md) (guia completo para rodar tudo no chat do zero) | [scripts.md](scripts.md) (resumo técnico de todos os scripts) | [Marketing/README.md](Marketing/README.md) (acervo estratégico de formatos, gatilhos, hooks e headlines para GTA 6).

---

## 1. Como cadastrar um ou mais canais

Abra o arquivo [canais.json](canais.json) no Bloco de Notas (ou aqui no editor), troque o endereço de exemplo pela live real e mude `"ativo": false` para `"ativo": true`.

Você pode deixar **vários canais ativos ao mesmo tempo** (por exemplo, 1 Twitch + 1 YouTube): cada um grava em sua própria pasta sem misturar arquivos.

> **Se quiser gravar uma live avulsa sem editar o JSON**, rode no terminal dentro desta pasta:
> ```bat
> py -3.11 scripts\gravar_lives.py --url https://www.twitch.tv/CANAL --nome canal
> ```
> Ou dois canais ao mesmo tempo:
> ```bat
> py -3.11 scripts\gravar_lives.py --url canal1=https://www.twitch.tv/CANAL1 --url canal2=https://www.youtube.com/@CANAL2/live
> ```

---

## 2. Passo a passo do dia a dia

1. **Comece a gravar a live:** dê dois cliques em [Abrir Stream-clipping App.bat](Abrir%20Stream-clipping%20App.bat) e clique em **▶ Gravar ativos** (ou use [1_GRAVAR_LIVES.bat](1_GRAVAR_LIVES.bat) direto no terminal).
   - Ele grava em blocos `.mkv` de 10 em 10 minutos dentro de `01_gravando/` e, assim que um bloco fecha, move para `02_para_processar/`.
   - No menu **Transcrição (Whisper)** da barra lateral do App, você escolhe entre **Manual (só quando eu pedir)** (deixa a placa de vídeo livre durante a gravação) ou **Automático (a cada bloco de 10m)** (pré-transcreve cada bloco em `02_para_processar/_pre_transcricoes/` assim que fecha, sem atrapalhar o XML único da rodada).
   - Se a live cair, ele reconecta sozinho. Para parar com segurança sem corromper o último bloco, clique em **■ Parar** no App (ou aperte `Ctrl+C` se estiver usando o `.bat` no terminal).
2. **Quando quiser processar o que já fechou:**
   - **Pelo chat (jeito mais rápido):** peça aqui *"processa o que temos"* ou *"analisa as lives pendentes"*. A IA roda o preparo, lê o áudio transcrito + as grades visuais, decide o que presta, organiza as pastas e entrega o `.xml`.
   - **Ou manualmente:** dê dois cliques em [2_PREPARAR_LOTE.bat](2_PREPARAR_LOTE.bat) (roda Whisper + gera grades de frames), peça para a IA analisar o `_pacote_ia.md` dentro de `03_em_processamento/` e depois dê dois cliques em [3_FINALIZAR_LOTE.bat](3_FINALIZAR_LOTE.bat).
3. **Abra no Premiere Pro:**
   - No Premiere, vá em **File > Import** e escolha o arquivo `.xml` dentro de `canais/<canal>/04_para_edicao/<lote_id>/`.
   - O Premiere abre **uma sequência única da rodada** com todos os blocos aprovados em ordem e com as barrinhas de marcador (In–Out) na régua da timeline.

---

## 3. Mapa das pastas de cada canal

Cada canal cria sua estrutura organizada dentro de `canais/<nome_do_canal>/`:

| Pasta / Arquivo | O que fica aqui |
| :--- | :--- |
| `01_gravando/` | Bloco `.mkv` sendo gravado agora. **Não mexer.** |
| `02_para_processar/` | Blocos já fechados esperando a próxima rodada de análise. |
| `03_em_processamento/` | Lote congelado em análise (novas gravações não entram aqui no meio do processo). |
| `04_para_edicao/` | Blocos aprovados + `.xml` da rodada + `.srt` + `resumo_lote.md`. **É daqui que vai pro Premiere.** |
| `05_cortes_extraidos/` | Clipes `.mp4` individuais cortados (só se você usar o `cortar_marcadores.py`). |
| `06_arquivo_descartados/` | Blocos processados onde não havia nada de interessante (arquivo morto). |
| `_historico_lotes/` | Cópia das transcrições e decisões de cada lote para consulta futura. |
| `estado_live.md` | Diário vivo do que está acontecendo na live (missões, contexto, histórico por rodada). |
| `marcadores_manuais.json` | Pontos marcados ao vivo pelo botão **★ Marcar ponto (M)** no App enquanto assiste. |

---

## 4. Legenda das cores dos marcadores no Premiere

| Tipo | Cor no Premiere | Quando é usado |
| :--- | :--- | :--- |
| `corte_forte` | Verde | Corte redondo pronto para virar clipe/Reels/Shorts |
| `climax` | Vermelho | Momento imperdível, virada forte, reação explosiva |
| `possivel_corte` | Amarelo | Trecho bom que vale conferir ou encurtar na edição |
| `momento_visual` | Azul | Acontecimento importante na tela com pouca ou nenhuma fala |
| `manual` | Azul | Ponto marcado por você ao vivo no App (**★ Marcar ponto** ou tecla `M`) |
| `continuacao` | Laranja | Assunto/jogada que começa no fim de um bloco e termina no começo do próximo |

---

## 5. Opcional: exportar os cortes já recortados em `.mp4`

Se algum dia quiser extrair os trechos marcados em arquivos `.mp4` separados (com 2 segundos de folga antes e depois), rode no terminal:

```bat
py -3.11 scripts\cortar_marcadores.py --canal NOME_DO_CANAL --lote lote_YYYYMMDD_HHMMSS
```

Opções úteis:
- `--folga 3.0`: muda a folga antes/depois (padrão: `2.0` segundos).
- `--copia`: corta instantaneamente sem re-encodar (limitado aos keyframes).

---

## 6. Acervo de Marketing, Headlines e Padrão 9:16 (`@helixgamess`)

Para transformar os trechos das lives de GTA 6 em cortes verticais (`1080x1920`) com retenção alta no TikTok, Reels e YouTube Shorts, este projeto conta com um acervo estratégico dedicado e telas-modelo aprovadas:

- **Acervo em `Marketing/` (consulta obrigatória antes de definir cortes, headlines ou gerar vídeos):**
  - [Marketing/README.md](Marketing/README.md) — Guia rápido e por que evitar clipes soltos sem contexto no segundo zero.
  - [Marketing/formatos-e-estruturas-gta.md](Marketing/formatos-e-estruturas-gta.md) — Os 6 formatos vencedores (`F1`–`F6`) com estrutura segundo a segundo.
  - [Marketing/gatilhos-e-hooks-gta.md](Marketing/gatilhos-e-hooks-gta.md) — Gatilhos mentais validados e escolha cirúrgica do `Ponto IN` (`0–3,5s`).
  - [Marketing/banco-headlines-e-titulos-gta.md](Marketing/banco-headlines-e-titulos-gta.md) — 3 estilos visuais de Headline (`Poppins Black` MAIÚSCULA, 5–10s), 64 moldes prontos e regras de título.
  - [Marketing/retencao-edicao-e-checklist-gta.md](Marketing/retencao-edicao-e-checklist-gta.md) — Manual técnico 9:16 (Split 38/62, Modo Centro Blur, Tela Cheia em momentos muito engraçados, Zoom *"olha isso"*, legenda POP 1 palavra por vez, `@helixgamess` 30% no centro, `-14 LUFS`, `10s–90s` sem cortar silêncios), coordenadas calibradas e checklist de 7 pontos.
- **Modelos visuais aprovados (`exemplos_layout_9x16/`):**
  - Contém as **16 telas-exemplo (`1080x1920`)** já calibradas (4 layouts × 4 canais: `Alanzoka`, `Bistecone`, `DaviJones` e `MrFall`).

---

## 7. Dependências do ambiente

- Python 3.11 (`py -3.11`) com `faster-whisper`, `streamlink` e `yt-dlp`.
  - Comando de instalação caso precise configurar outra máquina:
    ```bat
    py -3.11 -m pip install -U streamlink yt-dlp faster-whisper
    ```
- `ffmpeg` e `ffprobe` no `PATH` ou em `C:\ffmpeg\bin\`.
