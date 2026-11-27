<p align="center">
  <img src="scripts/assets/logo_128.png" alt="AutoClipStream Logo" width="96" />
</p>

<h1 align="center">AutoClipStream</h1>

<p align="center">
  Captura Contínua de Lives, Transcrição GPU, Análise com IA, Cortes Automáticos e Marcadores para Adobe Premiere Pro
</p>

---

Sistema para capturar transmissões ao vivo da Twitch e do YouTube, segmentar continuamente em blocos MKV de 10 minutos, transcrever áudio com faster-whisper, detectar destaques com áudio + texto + IA local ou via API, gerar sequências XML com marcadores In-Out para o Adobe Premiere Pro e extrair clipes MP4 com legendas SRT sincronizadas.

---

## Estrutura do Repositório

```text
AutoClipStream/
|-- scripts/
|   |-- assets/                # Ícones e logotipos da aplicação
|   |-- config.py              # Configurações globais e resolução de binários
|   |-- stream_app.py          # Servidor HTTP local, watchers e API da interface
|   |-- auto_clip.py           # Engine multimodal de scoring (áudio, texto, visão e IA) e estado vivo
|   |-- plugins/               # Sistema modular de detectores (OCR, Gemma Vision)
|   |   |-- base.py            # Classe base e gerenciador de plugins
|   |   |-- ocr_detector.py    # Detector de texto na tela via Windows Media OCR
|   |   `-- vision_gemma.py    # Detector visual via Gemma 3 Vision local
|   |-- gravar_lives.py        # Gravador contínuo multi-canal (Streamlink / yt-dlp)
|   |-- preparar_lote.py       # Whisper GPU (word-timestamps) + reaproveitamento de transcrições
|   |-- finalizar_lote.py      # Organização de lotes, atualização do estado_live.md e XML Premiere
|   |-- cortar_marcadores.py   # Utilitário de recorte de clipes MP4 e legendas SRT
|   |-- editar_vertical.py     # Renderizador de layouts verticais 9:16
|   |-- premiere_xml.py        # Gerador xmeml v4 validado
|   `-- app_ui.html            # Interface web do painel de controle
|-- canais.json                # Configuração e cadastro de canais
|-- canais/                    # Diretório local de gravações (ignorado pelo git)
|-- marketing/                 # Documentação editorial e padrões de retenção por jogo
|-- exemplos_layout_9x16/      # Modelos visuais calibrados
|-- atalhos_cli/               # Atalhos opcionais para execução em linha de comando
|   |-- 1_GRAVAR_LIVES.bat     # Inicia gravação contínua no terminal
|   |-- 2_PREPARAR_LOTE.bat    # Executa Whisper e frames nos blocos fechados
|   `-- 3_FINALIZAR_LOTE.bat   # Finaliza lotes e gera XML do Premiere
`-- Abrir Stream-clipping App.bat # Inicia o servidor e abre a interface visual
```

---

## Como Usar

### Painel Visual (Recomendado)

Execute o arquivo `Abrir Stream-clipping App.bat` ou inicie pelo terminal:

```bat
py scripts\stream_app.py
```

Acesse no navegador: `http://127.0.0.1:8795`

Pelo painel é possível:
- Cadastrar, editar e ativar múltiplos canais da Twitch e do YouTube.
- Iniciar e parar gravações simultâneas, além de inserir marcadores manuais ao vivo.
- Configurar individualmente cada canal na barra lateral (Transcrição, Lote, XML, Cortes, IA e limpeza de fila pós-clip).
- Acompanhar logs isolados por canal (com herança de eventos globais do sistema).
- Definir diretórios personalizados de gravações e de saída dos clipes no modal de Configurações Globais.
- Baixar e gerenciar o modelo `gemma3:4b` no Ollama com um clique.

---

## Configurações por Canal e Modos de Operação

Cada canal possui configuração independente na barra lateral:

1. **Transcrição (Whisper):**
   - **Automático:** pré-transcreve cada bloco de 10 minutos em segundo plano assim que fecha, utilizando `word_timestamps` para sincronização precisa de legendas `.srt`.
   - **Manual:** mantém a GPU livre durante a gravação e transcreve apenas sob demanda. Transcrições já geradas são reaproveitadas automaticamente ao criar lotes.
2. **Preparação de Lote (Premiere):**
   - **Automático:** move os blocos fechados para `03_em_processamento` assim que ficam prontos.
   - **Manual:** acumula os blocos em `02_para_processar` para criação de lotes sob demanda.
3. **Geração de XML (Premiere):**
   - **Automático:** analisa lotes novos com IA, gera `decisao_lote.json` e exporta o XML para o Premiere Pro em `04_para_edicao`.
   - **Manual:** aguarda acionamento manual no painel do lote.
4. **Criação de Cortes (Clipes):**
   - **Automático:** ao fechar o bloco (e concluir a transcrição, se automática), pontua áudio, texto e IA e extrai os melhores trechos MP4 + SRT.
   - **Manual:** extrai os clipes MP4 apenas ao clicar em "Gerar Cortes".
5. **Auxílio de IA:**
   - **Sem IA:** heurística offline baseada em picos de energia acústica (FFmpeg) e vocabulário de impacto na transcrição.
   - **Ollama Local (Gemma 3 4B):** inferência local em `http://localhost:11434` sem custo de API.
   - **IA Local Custom:** conecta a servidores compatíveis locais (LM Studio, vLLM, Ollama com outros modelos como `qwen3-vl:4b`).
   - **API Externa:** conecta a endpoints compatíveis com OpenAI (OpenAI, Gemini, OpenRouter, Groq).

---

## Estado da Live (`estado_live.md`)

Cada canal mantém o arquivo vivo `estado_live.md`, exibido e editável diretamente na aba **Estado da Live** do painel.

- **Memória contínua:** como as lives são fatiadas em blocos de 10 minutos, o `estado_live.md` preserva o contexto do jogo, objetivos e acontecimentos recentes entre um bloco e outro.
- **Atualização incremental:** ao analisar blocos com IA (no corte automático ou manual) ou ao finalizar lotes do Premiere, o sistema atualiza o estado atual e anexa o resumo da rodada no histórico cronológico.

---

## Requisitos do Sistema

- Windows 10/11 com Python 3.10 ou superior (testado com Python 3.12 e 3.14).
- FFmpeg e FFprobe no PATH ou instalados via WinGet.
- GPU NVIDIA com suporte CUDA para aceleração do Whisper e Ollama (recomendado).
- Pacotes Python: `faster-whisper`, `streamlink`, `yt-dlp`, `pillow`.
