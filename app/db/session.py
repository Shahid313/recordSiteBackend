from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine('postgresql://postgres:CrmiCxTSauqhMZxvGYKVOVEKjyfaybdW@postgres.railway.internal:5432/railway', pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """
    Database dependency
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()