# Revisão das evidências de marketing

**Qual montagem ajuda o público deste canal a acompanhar um momento que nasceu ao vivo?** Os estudos sugerem hipóteses para essa pergunta. Um guia roteirizado sobre um item novo pode ter muitas views porque os jogadores procuram o item naquela semana. Reproduzir sua abertura num corte de live muda tanto a pauta quanto o público.

## O que foi possível conferir

A cópia fornecida está na pasta externa de dados, identificada aqui como `AutoClipStream-Codex-marketingdata/`. Os caminhos abaixo partem dessa pasta.

| Fonte disponível | Cobertura e uso |
| --- | --- |
| `analise canais fortnite/README.md`, `_progresso.md`, `_taxonomia.md`, `comparativo.md` e 11 relatórios em `canais/` | Dez canais × 50 Shorts no recorte inicial, consultados em 31/10/2026. S11 acrescentou 50 em 04/11/2026. O adendo avisa que as seções 3 a 8 do comparativo preservam os 500 iniciais. A tabela de facecam da §6 permite conferir isso diretamente (280 com rosto + 220 sem rosto). No canal adicional, 12 dos 50 tratam de assuntos fora do Fortnite. |
| `analise canais fortnite/acervo/`, o plano de @user e `acompanhamento.csv` | Os cinco documentos do acervo interpretam o levantamento. O playbook propõe testes para um criador com pautas, tempo e canais próprios. O CSV tem quatro linhas de planejamento e campos de resultado vazios. |
| `ANALISE-ROTEIROS.md` e a síntese dos ensinamentos de L01 | Duas leituras dos **mesmos 34 vídeos longos** em inglês do canal L01, sobre criação no YouTube. A primeira classifica o áudio transcrito. A segunda reúne conselhos do criador. Na comparação de roteiros, o relatório exclui sete vídeos recentes e contrasta 14 do topo (≥ 1,3×) com dez da base (< 0,85×). Os tempos de fala são estimados. A classificação por IA registra 48 dúvidas e carece de revisão manual. |

As fichas individuais, os metadados e os rótulos dos Shorts estão disponíveis em `analise/_dados/`. A conferência numérica abaixo usa esses dados. A pasta também inclui classificações manuais e miniaturas, mas não contém os vídeos integrais nem as transcrições individuais dos longos.

## Como ler os números

### Conferência a partir das fichas individuais

Cruzei os IDs de `fichas.csv`, `hooks.csv` e `metadados.csv` em `analise/_dados/`. Os três arquivos têm os mesmos 550 IDs únicos. As views das fichas coincidem com as dos metadados em todos os registros. Para cada canal, calculei a mediana das views brutas de seus 50 Shorts. Dividi as views de cada Short por essa mediana e só arredondei depois de calcular a mediana de cada categoria.

| Seleção nas fichas | Dez canais iniciais (500) | Com S11 (550) |
| --- | --- | --- |
| Abertura visual em split | 16 vídeos · 1,54× | 16 vídeos · 1,54× |
| Formato clipe | 85 · 0,90× | 85 · 0,90× |
| Pergunta falada, qualquer formato | 53 · 1,16× | 62 · 1,13× |
| Formato curiosidade **e** pergunta falada | 8 · 1,95× | 9 · 1,47× |
| Formato análise de item **e** reação falada | 4 · 1,67× | 4 · 1,67× |
| Etiqueta de polêmica | 24 · 1,44× | 27 · 1,45× |
| Destaques com pelo menos 2× a mediana do canal | 96 vídeos | 109 vídeos |

O relatório anterior traz 1,94× para curiosidade com pergunta e 1,45× para polêmica no recorte inicial. A coluna fornecida `views_rel` já tem arredondamento. Os valores acima usam as views inteiras. No subgrupo de curiosidade, acrescentar apenas um vídeo é suficiente para mudar o resultado. Com S11, a mediana passa de 1,95× para 1,47×. Os exemplos da revisão histórica abaixo citam os valores publicados nos relatórios.

`×med` divide as views do vídeo pelas views medianas do próprio canal na amostra. Um valor de 1,54× situa um grupo de vídeos em relação a essa referência. Para atribuir um ganho à edição, seria preciso comparar montagens equivalentes. Na §6, “canais acima” considera os canais com pelo menos três Shorts naquela categoria. Na tabela de títulos, a comparação é entre vídeos **com e sem** o traço, com pelo menos três de cada lado no canal.

“Retenção” na taxonomia nomeia recursos visíveis na montagem, como mostrar uma prova ou ligar o fim ao começo. O desfecho medido nessas tabelas é **views relativas**. Os relatórios não fornecem curvas de abandono, taxa de conclusão ou conversão por recurso. Idade do post, idioma, pauta da semana, público do canal e custo de produção também podem mudar a comparação. Muitos vídeos de um mesmo criador compartilham voz, layout e audiência.

