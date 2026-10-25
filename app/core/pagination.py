"""Shared pagination primitives."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from app.core.config import get_settings

T = TypeVar("T")

settings = get_settings()


class PageParams:
    """Page-based pagination parameters for list endpoints."""

    def __init__(
        self,
        page: int = Query(1, ge=1, description="1-based page number"),
        per_page: int = Query(
            settings.default_page_size,
            ge=1,
            le=settings.max_page_size,
            description="Number of items per page",
        ),
    ) -> None:
        self.page = page
        self.per_page = per_page
        self.offset = (page - 1) * per_page
        self.limit = per_page


class Page(BaseModel, Generic[T]):
    """Envelope returned by every paginated collection endpoint."""

    items: list[T] = Field(description="Items on the current page")
    total: int = Field(description="Total number of items matching the filters")
    page: int = Field(description="Current page (1-based)")
    per_page: int = Field(description="Page size")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether a next page exists")
    has_prev: bool = Field(description="Whether a previous page exists")

    @classmethod
    def create(cls, items: Sequence[T], total: int, params: PageParams) -> Page[T]:
        pages = math.ceil(total / params.per_page) if total else 0
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            per_page=params.per_page,
            pages=pages,
            has_next=params.page < pages,
            has_prev=params.page > 1,
        )
