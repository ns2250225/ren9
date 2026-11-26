"""Skill 结构与多 Agent 兼容性：标准 SKILL.md、脚本可被发现、不依赖平台专属接口。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SUB_SKILLS = ["ren9-schedule", "football-data", "football-odds", "football-prediction", "football-risk",
              "ren9-selector", "ren9-optimizer", "ren9-review"]
SCRIPTS = ["fetch_schedule.py", "fetch_team_data.py", "fetch_odds.py", "calculate_probability.py",
           "calculate_elo.py", "dixon_coles.py", "calculate_risk.py", "select_matches.py", "optimize_bet.py",
           "generate_report.py", "review_results.py"]


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} 缺少 YAML frontmatter"
    return yaml.safe_load(m.group(1))


def test_main_skill_frontmatter():
    fm = frontmatter(ROOT / "SKILL.md")
    assert fm["name"] == "ren9-analyst"
    assert len(fm["description"]) > 50 and fm["license"] == "MIT"
    assert fm["metadata"]["version"] == "1.0.0"
    # 上传平台要求的顶层字段
    assert fm["version"] == "1.0.0"
    for key in ("display_name", "display_name_en", "description_zh", "description_en"):
        assert isinstance(fm[key], str) and fm[key].strip(), key
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"])


@pytest.mark.parametrize("name", SUB_SKILLS)
def test_sub_skill_is_self_describing(name):
    path = ROOT / "skills" / f"{name}.md"
    fm = frontmatter(path)
    assert fm["name"] == name and fm["description"]
    body = path.read_text(encoding="utf-8")
    for section in ("## 执行", "输出", "错误处理"):
        assert section in body, f"{name} 缺少 {section}"
    for script in re.findall(r"\.\./scripts/(\w+\.py)", body):      # 引用的脚本都存在
        assert (ROOT / "scripts" / script).exists(), f"{name} 引用了不存在的脚本 {script}"


def test_deliverables_exist():
    for rel in ["README.md", "LICENSE", "requirements.txt", "install.py", "mcp_server.py",
                "config/settings.yaml", "config/data_sources.yaml", "config/model_weights.yaml",
                "templates/daily_report.md", "templates/match_report.md",
                "providers/sporttery.py", "providers/clubelo.py", "providers/football_data.py",
                "providers/understat.py", "providers/espn.py",
                "models/elo.py", "models/poisson.py", "models/dixon_coles.py", "models/ensemble.py"]:
        assert (ROOT / rel).exists(), rel
    for s in SCRIPTS:
        assert (ROOT / "scripts" / s).exists(), s


def test_package_has_at_most_two_directory_levels():
    """Skill 包只允许 根目录/二级目录/文件：打包清单里不能有更深的路径。"""
    sys.path.insert(0, str(ROOT))
    import package

    files = package.collect()
    assert "SKILL.md" in files and "skills/ren9-schedule.md" in files and "scripts/generate_report.py" in files
    assert all(f.count("/") <= 1 for f in files), [f for f in files if f.count("/") > 1]
    assert not any(f.startswith("data/") for f in files)
    assert all(f in files for f in package.REQUIRED)
    assert "ren9/form.py" in files and "ren9/lineup.py" in files


def test_package_skips_backup_and_temp_files(tmp_path, monkeypatch):
    """备份 / 临时文件不进包；缺少核心文件时报错而不是打出残缺的包。"""
    sys.path.insert(0, str(ROOT))
    import package

    for rel in package.REQUIRED:
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    for junk in ("ren9/form.py.backup", "ren9/risk.py.bak", "ren9/predict.py.orig", "notes.tmp", "ren9/form.py~",
                 "run.log"):
        (tmp_path / junk).write_text("x", encoding="utf-8")
    monkeypatch.setattr(package, "ROOT", tmp_path)
    assert sorted(package.collect()) == sorted(package.REQUIRED)
    (tmp_path / "ren9" / "lineup.py").unlink()
    with pytest.raises(SystemExit, match="ren9/lineup.py"):
        package.collect()


def test_no_platform_specific_dependencies():
    """核心代码不能 import 任何 Agent 平台的专属 SDK。"""
    banned = re.compile(r"^\s*(import|from)\s+(openclaw|hermes|codex|cursor|claude_code|anthropic|openai)\b", re.M)
    for folder in ("ren9", "providers", "models", "scripts"):
        for f in (ROOT / folder).glob("*.py"):
            assert not banned.search(f.read_text(encoding="utf-8")), f
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    active = [ln for ln in reqs.splitlines() if ln.strip() and not ln.startswith("#")]
    assert not any(ln.startswith("mcp") for ln in active)          # MCP 是可选依赖


def test_settings_match_prd():
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert cfg["lottery"] == {"game_type": "ren9", "expected_matches": 14, "selected_matches": 9, "unit_price": 2}
    assert cfg["safety"]["never_invent_data"] and cfg["safety"]["stop_on_invalid_schedule"]
    assert cfg["data"]["require_official_schedule"] and cfg["data"]["allow_manual_schedule"]
    assert set(cfg["strategy"]["presets"]) == {"conservative", "balanced", "aggressive"}
    w = yaml.safe_load((ROOT / "config" / "model_weights.yaml").read_text(encoding="utf-8"))
    assert abs(sum(w["ensemble"]["initial"].values()) - 1) < 1e-9


def _cli(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, cwd=str(ROOT.parent), timeout=120)
    return r.returncode, r.stdout.decode("utf-8")


def test_cli_runs_from_any_directory_and_prints_json():
    """Agent 只需要能执行 Python 并读取 stdout 的 JSON。"""
    code, out = _cli(str(ROOT / "scripts" / "fetch_odds.py"), "--odds", "1.85,3.40,4.20")
    data = json.loads(out)
    assert code == 0 and data["implied_probability"]["home"] == 0.5405

    code, out = _cli(str(ROOT / "scripts" / "optimize_bet.py"), "--count", "3;31;10;3;310;0;3;31;0")
    data = json.loads(out)
    assert code == 0 and data["bet_count"] == 24 and data["total_cost"] == 48 and data["valid_ren9"]

    code, out = _cli(str(ROOT / "scripts" / "calculate_elo.py"), "--home-elo", "1750", "--away-elo", "1620")
    data = json.loads(out)
    assert code == 0 and data["elo_difference"] == 130.0
    assert abs(sum(data["probabilities"].values()) - 1) < 1e-3


def test_cli_errors_are_json():
    code, out = _cli(str(ROOT / "scripts" / "fetch_odds.py"), "--odds", "0.5,3,4")
    data = json.loads(out)
    assert code == 1 and data["status"] == "error" and "无效赔率" in data["message"]


def test_team_name_matching():
    from ren9.teams import TeamResolver, best_match, name_score

    assert name_score("Man City", "Manchester City") >= 0.9
    assert name_score("Sheffield Weds", "Sheffield Wednesday") >= 0.9
    assert name_score("AC Milan", "Milan") == 1.0
    assert name_score("Arsenal", "Chelsea") < 0.6
    teams = ["Arsenal", "Man City", "Man United", "Wolves", "Nott'm Forest"]
    assert best_match(["Manchester City"], teams) == "Man City"
    assert best_match(["Wolverhampton Wanderers", "Wolves"], teams) == "Wolves"
    assert best_match(["Real Madrid"], teams) is None
    r = TeamResolver()
    assert r.names_for("曼彻斯特城", "fd")[0] == "Man City"
    assert r.names_for("伍尔弗汉普顿")[0] == "Wolverhampton Wanderers"
    assert r.names_for("不存在的球队") == []
    r.learn("测试队", "Test FC", "fixture-pairing")
    assert TeamResolver().names_for("测试队") == ["Test FC"]
