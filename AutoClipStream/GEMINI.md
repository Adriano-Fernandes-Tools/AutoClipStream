# Regras do Projeto — Stream-clipping

> **Contexto completo do projeto:** consulte [AGENTS.md](AGENTS.md) (guia autocontido para qualquer chat novo do zero), [scripts.md](scripts.md) (referência de todos os scripts e da interface em `stream_app.py`) e [Marketing/README.md](Marketing/README.md) (acervo estratégico de formatos, gatilhos, hooks e headlines para GTA 6).

Sempre que o usuário pedir algo como *"processa o que temos"*, *"analisa o canal X"*, *"prepara e marca os vídeos pendentes"* ou para **gerar cortes, headlines e vídeos 9:16** dentro de `E:\Antigravity\Stream-clipping`, siga este protocolo completo:

## 1. Preparar o lote (se houver vídeos em `02_para_processar/`)
- Verifique se há arquivos `.mkv` (ou outros formatos de vídeo) em `canais/<canal>/02_para_processar/` ou lotes aguardando análise em `canais/<canal>/03_em_processamento/`.
- Se houver arquivos em `02_para_processar/`, execute:
  ```powershell
  py -3.11 "E:\Antigravity\Stream-clipping\scripts\preparar_lote.py" --canal <canal>
  ```
  *(ou `--todos` se o usuário quiser todos os canais pendentes).*

## 2. Ler contexto, transcrição e grades visuais
Para cada lote em `03_em_processamento/<lote_id>/` sem `decisao_lote.json`:
1. Leia `<lote_dir>/_pacote_ia.md` por inteiro. Ele já traz:
   - O histórico vivo de `estado_live.md` (o que o streamer está fazendo, missões, arcos).
   - Os últimos ~90 segundos do lote anterior (overlap de contexto).
   - A transcrição de cada bloco com timestamps `[MM:SS - MM:SS]`.
2. Inspecione visualmente as imagens de grade listadas em `<lote_dir>/frames/*.jpg` usando `read_file` para captar momentos visuais, mudanças de cena, gameplay, reações ou textos na tela.

