from fileinput import filename
from pathlib import Path
import json
import ast
from copy import deepcopy
from raqindex.client.environment import Environment

import httpx2
from datamodel_code_generator import (
    DataModelType,
    DatetimeClassType,
    InputFileType,
    OpenAPIScope,
    generate,
)
from datamodel_code_generator.format import Formatter
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(Path(__file__).stem)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "src" / "raqindex" / "models"

GENERAL_OPTIONS = {
    "output_model_type": DataModelType.PydanticV2BaseModel,
    "output_datetime_class": DatetimeClassType.Datetime,
    "formatters": [Formatter.BUILTIN],
    "use_schema_description": True,
    "reuse_model": False,
    "field_constraints": True,
    "snake_case_field": True,
    "deserialize_default_values": ["enum"]
}

#create a reusable model for some specific parameters for models that are not in the schema
def supplement_schema(schema: dict) -> None:
    """Reuse classic retrieval parameter schemas as a standalone model."""
    models = schema.setdefault("components", {}).setdefault("schemas", {})
    if "RetrievalParameters" in models:
        return

    properties = {}
    for parameter in schema["paths"]["/works"]["get"]["parameters"]:
        while "$ref" in parameter:
            target = schema
            for part in parameter["$ref"].removeprefix("#/").split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            parameter = target
            #this function is designed to extract the parameters that query and oqo use to share
        if parameter.get("name") in {"per_page", "page", "cursor", "select", "sort"}:
            properties[parameter["name"]] = deepcopy(parameter["schema"])

    models["RetrievalParameters"] = {
        "type": "object",
        "properties": properties,
    }

#retroactively assigns the shared RetrievalParameters model to all models that have the same fields
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


