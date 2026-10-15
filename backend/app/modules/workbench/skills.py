"""Small, read-only loader for local Agent Skills SKILL.md documents."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


_NAME = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_MAX_BYTES = 64 * 1024
_MAX_SKILLS = 64


class SkillRegistry:
    """Discover direct-child skills and load their body only on activation."""

    def __init__(self, root_path):
        self.root = Path(root_path)
        self.warnings: list[str] = []

    @staticmethod
    def _read(path: Path, expected_name: str, *, instructions: bool):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("invalid skill file")
        raw = path.read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ValueError("skill too large")
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        if not text.startswith("---\n"):
            raise ValueError("missing frontmatter")
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise ValueError("missing closing frontmatter")
        metadata = yaml.safe_load(text[4:closing])
        if not isinstance(metadata, dict):
            raise ValueError("invalid frontmatter")
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name != expected_name:
            raise ValueError("invalid skill name")
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
            raise ValueError("invalid skill description")
        body = text[closing + 5 :].strip()
        if not body:
            raise ValueError("missing skill instructions")
        entry = {"name": name, "description": description.strip()}
        if instructions:
            entry["instructions"] = body
        return entry

    def _discover(self):
        self.warnings = []
        if self.root.is_symlink():
            self.warnings.append("Skill 目录无效，已跳过。")
            return {}
        if not self.root.exists():
            return {}
        if not self.root.is_dir():
            self.warnings.append("Skill 目录无效，已跳过。")
            return {}
        found = {}
        try:
            children = sorted(self.root.iterdir(), key=lambda item: item.name)
        except OSError:
            self.warnings.append("Skill 目录无法读取。")
            return {}
        if len(children) > _MAX_SKILLS:
            self.warnings.append("Skill 数量超过限制，多余条目已跳过。")
        for child in children[:_MAX_SKILLS]:
            if not _NAME.fullmatch(child.name):
                continue
            if child.is_symlink() or not child.is_dir():
                self.warnings.append("已跳过无效 Skill。")
                continue
            candidate = child / "SKILL.md"
            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                found[child.name] = self._read(candidate, child.name, instructions=False)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                self.warnings.append("已跳过无效 Skill。")
        return found

    def catalog(self):
        return [
            {"name": entry["name"], "description": entry["description"]}
            for entry in self._discover().values()
        ]

    def load(self, name):
        from .retired_capabilities import check
        check(name)
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Skill 名称无效。")
        if name not in self._discover():
            raise ValueError("Skill 不存在或无法加载。")
        try:
            entry = self._read(self.root / name / "SKILL.md", name, instructions=True)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            raise ValueError("Skill 不存在或无法加载。") from None
        return {"name": entry["name"], "instructions": entry["instructions"]}
