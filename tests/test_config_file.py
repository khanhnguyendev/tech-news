from pathlib import Path

from settings import category_order, gate_by_source, load_config

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def test_shipped_config_is_valid():
    config = load_config(CONFIG_PATH)
    assert len(config["sources"]) >= 20


def test_every_source_has_a_usable_target():
    for source in load_config(CONFIG_PATH)["sources"]:
        if source["type"] == "github_release":
            assert source.get("repo"), source["name"]
        else:
            assert source.get("url", "").startswith("http"), source["name"]


def test_html_sources_declare_required_selectors():
    for source in load_config(CONFIG_PATH)["sources"]:
        if source["type"] == "html":
            selectors = source.get("selectors") or {}
            for key in ("item", "title", "link"):
                assert selectors.get(key), f"{source['name']}.{key}"


def test_expected_categories_are_present():
    categories = set(category_order(load_config(CONFIG_PATH)))
    assert {"Anthropic", "YouTube", "Releases", "Apple", "Security"} <= categories


def test_events_source_uses_new_only_gate():
    gates = gate_by_source(load_config(CONFIG_PATH))
    assert gates["Anthropic Events"] == "new_only"