## 3. Consulta Obrigatória ao Acervo de Marketing (Formatos, Hooks e Headlines)
**REGRA OBRIGATÓRIA:** **Antes** de selecionar cortes finais, escrever headlines/títulos de corte, criar `decisao_lote.json` ou gerar vídeos 9:16, você **DEVE consultar os documentos em `E:\Antigravity\Stream-clipping\Marketing\`**:
- [Marketing/README.md](Marketing/README.md) — visão geral e por que clipe solto (`0,90×`) perde para cortes com contexto desde o segundo zero.
- [Marketing/formatos-e-estruturas-gta.md](Marketing/formatos-e-estruturas-gta.md) — os **6 formatos vencedores (`F1` a `F6`)** para GTA 6 e seus esqueletos segundo a segundo.
- [Marketing/gatilhos-e-hooks-gta.md](Marketing/gatilhos-e-hooks-gta.md) — ranking dos gatilhos mentais validados e como escolher cirurgicamente o **`Ponto IN` (`0–3,5s`)** na transcrição.
- [Marketing/banco-headlines-e-titulos-gta.md](Marketing/banco-headlines-e-titulos-gta.md) — os **3 estilos de Headline** (`01 Solta`, `02 Tarja Vermelha`, `03 Caixa Branca`), **64 moldes prontos para GTA 6** e 15 moldes de título de postagem.
- [Marketing/retencao-edicao-e-checklist-gta.md](Marketing/retencao-edicao-e-checklist-gta.md) — manual técnico 9:16 `@helixgamess`, coordenadas calibradas dos 4 streamers e checklist de 7 pontos.

**O que aplicar na prática ao avaliar cada trecho:**
1. Procure encaixar o momento da live na **melhor possibilidade de formato (`F1`–`F6`) e gatilho mental** existente no acervo (evite clipe solto ou grito sem contexto).
2. Ajuste o `in_seg` (`Ponto IN`) exatamente na frase-gancho (`0–3,5s`: pergunta, intenção/desafio, veredito forte, *"olha isso"* ou 2–3s antes do desastre) e corte seco logo após o payoff final.
3. Indique no `"titulo"` e no `"comentario"` de cada marcador (ou na configuração do vídeo 9:16): o **formato (`F1`–`F6`)**, o **estilo de Headline (`01 Solta`, `02 Tarja Vermelha` ou `03 Caixa Branca`)**, se há **gatilho de Zoom (`"olha isso"`) / Modo Centro Blur / Tela Cheia do Game**, e uma **sugestão de Headline pronta** em `MAIÚSCULA`.

### Resumo Rápido do Padrão Visual 9:16 (`@helixgamess`)
- **Canvas & Duração:** `1080x1920` (9:16, 60 fps), mínimo **`10s`**, máximo **`90s` (`1:30`)** com foco no menor tempo possível (`20s–60s`), **sem cortar silêncios** automaticamente.
- **Modos de Tela (corte seco):** **Split `38%` topo streamer / `62%` base game** (sem linha divisória); **Streamer Centro + Fundo do Game Blur** quando estiver só conversando; **Tela Cheia do Game** apenas se houver algo muito engraçado no jogo, revezando com a reação do streamer; **Zoom na webcam** apenas ao dizer *"olha isso"* ou similar.
- **Tipografia, Marca d'Água e Áudio:** Fonte **`Poppins Black` MAIÚSCULA**; **Headline** fixa nos **primeiros `5s–10s`**; **legenda POP de 1 palavra por vez**; marca d'água **`@helixgamess`** no centro com **30% de opacidade**; áudio normalizado em **`-14 LUFS`**.

## 4. Critérios editoriais e criação de `decisao_lote.json`
Procure apenas trechos com potencial real de edição/corte (não marque conversa morna ou tempo morto), aplicando o checklist do acervo de Marketing:
- **Marcadores manuais (`marcadores_manuais`):** têm prioridade máxima; nunca descarte um bloco que possua marcador manual.
- **Ganchos e histórias completas**: início claro da ideia/situação (`Ponto IN` 0–3,5s) e fechamento natural com payoff cumprido (`in_seg` e `out_seg` com margem limpa).
- **Clímax / reações fortes**: viradas, sustos, risadas genuínas, raiva, conquistas difíceis (`tipo: "climax"`).
- **Cortes prontos / boas falas**: opiniões fortes, dicas úteis, histórias engraçadas (`tipo: "corte_forte"`).
- **Trechos promissores para conferir**: momentos interessantes que podem precisar de ajuste na ilha (`tipo: "possivel_corte"`).
- **Momentos puramente visuais**: jogadas/bugs/acontecimentos na tela sem muita fala (`tipo: "momento_visual"`).
- **Emendas entre blocos**: se um momento bom começar nos últimos segundos de um bloco e terminar no início do seguinte (ou continuar do bloco anterior), marque ambos com `tipo: "continuacao"` e mantenha ambos como `"status": "aprovado"`.

Regras obrigatórias de `decisao_lote.json`:
- Liste **todos** os blocos do lote no array `"blocos"`, na mesma ordem do `_pacote_ia.json`.
- Nunca invente tempos maiores que `duracao_seg` do bloco nem `out_seg <= in_seg`.
- Blocos sem nenhum momento interessante devem receber `"status": "descartado"`, `"marcadores": []` e um `"motivo_descarte"` curto.
- Preencha `"resumo_rodada"`, `"estado_atual_live"` e `"arcos_em_aberto"` de forma fiel ao que aconteceu no lote para manter `estado_live.md` sempre útil na próxima rodada.

## 5. Finalizar o lote e responder ao usuário
1. Execute:
   ```powershell
   py -3.11 "E:\Antigravity\Stream-clipping\scripts\finalizar_lote.py" --canal <canal> --lote <lote_id>
   ```
   *(A geração do XML do Premiere fica **desativada por padrão**; passe `--gerar-xml` apenas quando o usuário pedir. Blocos descartados com `"cortes_descartados"` geram recortes rápidos In–Out em `06_arquivo_descartados/<lote_id>/cortes_para_conferencia/`.)*
2. Confirme que os arquivos foram movidos (`04_para_edicao/<lote_id>/` e `06_arquivo_descartados/<lote_id>/`) e que o `.xml` da rodada foi gerado.
3. Apresente ao usuário links clicáveis (`file:///...`) para o `.xml` pronto para importar no Premiere, para o `resumo_lote.md` e para o `estado_live.md`, listando os principais cortes encontrados.
