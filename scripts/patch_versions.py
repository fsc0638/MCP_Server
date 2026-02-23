"""
Batch-patch SKILL.md files: add version: "1.0.0" to any missing the field.
Safe: only modifies the YAML frontmatter block, preserves everything else.
"""
import os
import yaml
from pathlib import Path

SKILLS_HOME = Path(r"C:\Users\kicl1\OneDrive\文件\研發組專案\MCP_Server\Agent_skills\skills")
PATCHED = 0
SKIPPED = 0

for skill_dir in sorted(SKILLS_HOME.iterdir()):
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        continue

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        print(f"  SKIP (no frontmatter): {skill_dir.name}")
        SKIPPED += 1
        continue

    parts = content.split("---", 2)
    if len(parts) < 3:
        print(f"  SKIP (malformed): {skill_dir.name}")
        SKIPPED += 1
        continue

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        print(f"  SKIP (YAML error): {skill_dir.name} — {e}")
        SKIPPED += 1
        continue

    if "version" in meta:
        print(f"  OK (has version {meta['version']}): {skill_dir.name}")
        SKIPPED += 1
        continue

    # Inject version as first field after name
    new_yaml_lines = []
    added = False
    for line in parts[1].splitlines():
        new_yaml_lines.append(line)
        if line.startswith("name:") and not added:
            new_yaml_lines.append('version: "1.0.0"')
            added = True

    if not added:
        new_yaml_lines.insert(0, 'version: "1.0.0"')

    new_content = "---\n" + "\n".join(new_yaml_lines) + "\n---" + parts[2]
    skill_md.write_text(new_content, encoding="utf-8")
    print(f"  PATCHED: {skill_dir.name}")
    PATCHED += 1

print(f"\nDone: {PATCHED} patched, {SKIPPED} skipped.")
