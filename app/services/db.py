from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ..core.config import cfg

engine = create_engine(cfg.DB_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