def generate_models(url, filename, input_type, schema_path, model_map_path):
    logger.info("Fetching schema from %s", url)
    response = httpx2.get(url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    logger.info("Schema fetched successfully. Generating models in %s", MODELS_DIR / filename)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    schema_path.parent.mkdir(parents=True, exist_ok=True)
    model_map_path.parent.mkdir(parents=True, exist_ok=True)
    schema = response.json()
    if input_type == InputFileType.OpenAPI:
        supplement_schema(schema)
    schema_text = json.dumps(schema, indent=2)
    schema_path.write_text(schema_text, encoding="utf-8")
    logger.info("Schema saved to %s", schema_path)
    if input_type == InputFileType.JsonSchema:
        logger.info("Generating models from JSON Schema")
        generate(
            input_=schema_text,
            output=MODELS_DIR / filename,
            input_file_type=input_type,
            **GENERAL_OPTIONS,
            emit_model_metadata=model_map_path,
        )
    elif input_type == InputFileType.OpenAPI:
        logger.info("Generating models from OpenAPI specification")
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
        logger.info("OpenAPI scopes determined: %s", [scope.name for scope in scopes])
        generate(
            input_=schema_text,
            output=MODELS_DIR / filename,
            input_file_type=input_type,
            **GENERAL_OPTIONS,
            openapi_scopes=scopes,
            include_path_parameters=True,
            emit_model_metadata=model_map_path,
        )
        share_retrieval_parameters(MODELS_DIR / filename)
        
    logger.info("Models generated successfully in %s", MODELS_DIR / filename)

def generate_mapping(env):
    logger.info("Generating endpoint mapping from schema and model map")
    try:
        import json
        with env.openalex_spec_file_path.open() as f:
            spec = json.load(f)
        with env.openalex_model_map_file_path.open() as f:
            model_map = json.load(f)
    except Exception as e:
        logger.error("Error loading schema or model map: %s", e)
        return

    #assert that there are only "GET" methods in the spec
    methods = set()
    for key in spec["paths"].keys():
        for method in spec["paths"][key].keys():
            methods.add(method.upper())
    assert methods == {'GET'} #there are ONLY GET methods in the OpenAlex API

    try:
        import importlib
        from raqindex.models.endpoints import Endpoint
        generated_models = importlib.import_module("raqindex.models.openalex")
        generated_models = importlib.reload(generated_models)
    except Exception as e:
        logger.error("Error importing generated models: %s", e)
        return

    endpoints = []
    codes = set()
    for entry in model_map['models']:
        path = entry.get('source_path', '') or []
        if path and path[0] == 'paths' and path[-1] == 'parameters':
            method = path[-2].upper()
            endpoint = '/' + '/'.join(path[1:-2])
            try:
                request = entry["class_name"]
                request_class = {
                    "ref": request,
                    "model": getattr(generated_models, entry["class_name"])
                }
                responses = spec['paths'][endpoint][method.lower()]['responses'].copy()
                for status_code, response in responses.items():
                    codes.add(status_code)
                    match status_code:
                        case "200":
                            source = (
                                body["$ref"]
                                if "$ref" in (body := response["content"]["application/json"]["schema"])
                                else f"#/paths/{endpoint.strip('/')}/{method.lower()}/responses/200/application/json"
                            )
                            matched = next(
                                item for item in model_map["models"]
                                if item["source_ref"].split("#", 1)[-1] == source.split("#", 1)[-1]
                            )
                            responses[status_code] = {
                                "ref": matched["class_name"],
                                "model": getattr(generated_models, matched["class_name"]),
                            }
                        case _:
                            response_name = response["$ref"].split("/")[-1]
                            definition = spec["components"]["responses"][response_name]
                            res = definition["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
                            responses[status_code] = {
                                "ref": response_name,
                                "model": getattr(generated_models, res)
                            }
            except Exception as e:
                print(f"Error occurred while fetching classes for {endpoint}: {e}")
                continue
            except KeyError as e:
                print(f"Error occurred while fetching classes for {endpoint}: {e}")
                continue
            endpoints.append(Endpoint.model_validate({
                "endpoint": endpoint,
                "method": {
                    **spec['paths'][endpoint][method.lower()],
                    "method": method,
                    'request': request_class,
                    'responses': responses}
                }))
            data = [
                endpoint.model_dump(mode="json", by_alias=True, exclude_none=True)
                for endpoint in endpoints
            ]
# save the data to a JSON file
    import json
    env.endpoints_file_path.parent.mkdir(parents=True, exist_ok=True)
    with env.endpoints_file_path.open("w") as f:
        json.dump(data, f, indent=4)
    logger.info("Endpoint mapping generated successfully in %s", env.endpoints_file_path)

def generate_wrappers(env):
    logger.info("Generating endpoint wrappers from endpoint mapping")

    try:
        import importlib
        import inspect
        import json
        import keyword
        from datetime import datetime, timezone
        from string import Formatter

        with env.endpoints_file_path.open() as f:
            endpoints = json.load(f)
    except Exception as e:
        logger.error("Error loading endpoint mapping: %s", e)
        return

    try:
        models = importlib.import_module("raqindex.models.openalex")
    except Exception as e:
        logger.error("Error importing generated models: %s", e)
        return

    groups = {}
    for item in endpoints:
        path = item["endpoint"]
        parts = list(Formatter().parse(path))

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

    lines = [
        "# Generated by scripts/generate_schema.py",
        f"# Generated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "class GeneratedEndpoints:",
    ]

    for name, variants in sorted(groups.items()):
        logger.debug("Generating endpoint wrapper for %s", name)
        variants.sort(key=lambda variant: (len(variant[1]), variant[0]))

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

        parameter_details = []
        endpoint_details = []

        for path, _, item in variants:
            request_name = item["method"]["request"]["model"]
            request_model = getattr(models, request_name)

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

        lines.extend([
            f"    {name}.parameters = {parameters!r}",
            f"    {name}.endpoints = {endpoint_summary!r}",
            "",
        ])

    if lines[-1] == "class GeneratedEndpoints:":
        lines.append("    pass")

    output = PROJECT_ROOT / "src" / "raqindex" / "client" / "generated_endpoints.py"
    output.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Saving endpoint wrappers to %s", output)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Endpoint wrappers generated successfully in %s", output)


if __name__ == "__main__":
    env = Environment()
    generate_models(
        "https://api.openalex.org/query/spec/oqo",
        "oqo.py",
        InputFileType.JsonSchema,
        env.oqo_spec_file_path,
        env.oqo_model_map_file_path
    )
    generate_models(
        "https://help.openalex.org/openapi.json",
        "openalex.py",
        InputFileType.OpenAPI,
        env.openalex_spec_file_path,
        env.openalex_model_map_file_path
    )
    generate_mapping(env)
    generate_wrappers(env)