## O que muda no guia geral

Os arquivos da coluna de origem, exceto os de L01, ficam em `analise canais fortnite/`.

| Alegação usada na adaptação | O que a fonte sustenta | Decisão editorial |
| --- | --- | --- |
| “Clipe solto falha” | `comparativo.md` §6 traz 85 clipes, mediana 0,90×, acima em 1/5 canais elegíveis. Os 3% são a parcela **dos 96 destaques** classificada como clipe. A categoria descreve momento de partida sem lição central. Segundo o relatório do canal S04, clipes com comentário chegam a 1,10× nesse canal. | Manter jogadas e reações que tenham causa e consequência compreensíveis. Uma virada no placar pode explicar o clipe inteiro. |
| “Split 38/62 prende mais” e “olha isso” pede zoom no rosto | `comparativo.md` §6 registra 16 **aberturas** em split, 1,54×, 2/2 canais elegíveis. `_taxonomia.md` §§3 e 7 inclui dois pontos de vista e comparação. `acervo/retencao-e-ctas.md` agrupa setas e zoom. A proporção da tela e o alvo do zoom não foram isolados. | Enquadrar o objeto da fala. Num lance decidido no minimapa, ampliar o minimapa. A câmera pode voltar depois para a reação. |
| “A primeira fala e a headline explicam 80% do resultado” | Em `comparativo.md` §3, 86% dos 500 apresentam o tema na primeira frase. É frequência de uma escolha. O material não estima contribuição de 80%, efeito de abrir meio segundo antes, nem tempo ideal da headline. O item 33 da síntese dos ensinamentos de L01 fala da leitura de uma **thumbnail**. | Escolher entre ação, fala, som ou texto observando o que o espectador precisa perceber primeiro. Testar a abertura com quem perdeu a live. |
| “Pergunta, teste e polêmica são os formatos vencedores” | Em `comparativo.md` §6, pergunta **falada** tem 1,16× (53), polêmica é uma **etiqueta** com 1,45× (24, 3/3 canais no recorte inicial). O adendo relata 4/4 com S11. O plano de @user situa 1,94× em **oito** curiosidades abertas com pergunta. Em `acervo/hooks.md`, o 1,67× vem de **quatro** análises de item abertas com reação. | Usar pergunta quando houver dúvida real e resposta no vídeo. Somar ou ordenar esses índices misturaria formato, frase de abertura, gatilho e subgrupos. |
| “Legenda de uma palavra é o padrão comprovado” | Os 79% da §3 usam a classe `palavra`, definida como **uma a três** palavras em `_taxonomia.md` §6. O 2,03× é de **cinco vídeos** de S11. No mesmo canal, 86% usam legenda em frase. | Comparar uma frase curta com a legenda por palavra quando ambas permitirem ler o diálogo e ver o jogo. |
| “O título com o nome do jogo ganha alcance” e “pedir comentário é melhor” | `acervo/titulos.md` mediu a presença de **Fortnite** (268 títulos, 1,03× com, 0,95× sem) em canais do nicho. A §6 do comparativo dá 1,11× em views aos 63 vídeos com pedido de comentário. Não há medida de indexação, de comentários ganhos pelo pedido ou de transferência para a legenda de um post em outra rede. | Usar o nome do jogo quando esclarecer o assunto. Se a meta for conversa, medir comentários por visualização e ler se as respostas tratam da pergunta. |
| “Usar inversão nos primeiros segundos porque aparece em 71% do topo” | Em `ANALISE-ROTEIROS.md` §2, o traço contraintuitivo aparece em 71% de 14 longos do topo e 40% de dez da base, sobre **crescer no YouTube**. O dado se refere ao roteiro do vídeo. | Adaptar a ideia quando a gravação mostrar uma expectativa e um resultado surpreendente. Preservar o momento da surpresa é uma decisão de montagem. |
| “Usar pergunta ou ‘como fazer’ em todos os títulos” | `ANALISE-ROTEIROS.md` §2 registra títulos “como…” em 64% do topo e 10% da base de L01. Já a linha de 1,16× nos Shorts mede a pergunta **falada no vídeo**. Um título de tutorial antes do clique e uma fala ouvida no feed ocupam lugares diferentes. | Se a pessoa procura uma solução, nomear a habilidade no título. Se a cena documenta uma dúvida que o jogador realmente resolveu, experimentar a pergunta na abertura. |

Os seis formatos de GTA, os tempos prescritos, a fonte e a marca d'água continuam disponíveis como escolhas históricas de produção. Os furos que mais limitam a generalização são a falta de resultados dos cortes de @user, a pouca cobertura de cenas visuais silenciosas e a ausência de uma comparação entre jogos e plataformas em condições semelhantes. O [plano de testes](geral/testes-e-medicao.md) trata essas três lacunas.
