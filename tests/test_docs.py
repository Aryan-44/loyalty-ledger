"""Docs stay in sync with code: every column documented, every check ID real."""
import re
from dataclasses import fields
from pathlib import Path

from loyalty.anomalies import GroundTruth
from loyalty.export import TABLE_MODELS
from loyalty.validate import CHECKS

DOCS = Path(__file__).resolve().parents[1] / "docs"
DICTIONARY = (DOCS / "data_dictionary.md").read_text(encoding="utf-8")
MODEL = (DOCS / "data_model.md").read_text(encoding="utf-8")


def _documented_columns() -> dict[str, list[str]]:
    sections = re.split(r"^### `(\w+)`", DICTIONARY, flags=re.M)
    return {name: re.findall(r"^\| `(\w+)` \|", body, flags=re.M)
            for name, body in zip(sections[1::2], sections[2::2])}


def test_every_table_and_column_is_documented_in_order():
    documented = _documented_columns()
    expected = {name: model for name, model in TABLE_MODELS.items()}
    expected["ground_truth"] = GroundTruth
    assert set(documented) == set(expected)
    for name, model in expected.items():
        assert documented[name] == [f.name for f in fields(model)], name


def test_check_ids_in_dictionary_exist():
    real = {c[0] for c in CHECKS}
    cited = set(re.findall(r"\b(?:PK|FK|ID|EN|DT|AR|LC|DU)-\d{2}\b", DICTIONARY))
    assert cited and cited <= real, sorted(cited - real)


def test_er_diagram_covers_every_table():
    entities = set(re.findall(r"^    ([A-Z_]+) \{", MODEL, flags=re.M))
    assert entities == {name.upper() for name in TABLE_MODELS}
