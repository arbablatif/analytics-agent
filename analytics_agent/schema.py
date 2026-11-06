"""Schema inspection: read the live structure from DuckDB and render it for the prompt.

The structure always comes from the database itself, so the prompt cannot drift out of
step with the data. The YAML files add only meaning, and only in the enhanced condition.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from .config import ALLOWED_TABLES, SEMANTIC_DIR
from .db import ReadOnlyDB


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str


def inspect_schema(db: ReadOnlyDB) -> dict[str, list[Column]]:
    """Return {table: [columns]} from DuckDB's own catalog."""
    # Trusted code, not model output: the catalog is deliberately off limits to the agent.
    rows = db.con.cursor().execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position").fetchall()
    schema: dict[str, list[Column]] = {}
    for table, column, dtype in rows:
        if table in ALLOWED_TABLES:
            schema.setdefault(table, []).append(Column(column, dtype))
    return schema


def load_semantics() -> dict:
    return yaml.safe_load((SEMANTIC_DIR / "schema_descriptions.yaml").read_text(encoding="utf-8"))


def load_few_shot() -> list[dict]:
    return yaml.safe_load((SEMANTIC_DIR / "few_shot.yaml").read_text(encoding="utf-8"))["examples"]


def render_plain(schema: dict[str, list[Column]]) -> str:
    """Bare DDL-style schema: table and column names with types, nothing else."""
    blocks = []
    for table, columns in schema.items():
        body = ",\n".join(f"  {c.name} {c.dtype}" for c in columns)
        blocks.append(f"CREATE TABLE {table} (\n{body}\n);")
    return "\n\n".join(blocks)


def render_enhanced(schema: dict[str, list[Column]]) -> str:
    """Schema annotated with descriptions, metric definitions and known pitfalls."""
    semantics = load_semantics()
    parts = []
    for table, columns in schema.items():
        info = semantics["tables"].get(table, {})
        lines = [f"TABLE {table}: {info.get('description', '')}"]
        for column in columns:
            note = info.get("columns", {}).get(column.name, "")
            lines.append(f"  - {column.name} ({column.dtype}): {note}".rstrip(": "))
        parts.append("\n".join(lines))
    metrics = "\n".join(f"- {name}: {text.strip()}" for name, text in semantics["metrics"].items())
    pitfalls = "\n".join(f"- {text.strip()}" for text in semantics["pitfalls"])
    return "\n\n".join(parts) + f"\n\nMETRIC DEFINITIONS\n{metrics}\n\nPITFALLS\n{pitfalls}"


def render_few_shot() -> str:
    blocks = []
    for example in load_few_shot():
        blocks.append(f"Question: {example['question']}\nSQL:\n{example['sql'].strip()}\n"
                      f"Note: {example['note'].strip()}")
    return "\n\n".join(blocks)
