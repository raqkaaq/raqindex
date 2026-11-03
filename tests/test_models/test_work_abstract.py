"""Abstract convenience survives model generation and stays out of wire data."""

from pydantic import BaseModel
import pytest

from raqindex.codegen import OpenAlexModelGenerator
from raqindex.generated.openalex import Work, WorksListResponse


@pytest.mark.parametrize("index, expected", [
    (None, None), ({}, None),
    ({"world": [1], "Hello": [0, 2]}, "Hello world Hello"),
])
def test_generated_work_abstract(index, expected):
    work = Work.model_validate({"abstract_inverted_index": index})
    assert work.abstract == expected
    assert "abstract" not in Work.model_fields
    assert "abstract" not in work.model_dump()
    assert "abstract" not in Work.model_json_schema()["properties"]
    response = WorksListResponse.model_validate({"results": [work.model_dump()]})
    assert response.results[0].abstract == expected


def test_generation_hook_adds_property(tmp_path):
    output = tmp_path / "models.py"
    output.write_text(
        "from pydantic import BaseModel\n"
        "class RetrievalParameters(BaseModel):\n    page: int = 1\n"
        "class Work(BaseModel):\n"
        "    abstract_inverted_index: dict[str, list[int]] | None = None\n"
    )
    OpenAlexModelGenerator().postprocess_models(output)
    namespace = {}
    exec(compile(output.read_text(), str(output), "exec"), namespace)
    generated = namespace["Work"]
    assert issubclass(generated, BaseModel)
    assert generated(abstract_inverted_index={"Hello": [0]}).abstract == "Hello"


@pytest.mark.parametrize("index, expected", [
    (None, None), ({}, None), ({"unused": []}, None),
    ({"world": [1], "Hello": [0, 2]}, "Hello world Hello"),
    ({"Hello": [0], "world": [2]}, "Hello  world"),
])
def test_shared_reconstruction_and_client_wrapping(index, expected):
    import textwrap
    from raqindex.models.abstract import reconstruct_abstract
    from raqindex import OpenAlexClient

    assert reconstruct_abstract(index) == expected
    assert Work(abstract_inverted_index=index).abstract == expected
    assert OpenAlexClient.reconstruct_and_wrap_abstract(index, max_width=8) == (
        textwrap.fill(expected or "", width=8)
    )
