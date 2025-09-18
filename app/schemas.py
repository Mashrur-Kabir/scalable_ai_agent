from pydantic import BaseModel, HttpUrl
from typing import Optional

class AnalyzeRequest(BaseModel):
    title: Optional[str] = None
    abstract: Optional[str] = None
    text: Optional[str] = None
    url: Optional[HttpUrl] = None

class SubmitResponse(BaseModel):
    request_id: str
    status: str

class StatusResponse(BaseModel):
    request_id: str
    status: str
    queued_at: float
    finished_at: Optional[float] = None
    result: Optional[dict] = None