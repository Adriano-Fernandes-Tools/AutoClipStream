"""Sistema de plugins do AutoClipStream (inspirado no ColinGPT9/clips-studio).

Permite criar detectores modulares para analisar blocos de live:
- Detectores de visão multimodal (ex: Gemma 3 Vision)
- Detectores de OCR de tela (ex: Windows Media OCR)
- Detectores de eventos específicos por jogo (ex: GTA, Valorant, Fortnite)
"""

from __future__ import annotations

import importlib
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseDetectorPlugin(ABC):
    """Classe base para plugins de detecção de momentos em blocos."""

    name: str = "base_detector"
    description: str = "Detector base"
    enabled: bool = True

    @abstractmethod
    def analyze(
        self,
        canal: str,
        video_path: Path,
        frames: list[Path],
        transcript_text: str,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Analisa o bloco e retorna lista de momentos encontrados.

        Cada momento é um dicionário:
        {
            "segundo": float,
            "score": float,
            "tipo": str,
            "descricao": str,
        }
        """
        raise NotImplementedError


class PluginManager:
    """Carrega e executa plugins disponíveis na pasta plugins/."""

    def __init__(self, plugins_dir: Path | None = None) -> None:
        self.plugins_dir = plugins_dir or Path(__file__).resolve().parent
        self.plugins: list[BaseDetectorPlugin] = []
        self._load_plugins()

    def _load_plugins(self) -> None:
        self.plugins.clear()
        if not self.plugins_dir.exists():
            return

        for py_file in self.plugins_dir.glob("*.py"):
            if py_file.name.startswith(("_", "base", "manager")):
                continue
            module_name = f"plugins.{py_file.stem}"
            try:
                if str(self.plugins_dir.parent) not in sys.path:
                    sys.path.insert(0, str(self.plugins_dir.parent))
                mod = importlib.import_module(module_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseDetectorPlugin)
                        and attr is not BaseDetectorPlugin
                    ):
                        plugin_instance = attr()
                        if plugin_instance.enabled:
                            self.plugins.append(plugin_instance)
            except Exception as exc:
                print(f"[PLUGINS] [AVISO] Falha ao carregar plugin {py_file.name}: {exc}", file=sys.stderr)

    def run_all(
        self,
        canal: str,
        video_path: Path,
        frames: list[Path],
        transcript_text: str,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Executa todos os plugins ativos e agrega os momentos detectados."""
        all_moments: list[dict[str, Any]] = []
        for plugin in self.plugins:
            try:
                found = plugin.analyze(
                    canal=canal,
                    video_path=video_path,
                    frames=frames,
                    transcript_text=transcript_text,
                    config=config,
                )
                if found:
                    all_moments.extend(found)
            except Exception as exc:
                print(f"[PLUGINS] [AVISO] Erro no plugin {plugin.name}: {exc}", file=sys.stderr)
        return all_moments
