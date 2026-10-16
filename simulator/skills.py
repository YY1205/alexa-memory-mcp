"""Agent Skills (agentskills.io SKILL.md format), loaded with progressive disclosure:

1. metadata (name + description) goes into every system prompt;
2. the SKILL.md body is added the first time a session uses the tools of the MCP
   server the skill is for (`metadata.mcp-server`);
3. reference files are read only when the model asks for them (read_skill_reference).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
READ_REFERENCE_TOOL = "read_skill_reference"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """The small YAML subset SKILL.md frontmatter uses: `key: value` lines and one
    level of indented `key: value` lines under `metadata:`. Returns (fields, body)."""
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter between --- lines")
    fields: dict = {}
    parent = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line.startswith((" ", "\t"))
        key, _, value = line.strip().partition(":")
        value = value.strip().strip('"').strip("'")
        if indented and parent:
            fields[parent][key.strip()] = value
        elif value:
            fields[key.strip()], parent = value, None
        else:
            fields[key.strip()], parent = {}, key.strip()
    return fields, match.group(2)


@dataclass
class Skill:
    name: str
    description: str
    root: Path
    metadata: dict

    @property
    def mcp_server(self) -> str | None:
        return self.metadata.get("mcp-server")

    def body(self) -> str:
        return parse_frontmatter((self.root / "SKILL.md").read_text(encoding="utf-8"))[1].strip()

    def references(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in (self.root / "references").glob("*.md"))

    def read_reference(self, path: str) -> str:
        """A reference file of this skill; nothing outside the skill folder."""
        if path not in self.references():
            raise FileNotFoundError(f"{self.name} has no reference {path!r}; available: {self.references()}")
        return (self.root / path).read_text(encoding="utf-8")


def load_skill(root: Path) -> Skill:
    fields, _ = parse_frontmatter((root / "SKILL.md").read_text(encoding="utf-8"))
    name, description = fields.get("name", ""), fields.get("description", "")
    if not _NAME.match(name) or len(name) > 64 or name != root.name:
        raise ValueError(f"invalid skill name {name!r} (must match folder {root.name!r})")
    if not 1 <= len(description) <= 1024:
        raise ValueError(f"skill {name}: description must be 1-1024 characters")
    return Skill(name, description, root, fields.get("metadata") or {})


def load_skills(directory: Path = SKILLS_DIR) -> list[Skill]:
    return [load_skill(d) for d in sorted(directory.iterdir()) if (d / "SKILL.md").is_file()] \
        if directory.is_dir() else []


def reference_tool_spec(skills: list[Skill]) -> dict:
    """Bedrock toolSpec for reading a skill's reference files on demand."""
    return {"toolSpec": {
        "name": READ_REFERENCE_TOOL,
        "description": "Read a reference file of a loaded skill (tool contracts, example dialogues). "
                       "Only when the skill's instructions are not enough.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": [s.name for s in skills]},
                "path": {"type": "string", "description": "e.g. references/TOOLS.md"},
            },
            "required": ["skill", "path"],
        }},
    }}
