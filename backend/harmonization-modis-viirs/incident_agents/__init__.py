"""Scheduled multi-source fire incident agents.

A query (countries or zones, free text) is turned into a search plan by a small
Groq model, each source is fetched by plain code, a light model judges the
free-text records, and matching reports become tickets that keep every source
URL with its publish time and fetch time. Every step writes an event so a UI can
show progress. Tickets are unverified public mentions, never official alerts.
"""
