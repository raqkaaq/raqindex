"""Check each generated route independently of HTTP or the live schema cache."""
import re

import pytest

from raqindex.client.generated_endpoints import GeneratedEndpoints


def routes():
    for name, method in vars(GeneratedEndpoints).items():
        if callable(method) and hasattr(method, 'endpoints'):
            for line in method.endpoints.splitlines():
                path = line.split(' -> ', 1)[0]
                yield pytest.param(name, path, id=path)


@pytest.mark.asyncio
@pytest.mark.parametrize('name,path', list(routes()))
async def test_every_generated_route_forwards_path_and_query_parameters(name, path):
    calls = []

    class Client(GeneratedEndpoints):
        def endpoint_factory(self, selected):
            async def call(**params):
                calls.append((selected, params))
                return 'sentinel'
            return call

    params = {key: f'value-{key}' for key in re.findall(r'\{([^}]+)\}', path)}
    params['select'] = 'id'
    assert await getattr(Client(), name)(**params) == 'sentinel'
    assert calls == [(path, params)]


@pytest.mark.asyncio
async def test_missing_required_route_parameter_rejected_without_calling_factory():
    class Client(GeneratedEndpoints):
        def endpoint_factory(self, path):
            pytest.fail('No request should be dispatched')
    with pytest.raises(TypeError, match='Unsupported path-parameter combination'):
        await Client().autocomplete()
