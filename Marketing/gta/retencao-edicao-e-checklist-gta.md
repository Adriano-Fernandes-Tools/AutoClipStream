# Manual de Retenção, Padrão de Edição 9:16 e Checklist

> Arquivo histórico. Os enquadramentos e a identidade visual pertencem à produção de GTA com quatro canais. Consulte os [limites do renderizador](../fluxo-do-projeto.md) e a [revisão das alegações de retenção](../revisao-das-fontes.md).

Este documento consolida todas as especificações visuais e técnicas fechadas para o projeto, combinadas às táticas de retenção comprovadas nos estudos de **550 Shorts** e **34 vídeos do `L01`**. Serve como referência obrigatória tanto para edição manual quanto para o sistema de edição automática.

---

## 1. Especificações Técnicas e Visuais Fechadas

| Parâmetro | Especificação Oficial | Motivo / Validação |
| :--- | :--- | :--- |
| **Resolução & FPS** | **`1080x1920`** (9:16 vertical), **60 fps** | Padrão nativo máximo de nitidez para TikTok, Reels e YouTube Shorts. |
| **Duração Alvo** | Mínimo **`10s`**, máximo **`90s` (`1:30`)**. Foco no **menor tempo possível (`20s–60s`)** | Cortes enxutos sem gordura mantêm alta taxa de conclusão sem truncar a história. |
| **Silêncios** | **NÃO cortar silêncios automaticamente** | Pausas de gameplay são parte do suspense, cutscenes e timing cômico. |
| **Fonte Oficial** | **`Poppins Black`** (`C:\Windows\Fonts\Poppins-Black.ttf`), **100% MAIÚSCULA** | Máxima legibilidade no celular mesmo com movimento rápido atrás. |
| **Headline (Gancho)** | Fixa nos **primeiros `5s` a `10s`** (Estilos **01 Solta**, **02 Tarja Vermelha** ou **03 Caixa Branca**) | Garante leitura do **Conceito + Stake** no segundo `0,0s` (`titulo-rosto` acima da mediana em **4 de 4 canais**). |
| **Legenda Dinâmica** | **1 palavra por vez** em estilo **POP** (`y ≈ 1470–1500`), contorno preto grosso + sombra, palavra-chave em amarelo | 79% dos Shorts do estudo usam legenda por palavra; no S11, **1 palavra por vez rendeu `2,03×`** (contra frase longa). |
| **Marca d'Água** | **`@user`** no centro da tela com **30% de opacidade** | Protege os cortes contra repostagem sem atrapalhar a visão do jogo. |
| **Áudio** | Normalizado no padrão YouTube (**`-14 LUFS`**) | Garante volume consistente sem estourar picos de grito do streamer. |

---

## 2. Os 4 Modos Visuais de Enquadramento e Transição

Toda transição entre modos deve ser feita em **corte seco (`corte seco`)**, sem transições lentas ou firulas que desviam atenção.

1. **Modo Principal — Split-Screen (`38%` Topo Streamer `1080x720` / `62%` Base Game `1080x1200`):**
   - Corte seco sem linha divisória; webcam no topo (`y=0..720`), gameplay na base (`y=720..1920`).
   - Formato validado no estudo (`split`: **`1,54×`**, acima da mediana em 2 de 2 canais).
2. **Zoom na Câmera do Streamer:**
   - Aplicado **apenas** quando o streamer disser gatilhos diretos de atenção como *"olha isso"*, *"repara nisso"*, *"olha lá"* (e voltar ao enquadramento normal depois da reação).
3. **Troca para Tela Inteira do Game (`1080x1920`):**
   - Aplicada **apenas** quando houver algo visualmente muito engraçado/caótico acontecendo no jogo que mereça foco total, **revezando em corte seco com a reação do streamer** no Split.
   - *(A antiga Tela 4 estática com bordas embaçadas em cima e embaixo foi descartada.)*
4. **Modo "Streamer Conversando" (Fundo Blur):**
   - Usado quando o streamer está **apenas conversando/opinando** sem ação relevante acontecendo na tela do jogo.
   - Fundo do game em `1080x1920` desfocado (`gblur`), câmera do streamer centralizada no meio (`1080x720` em `y=580..1300`), Headline acima (`y ≈ 420`) e legenda POP abaixo (`y ≈ 1470`).

