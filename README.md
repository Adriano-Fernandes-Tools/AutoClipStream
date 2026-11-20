# AutoClipStream — Captura Contínua e Marcadores para Premiere Pro

Sistema para capturar transmissões ao vivo da Twitch e do YouTube, segmentar continuamente em blocos MKV de 10 minutos, transcrever áudio com faster-whisper e gerar sequências XML com marcadores In-Out para o Adobe Premiere Pro ou extrair clipes de vídeo diretamente.

---

## Estrutura do Repositório

```text
AutoClipStream/
|-- scripts/
|   |-- config.py              # Configurações globais e resolução de binários
|   |-- stream_app.py          # Servidor HTTP local e API da interface
|   |-- auto_clip.py           # Engine multimodal de scoring (áudio, texto, visão e IA)
|   |-- plugins/               # Sistema modular de detectores (OCR, Gemma Vision)
|   |   |-- base.py            # Classe base e gerenciador de plugins
|   |   |-- ocr_detector.py    # Detector de texto na tela via Windows Media OCR
|   |   `-- vision_gemma.py    # Detector visual via Gemma 3 Vision local
|   |-- gravar_lives.py        # Gravador contínuo multi-canal (Streamlink / yt-dlp)
|   |-- preparar_lote.py       # Whisper GPU + geração de grades de frames
|   |-- finalizar_lote.py      # Organização de lotes e gerador de XML Premiere Pro
|   |-- cortar_marcadores.py   # Utilitário de recorte de clipes MP4
|   |-- editar_vertical.py     # Renderizador de layouts verticais 9:16
|   |-- premiere_xml.py        # Gerador xmeml v4 validado
|   `-- app_ui.html            # Interface web do painel de controle
|-- canais.json                # Configuração e cadastro de canais
|-- canais/                    # Diretório local de gravações (ignorado pelo git)
|-- Marketing/                 # Documentação editorial e padrões de retenção
|-- exemplos_layout_9x16/      # Modelos visuais calibrados
|-- 1_GRAVAR_LIVES.bat         # Inicia gravação contínua no terminal
|-- 2_PREPARAR_LOTE.bat        # Executa Whisper e frames nos blocos fechados
|-- 3_FINALIZAR_LOTE.bat       # Finaliza lotes e gera XML do Premiere
`-- Abrir Stream-clipping App.bat # Inicia o servidor e abre a interface visual
```

---

## Como Usar

### 1. Painel Visual (Recomendado)

Execute o arquivo `Abrir Stream-clipping App.bat` ou inicie pelo terminal:

```bat
py scripts\stream_app.py
```

Acesse no navegador: `http://127.0.0.1:8795`

Pelo painel é possível:
- Cadastrar e ativar múltiplos canais da Twitch e do YouTube.
- Iniciar e parar gravações simultâneas.
- Alternar o modo de análise entre manual (em blocos) e automático (ao vivo).
- Selecionar o nível de auxílio de IA (desabilitado, Ollama local ou API externa).
- Configurar pasta personalizada para salvar os clipes recortados.
- Baixar e gerenciar o modelo Gemma 3 (4B) no Ollama com um clique.

---

## Modos de Operação

### Modo de Análise

1. **Em blocos (Manual):**
   Os blocos de 10 minutos são gravados e movidos para a pasta de fila. A transcrição e o recorte só ocorrem quando solicitados pelo editor.
2. **Automático (Ao vivo):**
   Assim que um bloco de 10 minutos fecha, o sistema transcreve o áudio, calcula o score de destaque do trecho e, caso atinja o limiar configurado, extrai automaticamente os clipes para a pasta de saída.

### Auxílio de Inteligência Artificial

1. **Sem IA (Heurística Padrão):**
   Opera 100% offline sem alocação extra de VRAM. Avalia picos de energia acústica RMS via FFmpeg e densidade de vocabulário de impacto na transcrição.
2. **Ollama Local (Gemma 3 4B):**
   Executa inferência local via Ollama no endereço `http://localhost:11434`. Permite avaliação semântica refinada sem custos de API e com privacidade total.
3. **API Externa:**
   Permite conectar endpoints compatíveis com a especificação da OpenAI (Groq, OpenAI, OpenRouter).

---

## Requisitos do Sistema

- Python 3.10 ou superior (testado com Python 3.12 e 3.14).
- FFmpeg e FFprobe no PATH ou na pasta do WinGet.
- GPU com suporte CUDA para aceleração de Whisper e Ollama (recomendado).
- Pacotes Python: `faster-whisper`, `streamlink`, `yt-dlp`, `pillow`.
