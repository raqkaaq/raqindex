from pathlib import Path
from raq_helpers.client import (
    Environment as BaseEnvironment,
    EnvironmentVariable,
    parse_file,
    parse_ttl,
)


class OpenAlexEnvironment(BaseEnvironment):
    # defining the variables here to help with type checking and IDE autocompletion
    api_key: str
    email: str
    url: str
    cache_path: Path
    cache_db_path: Path
    cache_ttl: int
    schema_path: Path
    oqo_spec_file_path: Path
    openalex_spec_file_path: Path
    oqo_model_map_file_path: Path
    openalex_model_map_file_path: Path
    endpoints_file_path: Path

    variables = {
        "api_key": EnvironmentVariable(env_name="OPENALEX_API_KEY", required=True),
        "email": EnvironmentVariable(env_name="EMAIL", required=True),
        "url": EnvironmentVariable(
            env_name="OPENALEX_API_URL",
            default="https://api.openalex.org",
            required=True,
        ),
        "cache_path": EnvironmentVariable(
            env_name="OPENALEX_CACHE_PATH",
            default="~/.cache/openalex",
            parse=parse_file,
        ),
        "cache_db_path": EnvironmentVariable(
            env_name="OPENALEX_CACHE_DB_PATH",
            default="openalex_cache.db",
            parse=parse_file,
        ),
        "cache_ttl": EnvironmentVariable(
            env_name="OPENALEX_CACHE_TTL", default="1d", parse=parse_ttl
        ),
        "schema_path": EnvironmentVariable(
            env_name="OPENALEX_SCHEMA_PATH", default="schemas", parse=parse_file
        ),
        "oqo_spec_file_path": EnvironmentVariable(
            env_name="OPENALEX_OQO_SPEC_FILE_PATH",
            default="oqo_spec.json",
            parse=parse_file,
        ),
        "openalex_spec_file_path": EnvironmentVariable(
            env_name="OPENALEX_OPENALEX_SPEC_FILE_PATH",
            default="openalex_spec.json",
            parse=parse_file,
        ),
        "oqo_model_map_file_path": EnvironmentVariable(
            env_name="OPENALEX_OQO_MODEL_MAP_FILE_PATH",
            default="oqo_model_map.json",
            parse=parse_file,
        ),
        "openalex_model_map_file_path": EnvironmentVariable(
            env_name="OPENALEX_OPENALEX_MODEL_MAP_FILE_PATH",
            default="openalex_model_map.json",
            parse=parse_file,
        ),
        "endpoints_file_path": EnvironmentVariable(
            env_name="OPENALEX_ENDPOINTS_FILE_PATH",
            default="openalex_endpoints.json",
            parse=parse_file,
        ),
    }

    def __new__(cls, env_file=None):
        return super().__new__(
            cls,
            env_file=env_file
            if env_file is not None
            else Path(__file__).resolve().parents[3] / ".env",
        )

    def load_from_env(self):
        super().load_from_env()
        self.cache_db_path = self.cache_path / self.cache_db_path
        self.schema_path = self.cache_path / self.schema_path
        for name in (
            "oqo_spec_file_path",
            "openalex_spec_file_path",
            "oqo_model_map_file_path",
            "openalex_model_map_file_path",
            "endpoints_file_path",
        ):
            setattr(self, name, self.schema_path / getattr(self, name))

    def set_api_key(self, api_key: str):
        if not isinstance(api_key, str):
            raise TypeError("API key must be a string.")
        self.api_key = api_key

    def set_url(self, url: str):
        if not isinstance(url, str):
            raise TypeError("URL must be a string.")
        self.url = url

    def get_url(self):
        return self.url

    def get_api_key(self):
        return self.api_key

    def get_email(self):
        return self.email


Environment = OpenAlexEnvironment
