from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    sha256: str
    size_bytes: int
    created_at: datetime
    rows_read: int = Field(description="data rows in the feed, blank rows excluded")
    products: int = Field(description="Shopify products written")
    variants: int = Field(description="products that carry variants")
    rows_left_out: int = Field(description="rows blocked from the import")
    findings_total: int
    findings_blocking: int
    delimiter: str
    encoding: str
    decimal: str


class RunCreated(RunOut):
    already_seen: bool = Field(description="true when this exact file was uploaded before")
    findings_by_what: dict[str, int] = Field(description="what the engine CLI prints under its findings line")


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: int | None = Field(description="pass as ?after= for the next page; null on the last one")


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    level: str
    row: int
    sku: str
    what: str
    detail: str


class FindingStat(BaseModel):
    what: str
    level: str
    findings: int
    runs: int


class Health(BaseModel):
    status: str
    database: str
