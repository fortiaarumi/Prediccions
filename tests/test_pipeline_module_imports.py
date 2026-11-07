"""
tests/test_pipeline_module_imports.py
=====================================
Test de regressió per verificar la importació neta i l'avaluació de tipus
de tots els mòduls requerits pel workflow de GitHub Actions (Run #117 / Step 3.5).
Garanteix compatibilitat total entre Python 3.10, 3.11, 3.12 (GitHub Actions) i 3.14 (Local).
"""

import importlib
import inspect
import typing
import pytest

PIPELINE_MODULES = [
    "database.db_manager",
    "engine.combo_bet_engine",
    "engine.combo_tracker",
    "engine.jornada_predictor",
    "engine.match_predictor",
    "engine.pdf_report_generator",
    "engine.value_bet_engine",
    "engine.web_data_exporter",
    "engine.rank_reconstructor",
    "scraper.jornada_resolver",
    "scraper.live_crawler",
    "scraper.winamax_scraper",
    "scraper.bet365_scraper",
]


@pytest.mark.parametrize("module_name", PIPELINE_MODULES)
def test_pipeline_module_imports_cleanly(module_name):
    """Verifica que cada mòdul del pipeline s'importa sense errors de dependència o de sintaxi."""
    mod = importlib.import_module(module_name)
    assert mod is not None, f"El mòdul {module_name} ha de poder-se importar"


@pytest.mark.parametrize("module_name", PIPELINE_MODULES)
def test_pipeline_module_type_annotations_evaluate(module_name):
    """
    Verifica que les anotacions de tipus s'avaluen correctament.
    A Python 3.12 (utilitzat a GitHub Actions), les anotacions de tipus s'avaluen
    durant la càrrega del mòdul tret que s'utilitzi `from __future__ import annotations`.
    Aquest test força l'avaluació amb typing.get_type_hints per detectar qualsevol símbol no importat.
    """
    mod = importlib.import_module(module_name)
    for name, obj in inspect.getmembers(mod):
        # Només auditem funcions i classes definides directament en aquest mòdul
        if inspect.isfunction(obj) and getattr(obj, "__module__", None) == module_name:
            try:
                typing.get_type_hints(obj)
            except NameError as exc:
                pytest.fail(f"Anotació de tipus invàlida a {module_name}.{name}: {exc}")
        elif inspect.isclass(obj) and getattr(obj, "__module__", None) == module_name:
            for m_name, meth in inspect.getmembers(obj, predicate=inspect.isroutine):
                # Verificar únicament mètodes del propi mòdul/classe per evitar classes base de tercers
                if getattr(meth, "__module__", None) == module_name:
                    try:
                        typing.get_type_hints(meth)
                    except NameError as exc:
                        pytest.fail(f"Anotació de tipus invàlida a {module_name}.{obj.__name__}.{m_name}: {exc}")


def test_classify_corner_market_iterable_annotation():
    """Test específic de regressió per a la correcció de 'Iterable' a ValueBetEngine.classify_corner_market."""
    from engine.value_bet_engine import ValueBetEngine
    hints = typing.get_type_hints(ValueBetEngine.classify_corner_market)
    assert "available_keys" in hints
    assert "return" in hints
