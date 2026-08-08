"""The detector contract the game engine is fed from.

A detector's whole job is to turn physical board events into two things the
engine already understands: scored `Dart`s (via a callback) and a
"darts were taken out" signal. Nothing downstream knows or cares *how* the
darts were detected - historically this project's own cameras, now Autodarts -
so a detector can be swapped without touching a single game.

Kept deliberately tiny: `start()` spins up whatever polling/loop the detector
needs, `stop()` tears it down, and both are async so a detector can live as a
managed task in the FastAPI application lifecycle.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DartDetector(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Begin producing dart/takeout events. Idempotent."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop cleanly, releasing any resources. Idempotent."""

    def status(self) -> dict:
        """Health snapshot for the UI. Overridden by real detectors."""
        return {"detector": self.__class__.__name__}
