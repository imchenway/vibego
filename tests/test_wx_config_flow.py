import os
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


def test_wx_effective_config_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEGO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("WX_APPID", "ENV_APPID")
    monkeypatch.setenv("WX_PKP", "/abs/path/from/env/private.key")
    monkeypatch.setenv("PROJECT_PATH", "/abs/project")
    monkeypatch.setattr(bot, "PROJECT_NAME", "demo-wx")

    env_file = tmp_path / "wx_ci" / "demo-wx.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("WX_APPID=FILE_APPID\nWX_PKP=/from/file.key\nPROJECT_PATH=/from/file\n", encoding="utf-8")

    conf = bot._wx_effective_config()
    assert conf["appid"] == "ENV_APPID"
    assert conf["pkp_path"] == "/abs/path/from/env/private.key"
    assert conf["project_path"] == "/abs/project"
    assert conf["env_file"].endswith("demo-wx.env")


def test_wx_missing_reasons(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEGO_CONFIG_DIR", str(tmp_path))
    conf = {"appid": "", "pkp_path": "", "project_path": ""}
    reasons = bot._wx_missing_reasons(conf)
    assert any("WX_APPID" in item for item in reasons)
    assert any("WX_PKP" in item for item in reasons)

    missing_file_conf = {"appid": "appid", "pkp_path": "/non/exist.key", "project_path": ""}
    reasons = bot._wx_missing_reasons(missing_file_conf)
    assert any("/non/exist.key" in item for item in reasons)


def test_write_wx_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEGO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "PROJECT_NAME", "demo-wx")
    env_path = bot._write_wx_env_file("appid123", "/abs/key.key", "/abs/project")

    assert env_path.exists()
    content = env_path.read_text(encoding="utf-8")
    assert "WX_APPID=appid123" in content
    assert "WX_PKP=/abs/key.key" in content
    assert "PROJECT_PATH=/abs/project" in content
    mode = env_path.stat().st_mode & 0o777
    assert mode in {0o600, 0o644, 0o666}  # 允许 umask 影响，但确保可读写
