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
uv run python -m raqindex.codegen
```

The module runs the generator class (requires the dev dependencies):

```python
from raqindex.codegen import SchemaGenerator

SchemaGenerator().run()
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
`client.oqo(oqo)` executes it; `client.oql(text)` translates and executes OQL.
Calculation responses are modeled; automatic continuation is limited to entity pages.

See the [OQL cheatsheet (PDF)](docs/oql.pdf) for search syntax, conditions, semantic
search, entity references, and how to validate a sentence.

Run tests with `uv run pytest tests`.
