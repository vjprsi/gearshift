import json
import os
import re
import unittest

from tests.helpers import ROOT

from gearshift.claude_code import HANDLERS, _BRIEF
from gearshift.policy import EFFORTS, TIERS, load_policy


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert m, "%s has no frontmatter" % path
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm, m.group(2)


class PluginFiles(unittest.TestCase):
    def test_manifests_parse(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
            plugin = json.load(f)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as f:
            market = json.load(f)
        self.assertEqual(plugin["name"], "gearshift")
        self.assertEqual(market["plugins"][0]["name"], "gearshift")
        for opt in plugin["userConfig"].values():
            self.assertTrue({"type", "title", "description"} <= set(opt))
            self.assertTrue(set(opt) <= {"type", "title", "description", "default", "required"},
                            "keep to userConfig keys every recent Claude Code version accepts")
        import gearshift
        self.assertEqual(plugin["version"], gearshift.__version__)

    def test_hooks_reference_real_handlers_and_files(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "PreToolUse", "PostToolUse", "SubagentStop",
                                      "UserPromptSubmit", "PreModelSwitch", "PostModelSwitch"})
        seen = set()
        for event, groups in hooks.items():
            for g in groups:
                for h in g["hooks"]:
                    m = re.fullmatch(r'sh "\$\{CLAUDE_PLUGIN_ROOT\}/bin/gearshift" hook ([a-z\-]+)', h["command"])
                    self.assertTrue(m, h["command"])
                    self.assertIn(m.group(1), HANDLERS)
                    seen.add(m.group(1))
                    self.assertLessEqual(h.get("timeout", 600), 30)
        self.assertEqual(seen, set(HANDLERS))
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Agent|Task")
        self.assertTrue(os.access(os.path.join(ROOT, "bin", "gearshift"), os.X_OK))

    def test_agents_are_pinned_consistently_with_the_policy(self):
        pol = load_policy(use_user_files=False)
        expect = {"scout": "lookup", "sorter": "extract", "fixer": "edit", "reviewer": "review",
                  "builder": "build", "architect": "design", "prover": "frontier"}
        agents_dir = os.path.join(ROOT, "agents")
        self.assertEqual({f[:-3] for f in os.listdir(agents_dir)}, set(expect))
        for name, cls in expect.items():
            fm, body = frontmatter(os.path.join(agents_dir, name + ".md"))
            self.assertEqual(fm["name"], name)
            self.assertIn(fm["model"], TIERS)
            self.assertIn(fm["effort"], EFFORTS)
            self.assertEqual((fm["model"], fm["effort"]), (pol.classes[cls]["tier"], pol.classes[cls]["effort"]),
                             "%s must match the policy's %s class" % (name, cls))
            self.assertGreater(len(fm["description"]), 40)
            self.assertGreater(len(body), 300)
            self.assertIn("gearshift:%s" % name, _BRIEF)
        for name in ("scout", "reviewer", "architect"):
            fm, _ = frontmatter(os.path.join(agents_dir, name + ".md"))
            self.assertNotIn("Edit", fm["tools"])
            self.assertNotIn("Write", fm["tools"])

    def test_skills(self):
        skills = os.path.join(ROOT, "skills")
        self.assertEqual(set(os.listdir(skills)), {"routing", "route", "report", "doctor"})
        for name in os.listdir(skills):
            fm, body = frontmatter(os.path.join(skills, name, "SKILL.md"))
            self.assertEqual(fm["name"], name)
            self.assertNotIn("claude", fm["name"])
            self.assertLess(len(fm["description"]), 1024)
        fm, body = frontmatter(os.path.join(skills, "routing", "SKILL.md"))
        pol = load_policy(use_user_files=False)
        ladder = " → ".join("%s@%s" % (t, e) for t, e in pol.ladder)
        self.assertIn(ladder, body, "the skill's ladder must match the policy's")

    def test_no_stray_claude_md_at_plugin_root(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "CLAUDE.md")),
                         "a CLAUDE.md at a plugin root is not loaded and fails validation")


if __name__ == "__main__":
    unittest.main()
