"""Generate a synthetic chunked message without external dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CHUNK_BYTES = 256 * 1024
SESSION_ID = "00000000-0000-7000-8000-000000000001"
MESSAGE_ID = "msg_00000000-0000-7000-8000-000000000999"


def split_utf8(text: str, maximum: int) -> list[str]:
    """Split text without cutting a UTF-8 code point."""
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for character in text:
        encoded_size = len(character.encode("utf-8"))
        if current and current_size + encoded_size > maximum:
            chunks.append("".join(current))
            current = []
            current_size = 0
        current.append(character)
        current_size += encoded_size
    if current:
        chunks.append("".join(current))
    return chunks


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    text = ("これは分割確認用の架空メッセージです。\n" * 12_000) + "末尾です。\n"
    encoded = text.encode("utf-8")
    chunks = split_utf8(text, CHUNK_BYTES)
    references = []

    for number, chunk in enumerate(chunks, start=1):
        filename = f"{MESSAGE_ID}-part-{number:06d}.json"
        payload = {
            "schema_version": "1.0",
            "data_type": "message_chunk",
            "session_id": SESSION_ID,
            "message_id": MESSAGE_ID,
            "part": number,
            "total_parts": len(chunks),
            "text": chunk,
        }
        write_json(args.output / "messages" / "chunks" / filename, payload)
        references.append(
            {
                "part": number,
                "path": f"messages/chunks/{filename}",
                "text_byte_size": len(chunk.encode("utf-8")),
            }
        )

    index = {
        "schema_version": "1.0",
        "data_type": "large_sample_index",
        "session_id": SESSION_ID,
        "message_id": MESSAGE_ID,
        "original_byte_size": len(encoded),
        "chunk_count": len(chunks),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "chunks": references,
    }
    write_json(args.output / "index.json", index)


if __name__ == "__main__":
    main()
