"""LLM backends — one per agent, assigned in config.

Three backend types:
- ollama:     Local LLM via Ollama HTTP API (Gemma4, etc.)
- claude_cli: Claude Code CLI subprocess (subscription bridge)
- api:        Direct HTTP to any provider (DeepSeek, OpenAI, Anthropic, etc.)
"""
