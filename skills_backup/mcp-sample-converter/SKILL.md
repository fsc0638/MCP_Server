---
name: mcp-sample-converter
provider: mcp
version: 0.1.0
runtime_requirements: []
description: >
  Convert text data between formats. Accepts an input string and a target format,
  then returns the converted result. Supported conversions: text to uppercase,
  text to lowercase, text to title case, and word count analysis.
  Use this tool when users ask to transform, convert, or analyze text content.
parameters:
  type: object
  properties:
    input_text:
      type: string
      description: The text content to process.
    operation:
      type: string
      description: One of "uppercase", "lowercase", "titlecase", "wordcount".
  required: [input_text, operation]
---

# MCP Sample Converter

## Description
This tool converts text between various formats and provides basic text analysis.

## How to use (Strict Mode / Low Freedom)
- Input parameters:
  - input_text (string): The text content to process
  - operation (string): One of "uppercase", "lowercase", "titlecase", "wordcount"
