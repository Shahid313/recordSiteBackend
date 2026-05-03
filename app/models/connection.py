from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.db.base import Base


class Connection(Base):
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True)
    from_pano_id = Column(Integer, ForeignKey("panoramas.id", ondelete="CASCADE"), nullable=False, index=True)
    to_pano_id = Column(Integer, ForeignKey("panoramas.id", ondelete="CASCADE"), nullable=False, index=True)

    confidence = Column(Float, nullable=False, default=0.0)  # 0..1
    manual_override = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    from_panorama = relationship("Panorama", foreign_keys=[from_pano_id])
    to_panorama = relationship("Panorama", foreign_keys=[to_pano_id])


