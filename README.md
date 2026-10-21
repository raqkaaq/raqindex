# raqindex

An unofficial async Python client for OpenAlex, with schema-generated Pydantic
models and endpoint methods, OQO queries, retries, and persistent HTTP caching.

Requires Python 3.14+ and uv. From the repository directory:

```bash
uv sync --group dev
cp example.env .env
```

Set your OpenAlex API key and email in `.env`, then generate the schemas and
endpoint metadata required by the client:

```bash
uv run python scripts/generate_schema.py
```

Use the client inside an async function or notebook:

```python
from raqindex import OpenAlexClient

async with OpenAlexClient() as client:
    response = await client.works(search="climate change", per_page=5)
    for work in response.results or []:
        print(work.display_name)
```

`client.query(oqo)` validates and translates an OQO without executing it;
`client.oqo(oqo)` executes it. Calculation response parsing is still incomplete.

Run tests with `uv run pytest tests`.
