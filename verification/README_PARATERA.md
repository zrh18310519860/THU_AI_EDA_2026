# Paratera Model Configuration

The official quick-start guide is https://ai.paratera.com/document/llm/quickStart/useApi.
The platform frontend's Paratera configuration identifies `https://llmapi.paratera.com`
as its LLM gateway. The OpenAI-compatible client therefore uses:

- Base URL: `https://llmapi.paratera.com/v1`
- Exact model ID: `DeepSeek-V4.1-Flash-1017`
- Model discovery: `GET /v1/models`
- Inference: `POST /v1/chat/completions`
- Authentication: `Authorization: Bearer <local key>`

The new credential was verified against model discovery on November 2, 2026.
Two real streaming probes returned the requested model ID and complete usage:
10 tokens with thinking disabled; 46 tokens with thinking enabled at low effort.
Probe parameters, timing and usage are in `F:/AI_EDA/paratera-20261102/connectivity.json`.
These probes verify connectivity, not L3 functionality.

`ai_eda/model/client.py` defaults to this gateway/model. `AI_EDA_BASE_URL` and
`AI_EDA_MODEL` can override them before starting the Python process. The existing
local `.api_key` remains the credential source and is excluded from Git; never put
the key into run artifacts or submission packages.

Streaming requests set `stream_options.include_usage=true`. DeepSeek thinking is
controlled with `thinking.type=enabled/disabled`; enabled requests use
`reasoning_effort=low` and omit temperature. Both forms were tested on this gateway.
Every model call stores its actual parameters, usage and elapsed time. Missing
usage remains unknown. Global options can differ from the subagent overrides.

Fresh L3 runs start with an empty project and retain model-generation provenance.
Resume runs retain `generation_mode=resumed` and their source run ID. Only a real
functional and PPA acceptance can be reported as L3 success.
