"""Configuracion de gunicorn para el servicio tfg-api.

bind en 127.0.0.1: la API no se expone directamente a internet, siempre pasa
por nginx, que es el unico que escucha en las interfaces publicas.
"""
bind = "127.0.0.1:5000"
workers = 2
threads = 2
timeout = 60
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
