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
