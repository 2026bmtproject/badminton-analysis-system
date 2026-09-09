"""Bounded record reads for large pose artifacts.

Parse the complete top-level envelope (including its tail), retaining one array
record at a time. Unlike key-search readers this rejects nested-key impostors,
truncation, trailing data, missing separators, and duplicate envelope keys.
Individual values use the standard JSON decoder; no extra artifact framework.
"""

import json
from pathlib import Path
from collections.abc import Iterator


class _Reader:
    def __init__(self, handle):
        self.handle = handle
        self.buffer = ""
        self.index = 0

    def peek(self):
        if self.index == len(self.buffer):
            self.buffer = self.handle.read(65536)
            self.index = 0
        return self.buffer[self.index:self.index + 1]

    def take(self):
        char = self.peek()
        if not char:
            raise ValueError("truncated JSON envelope")
        self.index += 1
        return char

    def whitespace(self):
        while self.peek() and self.peek() in " \r\n\t":
            self.take()

    def expect(self, char):
        self.whitespace()
        if self.take() != char:
            raise ValueError(f"expected JSON separator {char!r}")

    def value(self):
        self.whitespace()
        chars = []
        depth = 0
        quoted = escaped = False
        while self.peek():
            char = self.peek()
            if not quoted and depth == 0 and chars and char in ",]}: \r\n\t":
                break
            char = self.take()
            chars.append(char)
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                    if depth == 0:
                        break
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    break
        def reject(value):
            raise ValueError(f"non-JSON constant {value}")
        return json.loads("".join(chars), parse_constant=reject)


def iter_records(path: str | Path, key: str) -> Iterator[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        reader = _Reader(handle)
        reader.expect("{")
        seen = set()
        reader.whitespace()
        if reader.peek() != "}":
            while True:
                name = reader.value()
                if not isinstance(name, str) or name in seen:
                    raise ValueError("invalid or duplicate envelope key")
                seen.add(name)
                reader.expect(":")
                if name == key:
                    reader.expect("[")
                    reader.whitespace()
                    if reader.peek() != "]":
                        while True:
                            item = reader.value()
                            if not isinstance(item, dict):
                                raise ValueError("artifact record must be an object")
                            yield item
                            reader.whitespace()
                            if reader.peek() == "]":
                                break
                            reader.expect(",")
                    reader.expect("]")
                else:
                    reader.value()
                reader.whitespace()
                if reader.peek() == "}":
                    break
                reader.expect(",")
        reader.expect("}")
        reader.whitespace()
        if reader.peek() or key not in seen:
            raise ValueError("invalid envelope: trailing data or missing record array")
