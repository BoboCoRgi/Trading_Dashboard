"""Shared types used by connectors, analytics, and the database.

This package exists so broker adapters and the risk engine do not import
SQLAlchemy, and so the ORM does not import connector code.
"""
