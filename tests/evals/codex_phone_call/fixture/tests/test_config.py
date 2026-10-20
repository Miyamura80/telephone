from acme_cli.config import load_profile


def test_loads_region_from_profile(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[profile]\ntoken = "t"\nregion = "eu-west-1"\n')
    monkeypatch.setenv("ACME_CONFIG", str(cfg))
    monkeypatch.delenv("ACME_REGION", raising=False)
    assert load_profile() == {"token": "t", "region": "eu-west-1"}
