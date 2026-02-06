---
name: mcp-my-first-tool
provider: mcp
version: 0.1.0
runtime_requirements:
  - # Add dependencies here (e.g. pandas, python-docx)
description: >
  Provide a detailed description of the skill here. 
  The LLM will use this to decide whether to trigger this skill.
---

# Mcp My First Tool

## Description
[LLM Trigger Decider]
This skill is designed to...

## How to use (Strict Mode / Low Freedom)
- This tool should be called using relative paths to the `Scripts/` directory.
- Input parameters are restricted to:
  - param1 (type): description

## Execution Flow
1. Read Metadata
2. (Optional) Read References
3. Execute Script
4. Process Assets/Templates

## Input Boundary Checking
- [ ] Param check implemented in script
