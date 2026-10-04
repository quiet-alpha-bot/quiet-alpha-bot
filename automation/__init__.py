"""Quiet Alpha private automation layer.

Everything in this package is optional: the existing manual bot in main.py
keeps working when AUTOMATION_ENABLED is false, when configuration is
missing, or when any automation component fails.

This package never imports main.py. The existing card renderers are passed
in through dependency injection (see automation.cards.CardRenderers).
"""
