# Provider contracts and qualification

Checked 2026-10-23 against official documentation by an independent source reader.
These are protocol/model documentation facts, not authentication, entitlement or
live inference guarantees. A catalog response alone qualifies none of those.
Presets live in `ui-kit/providers.ts`; actual wire projection is in edition
`llm.py`, configuration projection in `localharness/boot.py`. Wire tests use the
real SDK with fake HTTP, separately from live consumer checks.

| Surface | Contract / current boundary | Official source |
|---|---|---|
| OpenAI direct | Responses, image input for GPT-5.4, typed reasoning/function output replay. Compatible endpoints use their configured Chat/Responses mode, not a guessed identity. | https://platform.openai.com/docs/guides/responses-vs-chat-completions ; https://platform.openai.com/docs/models/gpt-5.4 |
| Anthropic | Adaptive thinking and `output_config.effort` on current Claude; model-specific effort tiers. Manual thinking budgets are not a universal modern Claude contract. | https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking ; https://platform.claude.com/docs/en/build-with-claude/effort |
| Z.ai | GLM-5.3 is text-only, forced reasoning, low/high/max, default max. API/Coding key and historical compatible-key billing differ. Exact Anthropic effort field is not established merely by Chat documentation. | https://docs.z.ai/guides/llm/glm-5.3 ; https://docs.z.ai/guides/capabilities/thinking ; https://docs.z.ai/devpack/notice/api-key-management |
| MiniMax | M3.1-Flash/M3 image inputs; full thinking-block replay. M3.1 low/medium/high/xhigh/max, default max; M3 adaptive. | https://platform.minimax.io/docs/api-reference/text-anthropic-api |
| Kimi | Paid Messages endpoint documents `kimi-k3` and image input. This is not proof of the separate Coding endpoint/model `k3` alias or subscription entitlement. | https://platform.kimi.ai/docs/api/messages |
| DeepSeek | Anthropic-compatible image/thinking/tool input; low/high/max, default high, reasoning replay. | https://api-docs.deepseek.com/guides/anthropic_api ; https://api-docs.deepseek.com/guides/thinking_mode |
| Qwen | Workspace Messages `/apps/anthropic`, Qwen3.8-max image input; low/medium/xhigh, default xhigh. `/models` is not provided by that API. Coding plan endpoint and account remain separate qualification. | https://help.aliyun.com/zh/model-studio/anthropic-api-messages.md |
| Ollama | OpenAI-compatible Chat/vision/tools and stateless Responses depend on loaded model. Local address does not prove capability. | https://docs.ollama.com/api/openai-compatibility |
| LM Studio | OpenAI-compatible models/Chat/Responses; image/tool support depends on loaded model. | https://lmstudio.ai/docs/developer/openai-compat |
| ChatGPT embedded relay | Separate desktop control/two-slot product; preserve its exact source/binary pair and `--minimized --start-api --config` interface. Linux CLI and universal relay are not substitutes. | Distribution source manifest and relay API, not a vendor catalog claim |

`accepts_images` now includes the explicitly documented GPT-5.4, MiniMax M3.1/M3,
Kimi paid ID, DeepSeek Flash/V4 Pro and Qwen3.8-max IDs. Unqualified aliases/local
models are not silently called verified vision models; GLM-5.3 stays text-only.
Live configured-route image/tool tests must be distinguished from this static
knowledge and SDK wire conformance. The exact Kimi Coding/Qwen Coding plan and
Z.ai Anthropic effort serialization still need their own endpoint evidence.

Focused tests: `t_provider_config_192`, `t_provider_wire_192`,
`t_llm_transport_192`, `t_llm_image_delivery_192` and the provider Settings browser
fixture. Use separate fresh Python processes to avoid cross-module test state.
Keep requested/observed model and protocol distinct; do not replace a missing
provider check with a successful different-model or different-account call.
