import logging
import pytimeparse2

logger = logging.getLogger(__name__)

class Environment:
    instance = None
    #singleton pattern to ensure only one instance of Environment exists

    def __new__(cls):
        if cls.instance is None:
            cls.instance = super(Environment, cls).__new__(cls)
            cls.instance.api_key = None
            cls.instance.url = None
        if cls.instance.api_key is None:
            cls.instance.load_from_env()
        logger.debug("Environment instance created.")
        return cls.instance

    def load_from_env(cls):
        from dotenv import load_dotenv, dotenv_values
        from pathlib import Path
        import os
        ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

        load_dotenv(dotenv_path=ENV_FILE)
        variables = dotenv_values(dotenv_path=ENV_FILE)
        logger.debug("Loaded .env file from: %s", ENV_FILE)
        logger.debug("Variables obtained from .env file: %s", list(variables.keys()))
        cls.instance.api_key = os.getenv("OPENALEX_API_KEY", None)
        cls.instance.email = os.getenv("EMAIL", None)
        cls.instance.url = os.getenv("OPENALEX_API_URL", "https://api.openalex.org")

        #set default cache path and other environment variables
        cache_path = Path(os.getenv("OPENALEX_CACHE_PATH", "~/.cache/openalex")).expanduser()
        def cache_file(variable, default):
            path = Path(os.getenv(variable, default)).expanduser()
            return path if path.is_absolute() else cache_path / path
        cls.instance.cache_path = cache_path

        cls.instance.cache_db_path = cache_file("OPENALEX_CACHE_DB_PATH", "openalex_cache.db")
        schema_path = Path(os.getenv("OPENALEX_SCHEMA_PATH", "schemas"))

        if schema_path.is_absolute():
            raise ValueError("OPENALEX_SCHEMA_PATH must be relative")
        schema_path = cache_path / schema_path

        cls.instance.schema_path = schema_path

        def schema_file(variable, default):
            path = Path(os.getenv(variable, default))
            if path.is_absolute():
                raise ValueError(f"{variable} must be relative")
            return schema_path / path

        cls.instance.oqo_spec_file_path = schema_file(
            "OPENALEX_OQO_SPEC_FILE_PATH", "oqo_spec.json"
        )
        cls.instance.openalex_spec_file_path = schema_file(
            "OPENALEX_OPENALEX_SPEC_FILE_PATH", "openalex_spec.json"
        )
        cls.instance.oqo_model_map_file_path = schema_file(
            "OPENALEX_OQO_MODEL_MAP_FILE_PATH", "oqo_model_map.json"
        )
        cls.instance.openalex_model_map_file_path = schema_file(
            "OPENALEX_OPENALEX_MODEL_MAP_FILE_PATH", "openalex_model_map.json"
        )
        cls.instance.endpoints_file_path = schema_file(
            "OPENALEX_ENDPOINTS_FILE_PATH", "openalex_endpoints.json"
        )

        cls.instance.cache_ttl = pytimeparse2.parse(os.getenv("OPENALEX_CACHE_TTL", "1d"))  # Default to 1 day in seconds if not set
        logger.debug(
            "Environment variables set: url=%s, cache_db_path=%s, cache_ttl=%s, oqo_spec_file_path=%s, openalex_spec_file_path=%s",
            cls.instance.url,
            cls.instance.cache_db_path,
            cls.instance.cache_ttl,
            cls.instance.oqo_spec_file_path,
            cls.instance.openalex_spec_file_path)
        
        if cls.instance.api_key is None:
            raise ValueError("OPENALEX_API_KEY environment variable is not set.")
        if cls.instance.url is None:
            raise ValueError("OPENALEX_API_URL environment variable is not set.")
        if cls.instance.email is None:
            raise ValueError("EMAIL environment variable is not set.")

    def set_api_key(self, api_key: str):
        if not isinstance(api_key, str):
            raise TypeError("API key must be a string.")
        self.api_key = api_key

    def set_url(self, url: str):
        if not isinstance(url, str):
            raise TypeError("URL must be a string.")
        self.url = url

    def get_api_key(self) -> str:
        return self.api_key

    def get_url(self) -> str:
        return self.url