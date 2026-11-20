"""Modo A: gera um .xml do Premiere para cada .mp4 desta pasta.

Le os marcadores do banco do Insights Capture (somente leitura) e grava
"<nome do video>.xml" ao lado do video. Importar no Premiere: File > Import.
Cria uma sequencia com o video e os marcadores na regua da timeline.
Nao altera os videos nem o banco. Nao sobrescreve arquivos que ja existem,
a menos que rode com --sobrescrever.
"""
import json
import os
import sqlite3
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

TICKS_POR_SEGUNDO = 254016000000

# nome, cor do marcador (None = verde, a cor padrao do Premiere)
TIPOS = {
    "kill": ("Kill", None),
    "knockout": ("Knock", 4281049552),  # amarelo
    "knockedout": ("Derrubado", 4280578025),  # laranja
    "death": ("Morte", 4281740498),  # vermelho
    "bookmark": ("Marcador manual", 4294741314),  # azul
}


def abrir_banco():
    caminho = Path(os.environ["APPDATA"]) / "Insights Capture" / "db.sqlite3"
    url = "file:///" + quote(caminho.as_posix(), safe="/:") + "?mode=ro&immutable=1"
    return sqlite3.connect(url, uri=True)


def marcadores_do_video(banco, nome):
    """Devolve a lista (segundos, tipo, comentario), ou None se o video nao esta no banco."""
    achados = [
        uuid
        for uuid, caminho in banco.execute("SELECT uuid, file_path FROM video")
        if caminho and caminho.replace("\\", "/").rsplit("/", 1)[-1].lower() == nome.lower()
    ]
    if not achados:
        return None
    marcas = []
    for uuid in achados:
        for t, chave, valor in banco.execute(
            "SELECT timestamp, key, value FROM video_game_event WHERE video_uuid = ?", (uuid,)
        ):
            try:
                valor = json.loads(valor) if valor else None
            except ValueError:
                valor = None
            marcas.append((t, chave, valor if isinstance(valor, str) else ""))
        for (t,) in banco.execute("SELECT timestamp FROM video_bookmark WHERE video_uuid = ?", (uuid,)):
            marcas.append((t, "bookmark", ""))
    return sorted(marcas, key=lambda m: m[0])


def ler_video(video):
    saida = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    dados = json.loads(saida)
    imagem = next(s for s in dados["streams"] if s["codec_type"] == "video")
    audios = [s for s in dados["streams"] if s["codec_type"] == "audio"]
    fps = Fraction(imagem["avg_frame_rate"])
    return imagem, audios, fps, float(dados["format"]["duration"])


def bloco_tempo(fps):
    base = round(fps)
    ntsc = "TRUE" if fps.denominator != 1 else "FALSE"
    return f"<rate><timebase>{base}</timebase><ntsc>{ntsc}</ntsc></rate>"


