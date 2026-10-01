from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Run(Base):
    """One feed upload. The file's sha256 is unique: the same bytes are one run."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rows_read: Mapped[int] = mapped_column(Integer)
    products: Mapped[int] = mapped_column(Integer)
    variants: Mapped[int] = mapped_column(Integer)
    rows_left_out: Mapped[int] = mapped_column(Integer)
    findings_total: Mapped[int] = mapped_column(Integer)
    findings_blocking: Mapped[int] = mapped_column(Integer)
    delimiter: Mapped[str] = mapped_column(String(4))
    encoding: Mapped[str] = mapped_column(String(40))
    decimal: Mapped[str] = mapped_column(String(1))

    findings: Mapped[list["Finding"]] = relationship(back_populates="run", order_by="Finding.id")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(20))
    row: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(Text)
    what: Mapped[str] = mapped_column(String(200), index=True)
    detail: Mapped[str] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="findings")


class RunFile(Base):
    """The two CSVs the engine writes for a run, kept as text so a download is byte-exact."""

    __tablename__ = "run_files"
    __table_args__ = (
        UniqueConstraint("run_id", "kind"),
        CheckConstraint("kind IN ('matrixify', 'report')", name="run_files_kind_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
