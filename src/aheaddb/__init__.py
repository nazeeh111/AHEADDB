"""AHEADDB: local relational prediction and maintained views."""
from .compiler import Compiler
from .engine import Engine
from .modeling import FittedModel, backtest, fit
from .snapshot import Snapshot
from .spec import Event, Outcome, Schema, Task

__all__ = ["Compiler", "Engine", "Event", "FittedModel", "Outcome", "Schema", "Snapshot", "Task", "backtest", "fit"]
__version__ = "0.1.0"
