"""Helper pour charger ``extract_rapports_markdown.py`` comme module.

Le script extracteur n'est pas un package installable : il vit dans
``scripts/`` avec un en-tête PEP 723 pour ``uv run``. Les tests ont
besoin d'importer ses fonctions ; on utilise ``importlib.util`` pour
le charger à partir de son chemin disque, puis on l'enregistre dans
``sys.modules`` pour que ``dataclass`` résolve correctement
``cls.__module__`` au moment de la décoration.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_MODULE_NAME = "extract_rapports_markdown"
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "extract_rapports_markdown.py"

_AUDIT_MODULE_NAME = "audit_coordinates"
_AUDIT_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "audit_coordinates.py"

_FICHES_MODULE_NAME = "construire_fiches"
_FICHES_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "construire_fiches.py"


def load_extractor() -> ModuleType:
    """Charge (ou retourne depuis le cache) le module extracteur."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"impossible de charger le module {_MODULE_NAME} "
            f"depuis {_SCRIPT_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    # Enregistre AVANT exec_module : @dataclass(slots=True) inspecte
    # ``sys.modules[cls.__module__]`` pendant le traitement et plante
    # avec un AttributeError si le module n'y est pas encore.
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def load_audit_coordinates() -> ModuleType:
    """Charge (ou retourne depuis le cache) le module audit_coordinates.

    L'import de ``requests`` dans audit_coordinates.py est lazy
    (à l'intérieur de ``post_with_retry``), ce qui permet aux tests
    d'exercer les fonctions pures sans installer requests dans le
    Python système — voir la docstring de audit_coordinates.py.
    """
    if _AUDIT_MODULE_NAME in sys.modules:
        return sys.modules[_AUDIT_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_AUDIT_MODULE_NAME, _AUDIT_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"impossible de charger le module {_AUDIT_MODULE_NAME} "
            f"depuis {_AUDIT_SCRIPT_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_AUDIT_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def load_construire_fiches() -> ModuleType:
    """Charge (ou retourne depuis le cache) le module construire_fiches.

    L'import de ``duckdb`` et ``jsonschema`` est lazy (à l'intérieur
    de ``write_parquet`` et ``validate_rows``), ce qui permet aux tests
    d'exercer les fonctions pures sans installer ces deps.
    """
    if _FICHES_MODULE_NAME in sys.modules:
        return sys.modules[_FICHES_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        _FICHES_MODULE_NAME, _FICHES_SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"impossible de charger le module {_FICHES_MODULE_NAME} "
            f"depuis {_FICHES_SCRIPT_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_FICHES_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