### Coordenadas Calibradas dos 4 Canais (`1920x1080` fonte)

| Canal | Webcam Normal (`x, y, w, h`) | Webcam Zoom *"Olha isso"* (`x, y, w, h`) | Região Principal do Game (`x, y, w, h`) |
| :--- | :--- | :--- | :--- |
| **P01** | `1485, 486, 421, 268` | `1520, 495, 340, 225` | `400, 120, 1060, 960` |
| **P02** | `1450, 162, 468, 302` | `1510, 195, 340, 235` | `320, 80, 1100, 1000` |
| **P03** | `1505, 106, 405, 228` | `1555, 115, 305, 195` | `320, 140, 1150, 940` |
| **P04** | `1280, 605, 635, 423` | `1360, 620, 555, 370` | `220, 100, 1200, 980` |

---

## 3. Técnicas de Retenção Validadas pelo Estudo

1. **Prova Visual Sincronizada (`1,15×`, acima em 6 de 9 canais · L01 #88 e #89):**
   - Tudo o que o streamer menciona precisa aparecer claramente dentro do crop vertical de 62% (ou em corte seco para Tela Cheia). Nunca deixe o espectador ouvindo o streamer rir de algo que ficou cortado para fora da borda esquerda/direita.
2. **Guie o Olhar no Eixo Central (L01 #90):**
   - A junção webcam/game (`y=720`), a Headline inicial, a marca d'água central e a legenda POP de 1 palavra mantêm o olhar do usuário sempre no **eixo central** da tela, reduzindo fadiga visual no celular.
3. **Loop Honesto (`1,17×`):**
   - Sempre que a gravação natural permitir, encerre o corte em uma frase ou reação que **conecte de volta** à pergunta ou tese da Headline inicial — sem truncar a resposta nem enganar o público.
4. **CTAs no Final do Vídeo (`comparativo.md` §6):**
   - **Não adicione telas finais longas pedindo inscrição** (cairia o final do corte e o CTA `seguir` ficou neutro em `0,98×`).
   - Os melhores resultados vêm de **nenhum CTA dentro do vídeo (`1,04×`, 5/7 canais)**, terminando seco no ápice, e usando uma **pergunta real para comentários (`1,11×`, o único CTA positivo)** no texto da legenda da postagem.
5. **Evite a Todo Custo Pergunta Sem Resposta (`0,79×`):**
   - Se a Headline perguntou *"O que acontece se..."*, o corte **tem que mostrar** o que aconteceu antes de terminar.

---

## 4. Checklist de 7 Pontos para Aprovar um Corte de GTA 6

Antes de renderizar ou aprovar qualquer corte (seja na triagem manual do `_pacote_ia.md` ou na automação futura), valide estes **7 pontos**:

1. **Teste do Segundo 0 (`Headline`):** A Headline em `Poppins Black` MAIÚSCULA (4 a 9 palavras) entrega claramente uma pergunta, polêmica, conflito ou curiosidade em **1 segundo de leitura**, mesmo sem som?
2. **Teste dos 3,5 Segundos (`Ponto IN`):** A 1ª frase falada pelo streamer já entra direto no assunto, intenção ou ação, sem enrolação (*"então chat..."*, saudações ou risada solta sem contexto)?
3. **Teste do "Clipe Solto":** Uma pessoa aleatória no TikTok/Reels que **não estava na live** entende *o que está em jogo (stake)* nos primeiros 5 segundos?
4. **Enquadramento Adequado:**
   - Está em **Split 38%/62%** para gameplay + reação?
   - Trocou para **Modo Centro + Fundo Blur** se o streamer ficou apenas conversando?
   - Usou **Tela Cheia do Game** em corte seco apenas nos momentos visuais muito engraçados, revezando com a reação?
5. **Gatilho de Zoom:** Se o streamer falou *"olha isso"* (ou equivalente), o Zoom na webcam foi aplicado? Se não falou, a câmera permaneceu no enquadramento normal?
6. **Payoff Cumprido:** A promessa feita pela Headline foi 100% entregue no final (sem promessa falsa nem pergunta sem resposta)?
7. **Corte Final Seco e Duração (`10s–90s`):** O vídeo tem entre `10s` e `90s` (ideal `20s–60s`), preservou os silêncios necessários de suspense do game e cortou seco imediatamente após o clímax final?
