"""Generate models, endpoint metadata, and client wrappers from upstream schemas."""

import ast
import importlib
import inspect
import json
import keyword
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter as PathFormatter

import httpx2
from datamodel_code_generator import (
    DataModelType,
    DatetimeClassType,
    InputFileType,
    OpenAPIScope,
    generate,
)
from datamodel_code_generator.format import Formatter

from raqindex.client.environment import Environment

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

GENERAL_OPTIONS = {
    "output_model_type": DataModelType.PydanticV2BaseModel,
    "output_datetime_class": DatetimeClassType.Datetime,
    "formatters": [Formatter.BUILTIN],
    "use_schema_description": True,
    "reuse_model": False,
    "field_constraints": True,
    "snake_case_field": True,
    "deserialize_default_values": ["enum"],
}

# Exact search is accepted by the shared entity-list parser:
# https://github.com/ourresearch/openalex-elastic-api/blob/c973302ac3e236439e4d011e9bc60d40b632905e/core/params.py
EXACT_SEARCH_PARAMETER = {
    "type": "string", "description": "Search without stemming.",
}

# Additional search options specific to GET /works.
# https://help.openalex.org/api/searching/
# https://help.openalex.org/api/semantic-search/
WORKS_SEARCH_PARAMETERS = {
    "search.title": {
        "type": "string", "description": "Search work titles.",
    },
    "search.title.exact": {
        "type": "string", "description": "Search work titles without stemming.",
    },
    "search.title_and_abstract": {
        "type": "string", "description": "Search work titles and abstracts.",
    },
    "search.title_and_abstract.exact": {
        "type": "string", "description": "Search work titles and abstracts without stemming.",
    },
    "search.title_abstract_keywords": {
        "type": "string", "description": "Search work titles, abstracts, and keywords.",
    },
    "search.title_abstract_keywords.exact": {
        "type": "string", "description": "Search work titles, abstracts, and keywords without stemming.",
    },
    "search.semantic": {
        "type": "string",
        "description": (
            "Find works by semantic similarity. Input beyond 2,000 characters is "
            "truncated by the API. Returns at most 50 results; limited to one request "
            "per second. Cannot be combined with another search parameter."
        ),
    },
    "rerank": {
        "type": "boolean",
        "description": (
            "Rerank the first 100 search results. Requires relevance sorting; "
            "incompatible with group_by, sample, and search.semantic."
        ),
    },
}


