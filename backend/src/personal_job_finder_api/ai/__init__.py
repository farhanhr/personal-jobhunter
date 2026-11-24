"""Bring-your-own-key AI subsystem (ADR-030).

The package keeps three concerns separate: provider presets, persisted local
settings, and the network probe. Nothing here logs, prints, or returns a stored
API key in plaintext outside the request that the user explicitly submitted.
"""
