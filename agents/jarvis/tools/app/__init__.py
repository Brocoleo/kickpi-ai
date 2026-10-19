"""Jarvis Tools — servicio de solo lectura con acceso controlado al sistema.

Es el ÚNICO componente que ve /var/run/docker.sock. Solo implementa
operaciones de lectura; no existe ningún endpoint que modifique estado.
"""
