"""Capa de base de datos: PostgreSQL 16 + pgvector con SQLAlchemy async.

El esquema solo cambia con migraciones de Alembic (alembic/versions); la
app nunca llama a Base.metadata.create_all().
"""
