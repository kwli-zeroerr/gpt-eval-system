from typing import Dict, List, Literal
from pydantic import BaseModel, Field


CategoryId = Literal["S1", "S2", "S3", "S4", "S5", "S6"]


class CategorySchema(BaseModel):
    """Represents a category with editable prompt metadata."""

    id: str
    title: str
    description: str
    default_prompt: str
    default_count: int = 5


class GenerateRequest(BaseModel):
    categories: List[str] = Field(
        default_factory=lambda: ["S1", "S2", "S3", "S4", "S5", "S6"]
    )
    per_category: int = 5
    prompt_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional prompt overrides keyed by category id.",
    )
    source_files: List[str] = Field(
        default_factory=list,
        description="Optional list of files to summarize; left as placeholder.",
    )


class QuestionItem(BaseModel):
    category: str
    text: str
    reference: str = Field(default="", description="Source reference (chunk/file identifier)")


class GenerateResponse(BaseModel):
    request_id: str
    questions: List[QuestionItem]


class CategoriesResponse(BaseModel):
    categories: List[CategorySchema]

