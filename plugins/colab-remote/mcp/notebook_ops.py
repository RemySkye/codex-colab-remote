"""Small dependency-free notebook editing helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from typing import Any


LANGUAGE_INFO = {
    "python": {"name": "python", "pygments_lexer": "ipython3"},
    "r": {"name": "R", "pygments_lexer": "r"},
    "julia": {"name": "julia", "pygments_lexer": "julia"},
}


def new_notebook(language: str = "python", title: str | None = None) -> dict[str, Any]:
    if language not in LANGUAGE_INFO:
        raise ValueError("Notebook language must be python, r, or julia")
    notebook: dict[str, Any] = {
        "cells": [],
        "metadata": {
            "kernelspec": {
                "display_name": language.capitalize(),
                "language": language,
                "name": language,
            },
            "language_info": LANGUAGE_INFO[language],
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    if title:
        notebook["metadata"]["title"] = title
    return notebook


def validate(notebook: Any) -> dict[str, Any]:
    if not isinstance(notebook, dict):
        raise ValueError("Notebook must be a JSON object")
    if notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
        raise ValueError("Only nbformat 4 notebooks are supported")
    for cell in notebook["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") not in {
            "code",
            "markdown",
            "raw",
        }:
            raise ValueError("Notebook contains an invalid cell")
        if "source" not in cell:
            cell["source"] = ""
    notebook.setdefault("metadata", {})
    notebook.setdefault("nbformat_minor", 5)
    return notebook


def load(path: Path) -> dict[str, Any]:
    return validate(json.loads(path.read_text(encoding="utf-8")))


def save(path: Path, notebook: dict[str, Any]) -> None:
    validate(notebook)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(notebook, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def source_text(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def make_cell(cell_type: str, source: str) -> dict[str, Any]:
    if cell_type not in {"code", "markdown", "raw"}:
        raise ValueError("cell_type must be code, markdown, or raw")
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "id": secrets.token_hex(4),
        "metadata": {},
        "source": source,
    }
    if cell_type == "code":
        cell.update({"execution_count": None, "outputs": []})
    return cell


def get_cell(notebook: dict[str, Any], index: int) -> dict[str, Any]:
    cells = notebook["cells"]
    if not -len(cells) <= index < len(cells):
        raise IndexError(f"Cell index out of range: {index}")
    return cells[index]


def add_cell(
    notebook: dict[str, Any], cell_type: str, source: str, index: int | None = None
) -> int:
    cell = make_cell(cell_type, source)
    if index is None:
        notebook["cells"].append(cell)
        return len(notebook["cells"]) - 1
    if not 0 <= index <= len(notebook["cells"]):
        raise IndexError(f"Cell insertion index out of range: {index}")
    notebook["cells"].insert(index, cell)
    return index


def edit_cell(
    notebook: dict[str, Any], index: int, source: str, cell_type: str | None = None
) -> None:
    cell = get_cell(notebook, index)
    if cell_type and cell_type != cell["cell_type"]:
        replacement = make_cell(cell_type, source)
        replacement["id"] = cell.get("id", replacement["id"])
        notebook["cells"][index] = replacement
    else:
        cell["source"] = source
        if cell["cell_type"] == "code":
            cell["execution_count"] = None
            cell["outputs"] = []


def delete_cell(notebook: dict[str, Any], index: int) -> dict[str, Any]:
    get_cell(notebook, index)
    return notebook["cells"].pop(index)


def move_cell(
    notebook: dict[str, Any], source_index: int, destination_index: int
) -> None:
    cell = get_cell(notebook, source_index)
    if not 0 <= destination_index < len(notebook["cells"]):
        raise IndexError(f"Cell destination index out of range: {destination_index}")
    notebook["cells"].pop(source_index)
    notebook["cells"].insert(destination_index, cell)


def summary(notebook: dict[str, Any], include_outputs: bool = True) -> dict[str, Any]:
    cells = []
    for index, cell in enumerate(notebook["cells"]):
        row = {
            "index": index,
            "id": cell.get("id"),
            "cell_type": cell["cell_type"],
            "source": source_text(cell),
        }
        if cell["cell_type"] == "code":
            row["execution_count"] = cell.get("execution_count")
            if include_outputs:
                row["outputs"] = cell.get("outputs", [])
        cells.append(row)
    return {
        "nbformat": notebook["nbformat"],
        "nbformat_minor": notebook.get("nbformat_minor"),
        "metadata": notebook.get("metadata", {}),
        "cell_count": len(cells),
        "cells": cells,
    }
