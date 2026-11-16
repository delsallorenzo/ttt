from match import apply_rules

CTX = {"lavoro": [{"type": "domain", "value": "github.com"}]}
PRJ = {"work": [{"type": "domain", "value": "work.example"},
                 {"type": "path", "value": "work"},
                 {"type": "title_contains", "value": "work"}]}


def test_other_fallback():
    assert apply_rules({}, {}, {}) == ("other", "other")


def test_work_unifies_site_and_path():
    s1 = {"domain": "work.example", "finder_path": None, "app_name": "Dia",
          "title": "Work – home"}
    s2 = {"domain": None, "finder_path": "/Users/x/work/work/cut",
          "app_name": "After Effects", "title": "work_prj.aep"}
    assert apply_rules(s1, CTX, PRJ)[0] == "work"
    assert apply_rules(s2, CTX, PRJ)[0] == "work"


def test_project_beats_context():
    s = {"domain": "github.com", "finder_path": None, "app_name": "Dia",
         "title": "work backend"}
    assert apply_rules(s, CTX, PRJ) == ("work", "lavoro")


def test_profile_rule_wins_over_domain():
    prj = {"work": [{"type": "profile", "value": "Work"}],
           "personal": [{"type": "domain", "value": "instagram.com"}]}
    s = {"domain": "instagram.com", "profile": "work"}
    assert apply_rules(s, {}, prj)[0] == "work"
    assert apply_rules({"domain": "instagram.com", "profile": "Side Project"}, {}, prj)[0] == "personal"
    assert apply_rules({"profile": "Work 2"}, {}, prj)[0] == "other"  # uguaglianza, non sottostringa
