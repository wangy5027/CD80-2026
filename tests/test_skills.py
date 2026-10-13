"""Agent Skills discovery, validation, and activation tests."""

import tempfile
import unittest
from pathlib import Path

from backend.app.modules.workbench.skills import SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-skills-")
        self.root = Path(self.temp.name) / "skills"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_skill(self, name, body="第一版操作。"):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 可用的新闻搜索流程。\n---\n\n{body}\n", encoding="utf-8"
        )
        return directory

    def test_catalog_and_activation_read_current_file(self):
        directory = self.write_skill("news-search")
        registry = SkillRegistry(self.root)
        self.assertEqual(registry.catalog(), [{"name": "news-search", "description": "可用的新闻搜索流程。"}])
        self.assertNotIn("instructions", registry.catalog()[0])
        self.assertEqual(registry.load("news-search"), {"name": "news-search", "instructions": "第一版操作。"})
        self.assertEqual(registry.warnings, [])
        (directory / "SKILL.md").write_text(
            "---\nname: news-search\ndescription: 可用的新闻搜索流程。\n---\n\n修改后操作。\n", encoding="utf-8"
        )
        self.assertEqual(registry.load("news-search")["instructions"], "修改后操作。")

    def test_rejects_links_traversal_bad_yaml_and_oversized_document(self):
        self.write_skill("valid-one")
        secret = Path(self.temp.name) / "private.txt"
        secret.write_text("do not read me", encoding="utf-8")
        linked_file = self.root / "linked-file"
        linked_file.mkdir()
        (linked_file / "SKILL.md").symlink_to(secret)
        (self.root / "linked-dir").symlink_to(self.root / "valid-one", target_is_directory=True)
        malformed = self.root / "bad-yaml"
        malformed.mkdir()
        (malformed / "SKILL.md").write_text("---\nname: [oops\n---\nbody", encoding="utf-8")
        large = self.root / "too-large"
        large.mkdir()
        (large / "SKILL.md").write_text("x" * (64 * 1024 + 1), encoding="utf-8")
        registry = SkillRegistry(self.root)
        self.assertEqual([item["name"] for item in registry.catalog()], ["valid-one"])
        with self.assertRaises(ValueError):
            registry.load("../private.txt")
        with self.assertRaises(ValueError):
            registry.load("linked-dir")
        self.assertTrue(registry.warnings)
        self.assertNotIn(str(self.root), " ".join(registry.warnings))
        self.assertNotIn("do not read me", " ".join(registry.warnings))

    def test_metadata_name_must_match_folder(self):
        directory = self.write_skill("alpha")
        (directory / "SKILL.md").write_text(
            "---\nname: beta\ndescription: wrong folder\n---\nbody", encoding="utf-8"
        )
        registry = SkillRegistry(self.root)
        self.assertEqual(registry.catalog(), [])

    def test_rejects_consecutive_hyphens_and_accepts_crlf(self):
        self.write_skill("bad--name")
        directory = self.root / "crlf-skill"
        directory.mkdir()
        (directory / "SKILL.md").write_bytes(
            b"---\r\nname: crlf-skill\r\ndescription: Windows line endings.\r\n---\r\n\r\nRun the tool.\r\n"
        )
        registry = SkillRegistry(self.root)
        self.assertEqual(registry.catalog(), [{"name": "crlf-skill", "description": "Windows line endings."}])
        self.assertEqual(registry.load("crlf-skill")["instructions"], "Run the tool.")
        with self.assertRaises(ValueError):
            registry.load("bad--name")


if __name__ == "__main__":
    unittest.main()