class SchemaGenerator:
    """Generate models, endpoint metadata, and wrappers from upstream schemas."""

    def __init__(self, env=None, project_root=None):
        self.env = env if env is not None else Environment()
        self.project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
        self.models_dir = self.project_root / "src" / "raqindex" / "models"
        self.spec = None
        self.model_map = None
        self.endpoints = None
        self.models = None

    @property
    def wrappers_path(self):
        return self.project_root / "src" / "raqindex" / "client" / "generated_endpoints.py"

    @staticmethod
    def _read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path, data, indent=2):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=indent), encoding="utf-8")

    def _load_models(self, reload=False):
        if self.models is None or reload:
            module = importlib.import_module("raqindex.models.openalex")
            self.models = importlib.reload(module) if reload else module
        return self.models

    def run(self):
        """Generate artifacts in dependency order."""
        self.generate_models(
            "https://api.openalex.org/query/spec/oqo", "oqo.py",
            InputFileType.JsonSchema, self.env.oqo_spec_file_path,
            self.env.oqo_model_map_file_path,
        )
        self.generate_models(
            "https://help.openalex.org/openapi.json", "openalex.py",
            InputFileType.OpenAPI, self.env.openalex_spec_file_path,
            self.env.openalex_model_map_file_path,
        )
        self.generate_mapping()
        self.generate_wrappers()

    @staticmethod
    def supplement_schema(schema: dict) -> None:
        """Supplement supported operations and factor shared retrieval schemas."""
        def resolve(parameter):
            while "$ref" in parameter:
                target = schema
                for part in parameter["$ref"].removeprefix("#/").split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
                parameter = target
            return parameter

        for path, path_item in schema["paths"].items():
            operation = path_item.get("get")
            if operation is None or path.count("/") != 1 or "{" in path:
                continue
            parameters = operation.get("parameters", [])
            existing = {
                (resolved.get("in"), resolved.get("name"))
                for parameter in [*path_item.get("parameters", []), *parameters]
                for resolved in [resolve(parameter)]
            }
            additions = {}
            if ("query", "search") in existing or path == "/works":
                additions["search.exact"] = EXACT_SEARCH_PARAMETER
            if path == "/works":
                additions.update(WORKS_SEARCH_PARAMETERS)
            for name, definition in additions.items():
                if ("query", name) in existing:
                    continue
                definition = deepcopy(definition)
                description = definition.pop("description")
                operation.setdefault("parameters", []).append({
                    "name": name, "in": "query", "required": False,
                    "description": description, "schema": definition,
                })

        models = schema.setdefault("components", {}).setdefault("schemas", {})
        if "RetrievalParameters" in models:
            return

        properties = {}
        for parameter in schema["paths"]["/works"]["get"]["parameters"]:
            parameter = resolve(parameter)
            if parameter.get("name") in {"per_page", "page", "cursor", "select", "sort"}:
                properties[parameter["name"]] = deepcopy(parameter["schema"])

        models["RetrievalParameters"] = {
            "type": "object",
            "properties": properties,
        }

    @staticmethod
    def share_retrieval_parameters(output: Path) -> None:
        """Factor exact generated field matches into the generated shared base."""
        source = output.read_text(encoding="utf-8")
        classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
        base = next(node for node in classes if node.name == "RetrievalParameters")

        def fields(node):
            return {
                field.target.id: field for field in node.body
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
            }

        shared = fields(base)
        if not shared:
            raise ValueError("RetrievalParameters has no generated fields")
        lines = source.splitlines(keepends=True)
        edits = []
        for child in classes:
            if child is base or not child.name.endswith("Parameters"):
                continue
            if len(child.bases) != 1 or ast.unparse(child.bases[0]) != "BaseModel":
                continue
            own = fields(child)
            if not all(name in own for name in shared):
                continue
            # A changed default, constraint, alias, or annotation prevents factoring.
            if any(ast.dump(own[name]) != ast.dump(field) for name, field in shared.items()):
                continue
            edits.append((child.lineno - 1, child.lineno,
                          [lines[child.lineno - 1].replace("(BaseModel)", "(RetrievalParameters)")]))
            for name in shared:
                field = own[name]
                edits.append((field.lineno - 1, field.end_lineno, []))
            if len(child.body) == len(shared):
                edits.append((child.end_lineno, child.end_lineno, ["    pass\n"]))

        # Ensure the base is defined before all children, regardless of codegen order.
        base_lines = lines[base.lineno - 1:base.end_lineno] + ["\n\n"]
        edits.append((base.lineno - 1, base.end_lineno, []))
        edits.append((classes[0].lineno - 1, classes[0].lineno - 1, base_lines))
        for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
            lines[start:end] = replacement
        result = "".join(lines)
        ast.parse(result)
        output.write_text(result, encoding="utf-8")

    def fetch_schema(self, url):
        logger.info("Fetching schema from %s", url)
        response = httpx2.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def openapi_scopes(schema):
        components = schema.get("components", {})
        paths = schema.get("paths", {})
        operations = [
            operation for path in paths.values()
            if isinstance(path, dict)
            for method, operation in path.items() if method.lower() in {
                "get", "post", "put", "delete", "patch", "options", "head", "trace"
            }
            and isinstance(operation, dict)
        ]
        scopes = []
        if components.get("schemas"):
            scopes.append(OpenAPIScope.Schemas)
        if operations:
            scopes.append(OpenAPIScope.Paths)
            if any(path.get("parameters") for path in paths.values()
                   if isinstance(path, dict)) or any(
                       operation.get("parameters") for operation in operations
                ):
                logger.debug("OpenAPI specification contains parameters.")
                scopes.append(OpenAPIScope.Parameters)
            if any(operation.get("tags") for operation in operations):
                scopes.append(OpenAPIScope.Tags)
        if schema.get("webhooks"):
            scopes.append(OpenAPIScope.Webhooks)
        if components.get("requestBodies"):
            scopes.append(OpenAPIScope.RequestBodies)
        return scopes

    def generate_models(self, url, filename, input_type, schema_path, model_map_path):
        schema = self.fetch_schema(url)
        is_openapi = input_type == InputFileType.OpenAPI
        if is_openapi:
            self.supplement_schema(schema)
        self._write_json(schema_path, schema)
        model_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        output = self.models_dir / filename
        options = dict(GENERAL_OPTIONS)
        if is_openapi:
            options.update(openapi_scopes=self.openapi_scopes(schema), include_path_parameters=True)
        generate(
            input_=json.dumps(schema, indent=2), output=output,
            input_file_type=input_type, emit_model_metadata=model_map_path, **options,
        )
        if is_openapi:
            self.share_retrieval_parameters(output)
            self.spec = schema
            self.model_map = self._read_json(model_map_path)
            self.models = None
            self.endpoints = None
        logger.info("Models generated successfully in %s", output)

    def _response_definition(self, endpoint, method, status, response):
        """Resolve a schema response to its generated Python model."""
        if status == "200":
            body = response["content"]["application/json"]["schema"]
            source = body.get("$ref", f"#/paths/{endpoint.strip('/')}/{method}/responses/200/application/json")
            matched = next(
                item for item in self.model_map["models"]
                if item["source_ref"].split("#", 1)[-1] == source.split("#", 1)[-1]
            )
            name = matched["class_name"]
            return {"ref": name, "model": getattr(self.models, name)}
        name = response["$ref"].split("/")[-1]
        definition = self.spec["components"]["responses"][name]
        model_name = definition["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        return {"ref": name, "model": getattr(self.models, model_name)}

    def _build_endpoint(self, entry):
        from raqindex.models.endpoints import Endpoint

        path = entry["source_path"]
        method = path[-2].lower()
        endpoint = '/' + '/'.join(path[1:-2])
        operation = self.spec["paths"][endpoint][method]
        name = entry["class_name"]
        responses = {
            status: self._response_definition(endpoint, method, status, response)
            for status, response in operation["responses"].items()
        }
        return Endpoint.model_validate({
            "endpoint": endpoint,
            "method": {
                **operation, "method": method.upper(),
                "request": {"ref": name, "model": getattr(self.models, name)},
                "responses": responses,
            },
        })

    def generate_mapping(self):
        """Build endpoint metadata using this run's schema and model registry."""
        if self.spec is None:
            self.spec = self._read_json(self.env.openalex_spec_file_path)
        if self.model_map is None:
            self.model_map = self._read_json(self.env.openalex_model_map_file_path)
        methods = {method.upper() for path in self.spec["paths"].values() for method in path}
        assert methods == {"GET"}
        self._load_models(reload=True)
        endpoints = []
        for entry in self.model_map["models"]:
            path = entry.get("source_path") or []
            if not path or path[0] != "paths" or path[-1] != "parameters":
                continue
            try:
                endpoint = self._build_endpoint(entry)
            except Exception:
                logger.exception("Error resolving endpoint model %s", entry["class_name"])
                continue
            endpoints.append(endpoint.model_dump(mode="json", by_alias=True, exclude_none=True))
        self._write_json(self.env.endpoints_file_path, endpoints, indent=4)
        self.endpoints = endpoints
        logger.info("Endpoint mapping generated in %s", self.env.endpoints_file_path)

    def _group_routes(self):
        """Group list/detail routes under valid Python method names."""
        groups = {}
        for item in self.endpoints:
            path = item["endpoint"]
            parts = list(PathFormatter().parse(path))

            fixed = "".join(literal for literal, _, _, _ in parts)
            name = "_".join(fixed.strip("/").split("/")).replace("-", "_")

            if keyword.iskeyword(name):
                name += "_"
            if not name.isidentifier():
                raise ValueError(f"Cannot generate a method name for {path!r}")

            placeholders = tuple(
                field for _, field, _, _ in parts
                if field is not None
            )
            for field in placeholders:
                if (
                    not field.isidentifier()
                    or keyword.iskeyword(field)
                    or field in {"self", "params"}
                ):
                    raise ValueError(f"Unsupported path parameter {field!r}")

            groups.setdefault(name, []).append((path, placeholders, item))

        return groups

    def _wrapper_details(self, variants):
        parameter_details = []
        endpoint_details = []

        for path, _, item in variants:
            request_name = item["method"]["request"]["model"]
            request_model = getattr(self.models, request_name)

            endpoint_details.append(f"{path} -> {request_name}")
            parameter_details.append(f"{path}:")

            visible_fields = {
                field_name: field
                for field_name, field in request_model.model_fields.items()
                if field_name not in {"api_key", "mailto"}
            }

            if not visible_fields:
                parameter_details.append("  No caller-supplied parameters.")

            for field_name, field in visible_fields.items():
                annotation = inspect.formatannotation(field.annotation)
                default = (
                    " (required)"
                    if field.is_required()
                    else f" = {field.default!r}"
                )
                parameter_details.append(
                    f"  {field_name}: {annotation}{default}"
                )

            parameter_details.append("")

        parameters = "\n".join(parameter_details).rstrip()
        endpoint_summary = "\n".join(endpoint_details)

        return parameters, endpoint_summary

    def _render_wrapper(self, name, variants):
        lines = []
        logger.debug("Generating endpoint wrapper for %s", name)
        variants = sorted(variants, key=lambda variant: (len(variant[1]), variant[0]))

        fields = list(dict.fromkeys(
            field
            for _, placeholders, _ in variants
            for field in placeholders
        ))

        routes = {}
        for path, placeholders, _ in variants:
            key = tuple(field for field in fields if field in placeholders)
            if key in routes:
                raise ValueError(
                    f"Ambiguous routes for {name!r}: {routes[key]!r}, {path!r}"
                )
            routes[key] = path

        args = ", ".join([
            "self",
            *(f"{field}=None" for field in fields),
            "**params",
        ])
        lines.append(f"    async def {name}({args}):")

        if fields:
            values = ", ".join(f"{field!r}: {field}" for field in fields)
            lines.extend([
                f"        path_params = {{{values}}}",
                "        supplied = {",
                "            key: value for key, value in path_params.items()",
                "            if value is not None",
                "        }",
                f"        path = {routes!r}.get(tuple(supplied))",
                "        if path is None:",
                "            raise TypeError(",
                f'                "Unsupported path-parameter combination for {name}: "',
                '                + ", ".join(supplied)',
                "            )",
                "        params.update(supplied)",
                "        return await self.endpoint_factory(path)(**params)",
            ])
        else:
            lines.append(
                f"        return await self.endpoint_factory({variants[0][0]!r})(**params)"
            )

        parameters, endpoint_summary = self._wrapper_details(variants)

        lines.extend([
            f"    {name}.parameters = {parameters!r}",
            f"    {name}.endpoints = {endpoint_summary!r}",
            "",
        ])
        return lines

    def generate_wrappers(self):
        """Render wrappers from this run's endpoint metadata and model registry."""
        if self.endpoints is None:
            self.endpoints = self._read_json(self.env.endpoints_file_path)
        self._load_models()
        lines = [
            "# Generated by scripts/generate_schema.py",
            f"# Generated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "",
            "class GeneratedEndpoints:",
        ]
        for name, variants in sorted(self._group_routes().items()):
            lines.extend(self._render_wrapper(name, variants))
        if lines[-1] == "class GeneratedEndpoints:":
            lines.append("    pass")
        self.wrappers_path.parent.mkdir(parents=True, exist_ok=True)
        self.wrappers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Endpoint wrappers generated in %s", self.wrappers_path)


def main():
    """Run the generation pipeline with command-line logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    SchemaGenerator().run()


if __name__ == "__main__":
    main()
