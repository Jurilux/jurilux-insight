"""Modèles Pydantic — contrat exact attendu par jurilux-web/src/api.ts."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    juridiction_key: Optional[str] = None
    source_type: Optional[str] = None  # jurisprudence | law | projet_loi


class AskRequest(BaseModel):
    q: str = Field(min_length=1)
    topK: int = Field(default=20, ge=1, le=100)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    pedagogical: bool = False  # mode étudiant : réponse didactique


class Citation(BaseModel):
    doc_id: str
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    year: Optional[int] = None
    juridiction_key: Optional[str] = None
    content: Optional[str] = None
    source_type: Optional[Literal["jurisprudence", "law", "projet_loi"]] = None
    title: Optional[str] = None


class Feedback(BaseModel):
    why: Optional[str] = None
    what_we_see: Optional[list[str]] = None
    limits: Optional[str] = None
    how_to_improve: Optional[list[str]] = None


class AskResponse(BaseModel):
    answer: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False
    status: Optional[Literal["ok", "partial"]] = "ok"
    feedback: Optional[Feedback] = None
    prompt_version: Optional[str] = None