def montar_xml(video, marcas):
    imagem, audios, fps, duracao = ler_video(video)
    quadros = round(duracao * fps)
    rate = bloco_tempo(fps)
    nome = escape(video.name)
    url = "file://localhost/" + quote(video.resolve().as_posix(), safe="/").replace(":", "%3a")
    largura, altura = imagem["width"], imagem["height"]

    midia_audio = "".join(
        f"<audio><samplecharacteristics><depth>16</depth><samplerate>{a.get('sample_rate', 48000)}</samplerate>"
        f"</samplecharacteristics><channelcount>{a.get('channels', 2)}</channelcount></audio>"
        for a in audios
    )
    arquivo = (
        f'<file id="file-1"><name>{nome}</name><pathurl>{escape(url)}</pathurl>{rate}'
        f"<duration>{quadros}</duration><media><video><duration>{quadros}</duration>"
        f"<samplecharacteristics>{rate}<width>{largura}</width><height>{altura}</height>"
        f"</samplecharacteristics></video>{midia_audio}</media></file>"
    )

    # Mesma estrutura que o Premiere exporta: cada audio estereo do arquivo ocupa
    # duas faixas "explodidas" da sequencia, e video e audio ficam ligados entre si.
    faixas = []  # (numero do audio no arquivo, tipo, parte do par, total do par)
    for i, a in enumerate(audios, 1):
        if a.get("channels", 2) == 2:
            faixas += [(i, "Stereo", 0, 2), (i, "Stereo", 1, 2)]
        else:
            faixas.append((i, "Mono", 0, 1))

    ligacoes = (
        "<link><linkclipref>clipitem-1</linkclipref><mediatype>video</mediatype>"
        "<trackindex>1</trackindex><clipindex>1</clipindex></link>"
    ) + "".join(
        f"<link><linkclipref>clipitem-{n}</linkclipref><mediatype>audio</mediatype>"
        f"<trackindex>{n - 1}</trackindex><clipindex>1</clipindex><groupindex>1</groupindex></link>"
        for n in range(2, len(faixas) + 2)
    )
    ticks_fim = round(quadros * TICKS_POR_SEGUNDO / fps)

    def clipe(numero, conteudo, canal=""):
        return (
            f'<clipitem id="clipitem-{numero}"{canal}><masterclipid>masterclip-1</masterclipid>'
            f"<name>{nome}</name><enabled>TRUE</enabled>"
            f"<duration>{quadros}</duration>{rate}<start>0</start><end>{quadros}</end>"
            f"<in>0</in><out>{quadros}</out><pproTicksIn>0</pproTicksIn><pproTicksOut>{ticks_fim}</pproTicksOut>"
            f"{conteudo}{ligacoes}</clipitem>"
        )

    faixas_audio = "".join(
        f'<track PannerCurrentValue="0.5" PannerIsInverted="true" PannerName="Balance" '
        f'currentExplodedTrackIndex="{parte}" totalExplodedTrackCount="{total}" premiereTrackType="{tipo}">'
        + clipe(
            n,
            '<file id="file-1"/>'
            f"<sourcetrack><mediatype>audio</mediatype><trackindex>{i}</trackindex></sourcetrack>",
            f' premiereChannelType="{tipo.lower()}"',
        )
        + "</track>"
        for n, (i, tipo, parte, total) in enumerate(faixas, 2)
    )
    conteudo_video = (
        "<alphatype>none</alphatype><pixelaspectratio>square</pixelaspectratio>"
        f"<anamorphic>FALSE</anamorphic>{arquivo}"
    )
    saidas = "".join(
        f"<group><index>{c}</index><numchannels>1</numchannels><downmix>0</downmix>"
        f"<channel><index>{c}</index></channel></group>"
        for c in (1, 2)
    )

    xml_marcas, usados = [], 0
    for segundos, chave, comentario in marcas:
        if segundos < 0 or segundos > duracao:
            continue
        titulo, cor = TIPOS.get(chave, (chave, None))
        q = round(segundos * fps)
        tag_cor = f"<pproColor>{cor}</pproColor>" if cor else ""
        xml_marcas.append(
            f"<marker><comment>{escape(comentario)}</comment><name>{escape(titulo)}</name>"
            f"<in>{q}</in><out>-1</out>{tag_cor}</marker>"
        )
        usados += 1

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1" explodedTracks="true"><duration>{quadros}</duration>{rate}<name>{escape(video.stem)}</name>'
        f"<media><video><format><samplecharacteristics>{rate}<width>{largura}</width><height>{altura}</height>"
        "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
        f"<track>{clipe(1, conteudo_video)}</track></video>"
        "<audio><numOutputChannels>2</numOutputChannels><format><samplecharacteristics><depth>16</depth>"
        f"<samplerate>48000</samplerate></samplecharacteristics></format><outputs>{saidas}</outputs>"
        f"{faixas_audio}</audio></media>"
        f"{''.join(xml_marcas)}</sequence></xmeml>\n"
    )
    return xml, usados


def main():
    args = [a for a in sys.argv[1:] if a != "--sobrescrever"]
    sobrescrever = "--sobrescrever" in sys.argv
    pasta = Path(args[0]) if args else Path(__file__).resolve().parent
    banco = abrir_banco()
    videos = sorted(pasta.glob("*.mp4"))
    print(f"{len(videos)} video(s) em {pasta}\n")
    for video in videos:
        destino = video.with_suffix(".xml")
        marcas = marcadores_do_video(banco, video.name)
        if marcas is None:
            print(f"NAO ESTA NO BANCO: {video.name}")
        elif not marcas:
            print(f"sem marcadores:    {video.name}")
        elif destino.exists() and not sobrescrever:
            print(f"ja existe, pulei:  {destino.name}")
        else:
            try:
                xml, usados = montar_xml(video, marcas)
            except (subprocess.CalledProcessError, StopIteration, ValueError, ZeroDivisionError) as erro:
                print(f"ERRO ao ler video: {video.name}: {erro}")
                continue
            destino.write_text(xml, encoding="utf-8")
            print(f"criado ({usados} marcadores): {destino.name}")
    if not args:
        input("\nPronto. Aperte Enter para fechar.")


if __name__ == "__main__":
    main()
