"""Results for work discovery, identifier resolution, and OQL ID collection."""

from pydantic import BaseModel

from ..generated import Error, Work


class WorkIDBatch(BaseModel):
    ids: list[str]
    total: int | None = None
    complete: bool


class WorkIDResolution(BaseModel):
    identifier: str
    id: str | None = None
    work: Work | None = None
    error: Error | None = None


class WorkIDLookup(BaseModel):
    results: list[WorkIDResolution]

    @property
    def ids(self) -> list[str]:
        """Successful bare IDs, deduplicated in input order."""
        return list(
            dict.fromkeys(result.id for result in self.results if result.id is not None)
        )
