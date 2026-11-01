"""Stable Live identities; no model deserialization or executable artifact load."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy
import pandas
import sklearn
from joblib.hashing import NumpyHasher

from .. import compiler, modeling
from ..compiler import Compiler
from ..modeling import FittedModel
from ..spec import digest

_CODE_IDENTITY = {"compiler_code": digest(Path(compiler.__file__).read_text()),
                  "modeling_code": digest(Path(modeling.__file__).read_text()),
                  "live_model_code": digest(Path(__file__).read_text())}


def runtime_versions() -> dict:
    return {"sklearn": sklearn.__version__, "numpy": numpy.__version__, "pandas": pandas.__version__,
            "joblib": joblib.__version__, "live_model_contract": 1,
            **_CODE_IDENTITY}


class ReplayModel(FittedModel):
    @property
    def version(self) -> str:
        # Pinned joblib canonicalizes string-key dictionaries and string sets,
        # including Pydantic field sets. Raw pickle bytes vary by PYTHONHASHSEED.
        # NumpyHasher supports SHA256 directly; no persisted pickle is ever loaded.
        return digest({"weights": NumpyHasher(hash_name="sha256").hash(self.estimator),
                       "task": self.task.version, "columns": self.columns,
                       "as_of": self.trained_as_of, "training": self.training_version,
                       "backend": self.backend, "preprocessing": Compiler.PREPROCESSING,
                       "runtime": runtime_versions(), "schema": self.schema_version,
                       "training_ranges": self.training_ranges, "selection": self.selection})


def replay_model(model: FittedModel) -> ReplayModel:
    return ReplayModel(**model.__dict__)
