from pydantic import BaseModel


class TaskChangesDTO(BaseModel):
    title: tuple[str | None, str | None] | None = None
    status: tuple[str | None, str | None] | None = None
    project: tuple[str | None, str | None] | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in (self.title, self.status, self.project))

    def iter_fields(self) -> list[tuple[str, str | None, str | None]]:
        out: list[tuple[str, str | None, str | None]] = []
        for field in ("title", "status", "project"):
            change = getattr(self, field)
            if change is not None:
                out.append((field, change[0], change[1]))
        return out
