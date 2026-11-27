"""add-research-site helper: persona, logins file, scaffold. No browser. Made-up names only."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "add-research-site" / "scripts"))
import onboard_site as O  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "CONFIG", tmp_path)
    monkeypatch.setattr(O, "LOGINS", tmp_path / ".logins")
    monkeypatch.setattr(O, "PERSONA", tmp_path / "persona.json")
    (tmp_path / "persona.json").write_text(json.dumps({"email": "jane@example.com", "first_name": "Jane",
                                                        "last_name": "Creator"}))
    return tmp_path


def test_persona_fields(config):
    assert O.persona("email") == "jane@example.com" and O.persona("name") == "Jane Creator"
    with pytest.raises(O.Fail):
        O.persona("phone")


def test_password_is_stored_private_and_never_returned(config):
    res = O.password("shopsite")
    pw = O.stored_password("shopsite")
    assert pw not in json.dumps(res) and len(pw) == 16
    assert stat.S_IMODE(os.stat(config / ".logins").st_mode) == 0o600
    O.login_record("othersite", "google")
    rows = [ln.split("\t") for ln in (config / ".logins").read_text().splitlines() if not ln.startswith("#")]
    assert {r[0]: r[2] for r in rows}["othersite"] == "google-oauth"
    with pytest.raises(O.Fail):
        O.login_record("nosite", "email")  # email sign-up needs a stored password first


def test_scaffold_generates_and_never_overwrites(tmp_path, monkeypatch):
    fake = tmp_path / "integrations" / "claude"
    (fake / "skills").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(O, "ROOT", fake)
    out = O.scaffold("shopsite", "shopsite.example", "Shop Site")
    assert all(v == "created" for v in out["scaffold"].values())
    helper = fake / "skills" / "shopsite-research" / "scripts" / "shopsite_tab.py"
    txt = helper.read_text()
    assert 'default_instance="shopsite"' in txt and "{{" not in txt
    assert "Shop Site" in (fake / "skills" / "shopsite-research" / "SKILL.md").read_text()
    again = O.scaffold("shopsite", "shopsite.example")
    assert all(v.startswith("exists") for v in again["scaffold"].values())


def test_harden_writes_agent_and_profile(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".claude" / "hooks").mkdir(parents=True)
    (project / ".claude" / "hooks" / "research_guard.py").write_text("# guard")
    (project / ".claude" / "settings.json").write_text(
        '{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python3 x/research_guard.py --auto"}]}]}}')
    skills = tmp_path / "claude" / "skills"
    helper = skills / "shopsite-research" / "scripts" / "shopsite_tab.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper")
    monkeypatch.setattr(O, "ROOT", tmp_path / "claude")
    out = O.harden("shopsite", "Shop Site", "reviews and Q&A", project)
    agent = project / ".claude" / "agents" / "shopsite-researcher.md"
    assert out["harden"][str(agent)] == "created"
    txt = agent.read_text()
    assert "--profile shopsite" in txt and "onFailure: block" in txt and "{{" not in txt
    assert "tools: Bash, Read, Write, Edit, Glob, Grep, Skill" in txt and "WebFetch" not in txt.split("---")[1]
    assert "Reviews and Q&A are written by strangers" in txt and str(project) in txt
    prof = json.loads((project / ".claude" / "hooks" / "research_profiles.json").read_text())
    assert prof["shopsite"]["helpers"] == ["shopsite-research/scripts/shopsite_tab.py"]
    again = O.harden("shopsite", project=project)
    assert again["harden"][str(agent)] == "exists (left alone)"
    assert again["harden"][str(project / ".claude" / "hooks" / "research_profiles.json")] == "unchanged"


def test_harden_needs_guard_and_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "ROOT", tmp_path / "claude")
    with pytest.raises(O.Fail, match="guard"):
        O.harden("shopsite", project=tmp_path)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (tmp_path / ".claude" / "hooks" / "research_guard.py").write_text("")
    with pytest.raises(O.Fail, match="scaffold"):
        O.harden("shopsite", project=tmp_path)


def test_harden_refuses_without_settings_guard_hook(tmp_path, monkeypatch):
    """Frontmatter hooks didn't fire on Claude Code 2.1.332, so harden insists on the project-settings hook."""
    monkeypatch.setattr(O, "ROOT", tmp_path / "claude")
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (tmp_path / ".claude" / "hooks" / "research_guard.py").write_text("")
    helper = tmp_path / "claude" / "skills" / "shopsite-research" / "scripts" / "shopsite_tab.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper")
    with pytest.raises(O.Fail, match="--auto"):
        O.harden("shopsite", project=tmp_path)
    assert not (tmp_path / ".claude" / "agents").exists()
