from sqlalchemy.ext.declarative import declarative_base

# IMPORTANT:
# This module must not import model modules.
# Models import `Base` from here, so importing models here creates a circular import.
Base = declarative_base()