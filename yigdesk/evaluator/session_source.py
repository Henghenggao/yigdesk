"""Read-only adapter: bound Yigdesk session -> evaluator ModelSource.

Provides the "uploaded XLSX -> source behind a decision" path by
reusing :mod:`yigdesk.session` (itself layered read-only over an
already-bound, immutable session directory) to locate the projection
workbook backing the active session, then wrapping it as a
:class:`yigdesk.evaluator.model_source.ModelSource` so the blackboard
can price actions against it.

This module adds no parsing, validation, or writes of its own --
``yigdesk/session.py`` and ``yigdesk/importer.py`` already own that
work and are left untouched. It is a thin, additive adapter only.
"""

from __future__ import annotations

from pathlib import Path

from yigdesk.session import load_active_session

from .model_source import ModelSource


def source_for_active_session(
    runtime_root: Path | str, input_refs: dict[str, str]
) -> ModelSource:
    """Resolve the active bound session and wrap it as a ModelSource.

    ``runtime_root`` is the same directory used with
    ``yigdesk.session.bind_synthetic_session`` /
    ``yigdesk.session.load_active_session`` -- it holds
    ``active-session.json`` and the immutable ``sessions/<id>/`` tree.

    ``input_refs`` maps evaluator input names to ``"Sheet!Cell"``
    addresses inside the session's *projection* workbook (see
    ``yigdesk.workbook.create_workbook`` for its exact, fixed sheet
    and cell layout, e.g. ``"Deal Inputs!B2"``). The projection
    workbook is used -- not the raw uploaded ``source.xlsx`` --
    because it is the addressable, named-cell view that
    ``ModelSource`` and ``ExpressionEvaluator`` expect; the raw
    upload is a free-form ledger extract with no such fixed cell
    layout.

    ``load_active_session`` already re-verifies the projection file's
    bytes against the session manifest's recorded fingerprint before
    returning ``BoundSession``, so the workbook handed to
    ``ModelSource`` here is already known-good.
    """

    session = load_active_session(runtime_root)
    return ModelSource(session.projection_path, input_refs)
