# Choose your AI

Configure the builder under **Admin > Builder Agent**, and AI steps separately under **Admin > Workflow AI providers**. Workflow AI steps cannot use your subscription: hosted providers need an API key, while local providers do not. You can use the OpenAI, Anthropic and Gemini presets, or add other compatible providers and separate keys.

Under **Authentication**, choose **Claude API key**, **Claude subscription**, **ChatGPT subscription (Codex)** or **OpenAI API key (Codex)**. Claude choices need Claude Code installed; OpenAI and ChatGPT choices need Codex, even with an API key. The tab shows the program's path and version, warns if it is too old and links to setup instructions; **Check connection** checks the sign-in.

The builder uses its own settings folder inside Cryogram's data without reading your personal Claude Code or Codex settings or writing into their chat histories. On Windows, Codex may need to use your own Codex folder instead.

Tick the builder model you want to use. With none selected, Cryogram uses the model Claude Code or Codex would run on its own. **Thinking effort** defaults to **Medium**; higher levels think longer before decisions, which can help with dependent steps or repeated problems. These changes apply from your next message.

You can add as many workflow providers as you need, including several entries for the same provider with different keys. Choose **+ Add provider**, then **OpenAI-compatible** under **API type** for providers such as xAI, Mistral, DeepSeek, Moonshot or OpenRouter; enter their endpoint, key and models.

Cryogram picks the cheapest available compatible model from the provider marked **Default**. Name a different model in the chat or select it in the step's panel. You can also change its temperature and token limit; a model that does not support temperature shows **Not available**.

On a provider's page, **Test** saves the settings and sends one real request to its cheapest model to check the connection. Hosted providers bill for usage separately.
