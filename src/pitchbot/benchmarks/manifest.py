from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from pitchbot.benchmarks.models import CandidateRegistry, CorpusAvailability, CorpusManifest

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_AUDIO_BYTES = 512 * 1024 * 1024


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"invalid JSON constant: {constant}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


def load_json_model[T: BaseModel](path: Path, model_type: type[T]) -> T:
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    value = _load_json(raw)
    return model_type.model_validate(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_candidate_registry(path: Path) -> CandidateRegistry:
    registry = load_json_model(path, CandidateRegistry)
    identifiers = [candidate.candidate_id for candidate in registry.candidates]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("candidate identifiers must be unique")
    return registry


def validate_corpus_manifest(path: Path) -> CorpusManifest:
    manifest = load_json_model(path, CorpusManifest)
    identifiers = [item.item_id for item in manifest.items]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("corpus item identifiers must be unique")

    root = path.parent.resolve()
    for item in manifest.items:
        if item.availability not in {
            CorpusAvailability.GENERATED,
            CorpusAvailability.AVAILABLE,
        }:
            continue
        assert item.audio_path is not None
        assert item.audio_sha256 is not None
        audio_path = (root / item.audio_path).resolve()
        if not audio_path.is_relative_to(root):
            raise ValueError(f"audio path escapes manifest directory: {item.item_id}")
        if not audio_path.is_file():
            raise ValueError(f"audio file is missing: {item.item_id}")
        if audio_path.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError(f"audio file exceeds size limit: {item.item_id}")
        if sha256_file(audio_path) != item.audio_sha256:
            raise ValueError(f"audio checksum mismatch: {item.item_id}")
    return manifest


def canonical_manifest_sha256(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    value = _load_json(raw)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
