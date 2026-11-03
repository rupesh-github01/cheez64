"""Metadata only; move/FEN editing belongs to the subsequent workspace stage."""
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Metadata(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=5000)

    @field_validator('title', 'description', mode='before')
    @classmethod
    def clean(cls, value):
        if not isinstance(value, str) or '\0' in value:raise ValueError('Text required without NUL characters')
        return value.strip()


class SubStudyInput(Metadata):
    source_url: str | None = Field(None, max_length=2048)

    @field_validator('source_url')
    @classmethod
    def url(cls, value):
        if value is None or not value.strip():return None
        value = value.strip()
        try:
            u = urlsplit(value)
            if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or any(c.isspace() or ord(c)<32 for c in value):raise ValueError('Use an HTTP(S) source URL without credentials')
            _ = u.port
        except ValueError:raise ValueError('Use a valid HTTP(S) source URL without credentials') from None
        return value


class Reorder(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ids: list[str] = Field(max_length=500)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.ids)) != len(self.ids) or any(len(x)!=32 or any(c not in '0123456789abcdef' for c in x) for x in self.ids):raise ValueError('Unique study entity IDs required')
        return self

class StudyView(Metadata):
    id: str
    created_at: str
    updated_at: str


class SubStudyView(SubStudyInput):
    id: str
    study_id: str
    sort_order: int
    created_at: str
    updated_at: str


class LineView(StudyView):
    sub_study_id: str
    initial_fen: str
    sort_order: int


from typing import Generic, TypeVar
T = TypeVar('T')
class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int
    next_offset: int | None
