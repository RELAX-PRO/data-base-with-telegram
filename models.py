"""ORM models for optical frames inventory."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import Mapped

from db import Base

MATERIAL_CHOICES = [
    "plastic",
    "acetate",
    "metal",
    "stainless steel",
    "titanium",
    "aluminum",
    "wood",
    "carbon fiber",
    "other",
    "unknown",
]


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    brand: Mapped[str | None] = Column(String(100), index=True, nullable=True)
    model_code: Mapped[str] = Column(String(100), index=True, nullable=False)
    material: Mapped[str] = Column(String(50), index=True, default="unknown")
    lens_width: Mapped[int | None] = Column(Integer, nullable=True)
    bridge_size: Mapped[int | None] = Column(Integer, nullable=True)
    temple_length: Mapped[int | None] = Column(Integer, nullable=True)
    color: Mapped[str | None] = Column(String(50), index=True)
    shape: Mapped[str | None] = Column(String(50), index=True)
    gender: Mapped[str | None] = Column(String(20), index=True)  # men/women/unisex/child
    price: Mapped[float | None] = Column(Float, nullable=True)
    stock: Mapped[int] = Column(Integer, default=0)
    notes: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):  # simple serializer
        return {
            "id": self.id,
            "brand": self.brand,
            "model_code": self.model_code,
            "material": self.material,
            "lens_width": self.lens_width,
            "bridge_size": self.bridge_size,
            "temple_length": self.temple_length,
            "color": self.color,
            "shape": self.shape,
            "gender": self.gender,
            "price": self.price,
            "stock": self.stock,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
