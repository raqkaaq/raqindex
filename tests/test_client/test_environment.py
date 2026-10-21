from pathlib import Path

import pytest

from raqindex.client.environment import Environment

def test_env_init():
    env = Environment()
    assert env is not None
    assert env.api_key is not None
    assert env.url is not None

def test_env_set_api_key():
    env = Environment()
    env.set_api_key("test_api_key")
    assert env.api_key == "test_api_key"

def test_env_set_url():
    env = Environment()
    env.set_url("https://test.api.url")
    assert env.url == "https://test.api.url"

def test_env_get_api_key():
    env = Environment()
    env.set_api_key("test_api_key")
    assert env.get_api_key() == "test_api_key"

def test_env_get_url():
    env = Environment()
    env.set_url("https://test.api.url")
    assert env.get_url() == "https://test.api.url"

def test_env_singleton_behavior():
    env1 = Environment()
    env2 = Environment()
    assert env1 is env2

def test_env_singleton_with_different_keys():
    env1 = Environment()
    env1.set_api_key("test_api_key_1")
    env2 = Environment()
    env2.set_api_key("test_api_key_2")
    assert env1 is env2
    assert env1.get_api_key() == "test_api_key_2"

def test_env_invalid_api_key_type():
    env = Environment()
    with pytest.raises(TypeError):
        env.set_api_key(12345)  # Non-string API key should raise TypeError

def test_env_valid_cache_ttl_parsing():
    env = Environment()
    assert isinstance(env.cache_ttl, int) and env.cache_ttl > 0, "Cache TTL should be a positive integer representing seconds"

@pytest.fixture(autouse=True)
def isolated_environment(environment_variables, monkeypatch):
    # Prevent the developer's .env from supplying values during tests.
    monkeypatch.setattr("dotenv.load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr("dotenv.dotenv_values", lambda **kwargs: {})


@pytest.mark.parametrize("duration,seconds", [("1d", 86400), ("2h", 7200), ("30m", 1800)])
def test_cache_ttl_parsing(monkeypatch, duration, seconds):
    monkeypatch.setenv("OPENALEX_CACHE_TTL", duration)
    assert Environment().cache_ttl == seconds


def test_environment_defaults(monkeypatch):
    for name in ["OPENALEX_API_URL", "OPENALEX_CACHE_PATH", "OPENALEX_CACHE_DB_PATH", "OPENALEX_CACHE_TTL", "OPENALEX_OQO_SPEC_FILE_PATH"]:
        monkeypatch.delenv(name, raising=False)
    env = Environment()
    assert env.url == "https://api.openalex.org"
    assert env.cache_path == Path.home() / ".cache/openalex"
    assert env.cache_db_path == env.cache_path / "openalex_cache.db"
    assert env.oqo_spec_file_path == env.cache_path / "schemas" / "oqo_spec.json"
    assert env.cache_ttl == 86400


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENALEX_API_KEY environment variable is not set"):
        Environment()


@pytest.mark.parametrize("value", [None, 123, []])
def test_invalid_url_type(value):
    with pytest.raises(TypeError, match="URL must be a string"):
        Environment().set_url(value)


@pytest.mark.parametrize("variable,attribute", [
    ("OPENALEX_CACHE_DB_PATH", "cache_db_path"),
    ("OPENALEX_OQO_SPEC_FILE_PATH", "oqo_spec_file_path"),
])
@pytest.mark.parametrize("configured", ["nested/file.json", "/tmp/openalex-test/file.json", "~/custom/file.json"])
def test_cache_file_path_resolution(monkeypatch, tmp_path, variable, attribute, configured):
    monkeypatch.setenv(variable, configured)
    path = Path(configured).expanduser()
    if attribute == "oqo_spec_file_path":
        if Path(configured).is_absolute():
            with pytest.raises(ValueError, match="must be relative"):
                Environment()
            return
        # Schema filenames are relative to the schema directory; ~ is literal.
        expected = tmp_path / "schemas" / configured
    else:
        expected = path if path.is_absolute() else tmp_path / path
    assert getattr(Environment(), attribute) == expected


def test_cache_directory_expands_home(monkeypatch):
    monkeypatch.setenv("OPENALEX_CACHE_PATH", "~/custom-openalex-cache")
    monkeypatch.delenv("OPENALEX_CACHE_DB_PATH")
    env = Environment()
    assert env.cache_path == Path.home() / "custom-openalex-cache"
    assert env.cache_db_path == env.cache_path / "openalex_cache.db"
