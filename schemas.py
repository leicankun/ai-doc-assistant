from typing import Literal
from pydantic import BaseModel, Field, field_validator

class Document(BaseModel):
    title: str = Field(..., description="文档标题",min_length=1, max_length=100)
    file_type: Literal["markdown", "pdf"] = Field(..., description="文档类型")
    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class QuestionRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="用户的问题",
    )
    session_id: int = Field(
    ...,
    gt=0,
    description="本次提问所属的会话 ID",
    )

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class SessionCreate(BaseModel):
    title: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="会话名称",

    )

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: Literal["bm25","dense","hybrid"] = "bm25"
    embedding_provider: Literal['api', 'local'] = 'api'

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value



class RagChatRequest(SearchRequest):
    session_id: int = Field(gt=0)


class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: int | None = Field(default=None, gt=0)
    embedding_provider: Literal['api', 'local'] = 'api'

    @field_validator('question', mode='before')
    @classmethod
    def strip_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class KnowledgeBaseCreate(SessionCreate):
    pass


class KnowledgeBaseDocuments(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator('document_ids')
    @classmethod
    def positive_ids(cls, value):
        if any(number <= 0 for number in value):
            raise ValueError('文档 ID 必须是正整数')
        return list(dict.fromkeys(value))
