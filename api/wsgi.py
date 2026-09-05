"""Punto de entrada para gunicorn: gunicorn --config gunicorn_config.py wsgi:app"""
from app import create_app

app = create_app()
