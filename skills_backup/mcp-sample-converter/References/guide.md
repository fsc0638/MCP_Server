# MCP Sample Converter — Reference Document

This is a sample reference document for the mcp-sample-converter skill.

## Supported Formats
- TXT to JSON
- CSV to JSON
- Markdown to HTML

## Conversion Rules
1. All input files must be UTF-8 encoded
2. Maximum input size: 10MB
3. Output format defaults to JSON unless specified

## Error Codes
| Code | Description |
|------|-------------|
| E001 | File not found |
| E002 | Unsupported format |
| E003 | File too large |
| E004 | Encoding error |
