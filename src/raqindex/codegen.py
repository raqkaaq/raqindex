"""OpenAlex customizations and orchestration for the shared generators."""

import ast
import importlib
import json
import logging
import sys
from types import ModuleType
from copy import deepcopy
from pathlib import Path

import httpx2
from datamodel_code_generator import InputFileType
from raq_helpers.codegen import ModelGenerator, EndpointGenerator, PackageGenerator

from raqindex.client.environment import Environment

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Exact search is accepted by the shared entity-list parser:
# https://github.com/ourresearch/openalex-elastic-api/blob/c973302ac3e236439e4d011e9bc60d40b632905e/core/params.py
EXACT_SEARCH_PARAMETER = {
    "type": "string",
    "description": "Search without stemming.",
}

# Additional search options specific to GET /works.
# https://help.openalex.org/api/searching/
# https://help.openalex.org/api/semantic-search/
WORKS_SEARCH_PARAMETERS = {
    "search.title": {
        "type": "string",
        "description": "Search work titles.",
    },
    "search.title.exact": {
        "type": "string",
        "description": "Search work titles without stemming.",
    },
    "search.title_and_abstract": {
        "type": "string",
        "description": "Search work titles and abstracts.",
    },
    "search.title_and_abstract.exact": {
        "type": "string",
        "description": "Search work titles and abstracts without stemming.",
    },
    "search.title_abstract_keywords": {
        "type": "string",
        "description": "Search work titles, abstracts, and keywords.",
    },
    "search.title_abstract_keywords.exact": {
        "type": "string",
        "description": "Search work titles, abstracts, and keywords without stemming.",
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


class OpenAlexModelGenerator(ModelGenerator):
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
                operation.setdefault("parameters", []).append(
                    {
                        "name": name,
                        "in": "query",
                        "required": False,
                        "description": description,
                        "schema": definition,
                    }
                )

        models = schema.setdefault("components", {}).setdefault("schemas", {})
        if "RetrievalParameters" in models:
            return

        properties = {}
        for parameter in schema["paths"]["/works"]["get"]["parameters"]:
            parameter = resolve(parameter)
            if parameter.get("name") in {
                "per_page",
                "page",
                "cursor",
                "select",
                "sort",
            }:
                properties[parameter["name"]] = deepcopy(parameter["schema"])

        models["RetrievalParameters"] = {
            "type": "object",
            "properties": properties,
        }

    @staticmethod
    def share_retrieval_parameters(output: Path) -> None:
        """Factor exact generated field matches into the generated shared base."""
        source = output.read_text(encoding="utf-8")
        classes = [
            node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
        ]
        base = next(node for node in classes if node.name == "RetrievalParameters")

        def fields(node):
            return {
                field.target.id: field
                for field in node.body
                if isinstance(field, ast.AnnAssign)
                and isinstance(field.target, ast.Name)
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
            if any(
                ast.dump(own[name]) != ast.dump(field) for name, field in shared.items()
            ):
                continue
            edits.append(
                (
                    child.lineno - 1,
                    child.lineno,
                    [
                        lines[child.lineno - 1].replace(
                            "(BaseModel)", "(RetrievalParameters)"
                        )
                    ],
                )
            )
            for name in shared:
                field = own[name]
                edits.append((field.lineno - 1, field.end_lineno, []))
            if len(child.body) == len(shared):
                edits.append((child.end_lineno, child.end_lineno, ["    pass\n"]))

        # Ensure the base is defined before all children, regardless of codegen order.
        base_lines = lines[base.lineno - 1 : base.end_lineno] + ["\n\n"]
        edits.append((base.lineno - 1, base.end_lineno, []))
        edits.append((classes[0].lineno - 1, classes[0].lineno - 1, base_lines))
        for start, end, replacement in sorted(
            edits, key=lambda edit: (edit[0], edit[1]), reverse=True
        ):
            lines[start:end] = replacement
        result = "".join(lines)
        ast.parse(result)
        output.write_text(result, encoding="utf-8")

    def postprocess_models(self, output):
        self.share_retrieval_parameters(output)


class OpenAlexEndpointGenerator(EndpointGenerator):
    RESERVED_WRAPPER_NAMES = EndpointGenerator.RESERVED_WRAPPER_NAMES | {
        "env",
        "api_key",
        "usage",
        "query",
        "oqo",
        "cost",
        "reconstruct_and_wrap_abstract",
    }

    def excluded_wrapper_parameters(self):
        return ("api_key", "mailto")


class SchemaGenerator:
    """Fetch OpenAlex schemas and run shared generators in dependency order."""

    supplement_schema = staticmethod(OpenAlexModelGenerator.supplement_schema)
    share_retrieval_parameters = staticmethod(
        OpenAlexModelGenerator.share_retrieval_parameters
    )

    def __init__(self, env=None, project_root=None):
        self.env = env if env is not None else Environment()
        self.project_root = (
            Path(project_root) if project_root is not None else PROJECT_ROOT
        )
        self.models_dir = self.project_root / "src" / "raqindex" / "generated"
        self.spec = None
        self.model_map = None
        self.endpoints = None
        self.models = None

    @property
    def wrappers_path(self):
        return (
            self.project_root / "src" / "raqindex" / "generated" / "endpoints.py"
        )

    @staticmethod
    def _read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _models_package(self):
        if (
            self.models_dir.resolve()
            == (PROJECT_ROOT / "src/raqindex/generated").resolve()
        ):
            name = "raqindex.generated"
        else:
            # Isolate temporary/custom outputs from installed model classes.
            name = f"_raqindex_generated_{id(self)}"
        if name not in sys.modules:
            # The previous initializer can reference classes removed by regeneration.
            # Load the new modules first; finalize the initializer after all stages.
            package = ModuleType(name)
            package.__path__ = [str(self.models_dir.resolve())]
            sys.modules[name] = package
        return name

    def _load_models(self, reload=False):
        if self.models is None or reload:
            name = f"{self._models_package()}.openalex"
            importlib.invalidate_caches()
            module = importlib.import_module(name)
            self.models = importlib.reload(module) if reload else module
        return self.models

    def _registry(self):
        self._load_models()
        module = importlib.import_module(f"{self._models_package()}.openalex_registry")
        return importlib.reload(module).MODEL_REGISTRY

    def fetch_schema(self, url):
        response = httpx2.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        return response.json()

    def generate_models(self, url, filename, input_type, schema_path, model_map_path):
        schema = self.fetch_schema(url)
        generator_type = (
            OpenAlexModelGenerator
            if input_type == InputFileType.OpenAPI
            else ModelGenerator
        )
        generator = generator_type(
            output_dir=self.models_dir,
            models_filename=filename,
            metadata_filename=str(model_map_path.resolve()),
        )
        prepared = generator.generate_models(schema, input_type)
        self._write_json(schema_path, prepared)
        if input_type == InputFileType.OpenAPI:
            self.spec = prepared
            self.model_map = self._read_json(model_map_path)
            self.models = None
            self.endpoints = None

    def _endpoint_generator(self):
        return OpenAlexEndpointGenerator(
            schema=self.spec if self.spec is not None else {},
            metadata=self.model_map if self.model_map is not None else {"models": []},
            registry=self._registry(),
            output_dir=self.models_dir,
            endpoints_filename=str(self.env.endpoints_file_path.resolve()),
            wrappers_filename=str(self.wrappers_path.resolve()),
        )

    def generate_mapping(self):
        if self.spec is None:
            self.spec = self._read_json(self.env.openalex_spec_file_path)
        if self.model_map is None:
            self.model_map = self._read_json(self.env.openalex_model_map_file_path)
        self._load_models(reload=True)
        self.endpoints = self._endpoint_generator().generate_mapping()

    def generate_wrappers(self):
        if self.endpoints is None:
            from raq_helpers.client import EndpointFactory

            factory = EndpointFactory.from_json(
                self.env.endpoints_file_path.read_text(encoding="utf-8"),
                registry=self._registry(),
            )
            self.endpoints = list(factory._endpoints.values())
        return self._endpoint_generator().generate_wrappers(self.endpoints)

    def generate_package(self):
        openalex_metadata = self.model_map if self.model_map is not None else self._read_json(
            self.env.openalex_model_map_file_path
        )
        oqo_metadata = self._read_json(self.env.oqo_model_map_file_path)
        openalex_names = list(dict.fromkeys(item["class_name"] for item in openalex_metadata["models"]))
        oqo_names = list(dict.fromkeys(item["class_name"] for item in oqo_metadata["models"]))
        oqo_exports = [
            (name, "OQO" if name == "Model" else f"OQO{name}" if name in openalex_names else name)
            for name in oqo_names
        ]
        return PackageGenerator(output_dir=self.models_dir).generate(exports={
            "endpoints": ["GeneratedEndpoints"],
            "openalex": openalex_names,
            "oqo": oqo_exports,
            "openalex_registry": [("MODEL_REGISTRY", "OPENALEX_MODEL_REGISTRY")],
            "oqo_registry": [("MODEL_REGISTRY", "OQO_MODEL_REGISTRY")],
        })

    def run(self):
        """Generate artifacts in dependency order."""
        self.generate_models(
            "https://api.openalex.org/query/spec/oqo",
            "oqo.py",
            InputFileType.JsonSchema,
            self.env.oqo_spec_file_path,
            self.env.oqo_model_map_file_path,
        )
        self.generate_models(
            "https://help.openalex.org/openapi.json",
            "openalex.py",
            InputFileType.OpenAPI,
            self.env.openalex_spec_file_path,
            self.env.openalex_model_map_file_path,
        )
        self.generate_mapping()
        self.generate_wrappers()
        self.generate_package()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    SchemaGenerator().run()


if __name__ == "__main__":
    main()
