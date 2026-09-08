import pathlib
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_conformance_shadow_model_baked_default():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text())
    assert cfg["conformance"]["shadow_model"] == "claude-fable-5-1"


def test_conformance_block_unrelated_keys_unchanged():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text())
    conf = cfg["conformance"]
    assert conf["enabled"] is True
    assert conf["max_reconcile_cycles"] == 3
    assert conf["block_on_material"] is True
