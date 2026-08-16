# GTM Signal

Dependency-free MVP for evidence-first account research.

```sh
python3 server.py
```

Open `http://localhost:8000`. The API is `POST /api/analyze-account` with `company_url`, `target_persona`, `our_product`, and `icp`.

Without an API key, GTM Signal uses an evidence-only local fallback: it fetches the public company page, cites only extracted source text, and labels its hypothesis as an inference. To enable strict JSON-schema LLM reasoning, set `OPENROUTER_API_KEY`. Set `TAVILY_API_KEY` to add three targeted, cleaned public web searches; without it, the existing website-only research remains active.
