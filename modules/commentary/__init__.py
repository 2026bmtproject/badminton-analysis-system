"""Grounded commentary contracts, runtime, and production orchestration."""

from modules.commentary.module import (
    CommentaryBatchResult,
    CommentaryModule,
    generate_commentary_segments,
)

__all__ = [
    "CommentaryBatchResult",
    "CommentaryModule",
    "generate_commentary_segments",
]
