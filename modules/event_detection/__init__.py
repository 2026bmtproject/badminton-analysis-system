"""擊球偵測 — find the frame of every hit in every rally.

Two shuttle trajectories and BST's frame-by-frame stroke probabilities go in;
``events.json``, a list of hit frames, comes out. See ``module.py`` for the phase split
and ``config.py`` for every knob.
"""

from modules.event_detection.config import EventDetectionConfig
from modules.event_detection.module import EventDetectionModule

__all__ = ["EventDetectionConfig", "EventDetectionModule"]
