"""Generate the checked-in Review export JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

from modules.review_export.contracts import (
    ARTIFACT_MODELS, CommentaryArtifact, ReviewExport, SegmentCommentaryArtifact,
)


def schema_text() -> str:
    return json.dumps(ReviewExport.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def artifact_schema_text() -> str:
    bundle = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "title": "Badminton pipeline artifact envelopes",
              "$defs": {
                  **{name: model.model_json_schema() for name, model in ARTIFACT_MODELS.items()},
                  "commentary": CommentaryArtifact.model_json_schema(),
                  "commentary_segment": SegmentCommentaryArtifact.model_json_schema(),
              }}
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    directory = Path(__file__).resolve().parents[2] / "ui" / "src" / "data"
    (directory / "review-export.schema.json").write_text(schema_text(), encoding="utf-8")
    (directory / "artifact-contracts.schema.json").write_text(artifact_schema_text(), encoding="utf-8")


if __name__ == "__main__":
    main()
