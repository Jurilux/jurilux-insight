"""Modèles Pydantic — contrat exact attendu par jurilux-web/src/api.ts."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    juridiction_key: Optional[str] = None
    source_type: Optional[str] = None  # jurisprudence | law | projet_loi


class Turn(BaseModel):
    role: str          # 'user' | 'assistant'
    content: str


class AskRequest(BaseModel):
    q: str = Field(min_length=1)
    topK: int = Field(default=20, ge=1, le=100)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    pedagogical: bool = False  # mode étudiant : réponse didactique
    history: Optional[List["Turn"]] = None  # tours précédents de la session (contexte conversationnel)


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
    # Cinématique de rebond : question voisine que le modèle PEUT traiter avec les
    # extraits trouvés — proposée surtout en cas de refus/partiel (pivot 1 clic, AUTRE angle).
    suggested_question: Optional[str] = None
    # Parcours guidé : série de questions de suivi LOGIQUES et ordonnées qui, enchaînées,
    # mènent à une réponse complète sur le même sujet (ajout optionnel rétrocompatible).
    follow_ups: Optional[List[str]] = None
    prompt_version: Optional[str] = None


class FeedbackIn(BaseModel):
    question: str = Field(min_length=1)
    helpful: bool
    missing: Optional[str] = None       # ce qui manquait (si 👎)
    status: Optional[str] = None        # statut de la réponse notée (ok|partial|refused)


class ShareIn(BaseModel):
    question: str = Field(min_length=1)
    answer: Optional[str] = None
    citations: list = Field(default_factory=list)  # instantané des citations (dicts)
    status: Optional[str] = None


# V3 offre cabinet
class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberAdd(BaseModel):
    email: str = Field(min_length=3)
    role: str = "member"


class DossierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DossierItemAdd(BaseModel):
    question: str = Field(min_length=1)
    answer: Optional[str] = None
    citations: list = Field(default_factory=list)
    status: Optional[str] = None
