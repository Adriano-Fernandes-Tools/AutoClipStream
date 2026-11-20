# Como escolher um corte de qualquer jogo

Assista ao trecho como alguém que acabou de entrar na live. **O que mudou entre o primeiro e o último quadro?** Pode ser o placar, uma ideia do jogador, o sentido de uma fala ou o som que denuncia onde está um adversário. Se você consegue apontar o momento da mudança, já tem onde procurar o início e o fim do corte.

## Uma pauta que atravessa jogos

Escreva em uma frase por que alguém voltaria para o próximo corte. Uma série chamada “A escolha que virou a partida” pode mostrar o desvio que decidiu uma corrida ou a mudança de rota que salvou recursos numa partida de estratégia. O elo entre os episódios é ver uma decisão e descobrir a consequência. Já uma pessoa que procura um tutorial sobre um item específico precisa do nome do jogo, do item e, às vezes, da versão no título. Registre qual dessas expectativas orientou o corte para poder comparar as publicações depois.

## Triagem no material bruto

1. **Escolha para quem vale a cena.** Um iniciante pode querer entender por que a porta abriu. Quem já conhece o jogo pode querer ver a ordem exata dos comandos. Isso muda o ponto de entrada, o texto e o que precisa ficar legível.
2. **Localize a entrega.** Marque o segundo em que se vê ou ouve a resposta, a piada, a execução ou a consequência. Use a transcrição, o marcador manual e a grade para encontrar candidatos. Veja o vídeo e escute o áudio antes de fechar o intervalo. Inclua na triagem pelo menos uma passagem visual que os picos de áudio e texto possam ter deixado passar.
3. **Volte ao primeiro detalhe necessário.** Esse é o candidato a IN. Pode ser a hesitação antes do salto, o contador quando ainda marcava zero, uma pergunta do chat que foi lida em voz alta ou o pedido do colega. No OUT, deixe o resultado tempo suficiente para ser percebido. Se o desfecho cair no bloco seguinte, localize a continuação antes de prometer a resposta.
4. **Escolha o destino.** Aproveite quando a cena se sustentar. Reenquadre ou acrescente uma linha factual se faltar contexto. Deixe pendente quando faltar o trecho com a causa ou o resultado. Se o título só funcionar inventando uma intenção, um recorde ou uma fala, procure outro ângulo.

## Formatos para reconhecer na live

São opções para decidir o que conservar na montagem. Os exemplos são fictícios.

| O que a gravação oferece | Abertura possível | O que precisa sobreviver ao corte |
| --- | --- | --- |
| Jogada decidida por pouco, em corrida ou esporte | Placar e relógio no instante anterior à tentativa | A jogada e a mudança no placar. Quem vê os dois pode entender a virada mesmo sem narração. |
| Teste ou dica de estratégia | “Quanto sobra se eu mudar a rota?” ou a tela de recursos antes da escolha | Condição inicial, uma decisão identificável e o saldo final. Se as tentativas ocorrerem em dificuldades diferentes, explicite a diferença. |
| Descoberta em um quebra-cabeça, sem fala nem webcam | Mostrar as peças antes de uma delas girar | O movimento, a ligação entre as peças e a porta abrindo. Preservar a posição relativa delas no quadro. |
| Humor em jogo cooperativo | A instrução que os colegas entendem de maneiras diferentes | Quem falou, qual foi a resposta e o que os personagens fizeram. Guardar a pausa se ela cria a piada. |
| Suspense ou exploração | O ruído que faz o jogador parar | A direção do som, a espera e a revelação ou reação. Uma legenda curta pode identificar o ruído para quem assiste sem áudio. |
| Construção ou progresso longo | O problema inicial visível, como uma fila de produção parada | A intervenção e um depois comparável. Indicar a passagem de tempo quando a montagem juntar momentos distantes. |
| Opinião durante a live | A frase em que o streamer toma uma posição sobre algo específico | O motivo e, se houver, a cena a que ele se refere. Atribuir a opinião ao streamer e preservar ressalvas que mudem o sentido. |

## Exemplo de decisão completa

Num **exemplo fictício** de jogo cooperativo, o bloco mostra uma ponte recolhida em `02:08`. Em `02:10`, uma pessoa pisa na placa e a ponte se estende. Ela diz “vai, eu seguro”. Em `02:16`, sai da placa. Em `02:17`, a ponte recolhe com o colega no meio. Em `02:21`, ouve-se “era pra eu ficar?” e os dois riem. O detector poderia marcar a risada. Começar ali perderia a promessa que dá sentido à piada.

| Decisão | Como ficaria neste corte |
| --- | --- |
| Público e objetivo | Quem gosta de humor cooperativo deve entender o desencontro mesmo vendo o jogo pela primeira vez. |
| IN e OUT no bloco | Entrar em `02:10`, com o primeiro jogador sobre a placa, antes de “eu seguro”. Sair em `02:22`, depois da réplica. No lote, os limites são `in_seg = 130` e `out_seg = 142`. |
| Texto e enquadramento | Testar a fala original como abertura. Se a função da placa ficar ambígua, usar “A ponte depende de quem fica na placa”. Manter placa e ponte visíveis quando o jogador der o passo. |
| Aprovação | Mostrar a alguém que perdeu a live e perguntar “Por que a ponte recolheu?”. Se a resposta for “não vi”, rever o enquadramento antes de mudar o título. |

**Ficha reutilizável.** Registre canal e jogo, arquivo do bloco, IN/OUT, público pretendido, o que ele precisa saber ao entrar, segundo da entrega, pista visual ou sonora e decisão de edição. O arquivo e os tempos permitem a outro editor reencontrar a cena. No fluxo de lote, use `titulo` e `comentario` do marcador para a nota editorial. [Campos aceitos e fluxo do projeto](../fluxo-do-projeto.md)

Para transformar a decisão em um post, consulte [edição, headlines e publicação](edicao-e-publicacao.md). Para avaliar o resultado, use [testes e medição](testes-e-medicao.md).
