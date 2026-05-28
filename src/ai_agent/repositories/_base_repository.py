from typing import Any
from uuid import UUID

from tortoise.exceptions import DoesNotExist

from ai_agent.database.models._base import BaseAbstractModel
from ai_agent.errors import DatabaseError, NotFoundError


class BaseRepo[M: BaseAbstractModel]:
    """Base repository for interacting with a database model."""

    _model: type[M]

    async def get(self, **kwargs: Any) -> M:
        try:
            return await self._model.get(**kwargs)
        except DoesNotExist as e:
            raise NotFoundError(details=kwargs) from e

    async def get_or_none(self, **kwargs: Any) -> M | None:
        return await self._model.get_or_none(**kwargs)

    async def create(self, **kwargs: Any) -> M:
        try:
            return await self._model.create(**kwargs)
        except Exception as e:
            raise DatabaseError(details={"error": str(e)}) from e

    async def update(self, _id: UUID, **kwargs: Any) -> M:
        try:
            instance = await self.get(id=_id)
            await instance.update_from_dict(kwargs).save()
            return instance
        except DoesNotExist as e:
            raise NotFoundError(details={"id": _id}) from e
        except Exception as e:
            raise DatabaseError(details={"error": str(e)}) from e

    async def get_or_create(
        self,
        defaults: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[M, bool]:
        try:
            return await self._model.get_or_create(defaults=defaults, **kwargs)
        except Exception as e:
            raise DatabaseError(details={"error": str(e)}) from e
