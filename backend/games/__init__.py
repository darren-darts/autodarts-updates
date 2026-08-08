"""Game library.

Importing this package registers every playable game with the registry, so
`registry.catalogue_view()` reflects what is actually available.
"""
from . import chores, golf, oxo, party, practice, snakes, x01  # noqa: F401  (imported for their @register side effects)
from .base import Dart, Game, PlayerState, TurnResult  # noqa: F401
from .engine import match_engine  # noqa: F401
from .registry import build_game, catalogue_view, get_definition  # noqa: F401
