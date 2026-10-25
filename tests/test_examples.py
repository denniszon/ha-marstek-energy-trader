"""The example dashboard files are valid and the status card renders."""

from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
import pytest
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .test_init import NIGHT, _start

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")), ids=lambda p: p.name)
def test_example_is_valid_yaml(path: Path) -> None:
    assert yaml.safe_load(path.read_text())


async def test_status_card_renders(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start(hass, freezer, NIGHT)
    card = yaml.safe_load((EXAMPLES / "status-card.yaml").read_text())
    text = Template(card["content"], hass).async_render(parse_result=False)
    assert "🔋 Laden" in text
    assert "Proefdraaien" in text
    # Header row, separator and 12 slots.
    assert text.count("\n| ") >= 13
