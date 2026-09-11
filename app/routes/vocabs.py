"""Vocabulary routes: import (upload), guarded URL fetch, list, detail, delete.

Raw JSON-LD is stored under ``data_dir/vocabs/<name>.json``; parsing happens on
demand (SkoHub files are small). Validation runs BEFORE anything is written.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..atomic import write_text_atomic
from ..fetch import FetchError, fetch_json
from ..security import read_upload_capped, require_key, safe_name
from ..settings import Settings, get_settings
from ..vocab import Vocabulary, parse_vocabulary
from ..vocab_formats import manual_to_jsonld
from ..vocab_turtle import turtle_to_jsonld

router = APIRouter(prefix="/vocabs", tags=["Vocabularies"], dependencies=[Depends(require_key)])


class FetchRequest(BaseModel):
    url: str = Field(max_length=2000)
    name: str | None = Field(default=None, max_length=100)
    # Optional WLO metadata field these labels belong to (e.g. the taxonid /
    # educationalcontext column). Recorded now, useful downstream for mapping
    # the vocabulary's concepts to a dataset column.
    label_field: str | None = Field(default=None, max_length=200)


class ManualRequest(BaseModel):
    """A vocabulary typed/pasted by hand — one concept per line."""

    name: str = Field(max_length=100)
    text: str = Field(max_length=200_000)
    lang: str = Field(default="de", max_length=10)
    title: str | None = Field(default=None, max_length=200)
    base_uri: str | None = Field(default=None, max_length=400)
    label_field: str | None = Field(default=None, max_length=200)


def _vocab_dir(settings: Settings) -> Path:
    directory = settings.data_dir / "vocabs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _vocab_path(settings: Settings, name: str) -> Path:
    return _vocab_dir(settings) / f"{safe_name(name, 'vocabulary name')}.json"


def _field_path(settings: Settings, name: str) -> Path:
    return _vocab_dir(settings) / f"{safe_name(name, 'vocabulary name')}.field.txt"


def _read_field(settings: Settings, name: str) -> str:
    path = _field_path(settings, name)
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _display_title(vocab: Vocabulary) -> str:
    for lang in ("de", "en"):
        if lang in vocab.title:
            return vocab.title[lang]
    return next(iter(vocab.title.values()), "")


def _store(settings: Settings, name: str, raw: dict, label_field: str | None = None) -> dict:
    try:
        vocab = parse_vocabulary(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = _vocab_path(settings, name)  # safe_name may 400 — before any write
    write_text_atomic(path, json.dumps(raw, ensure_ascii=False))
    field = (label_field or "").strip()
    write_text_atomic(_field_path(settings, name), field)
    return {"name": name, "title": _display_title(vocab),
            "concept_count": len(vocab.concepts), "label_field": field}


def _load(settings: Settings, name: str) -> Vocabulary:
    path = _vocab_path(settings, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary {name!r} not found.")
    return parse_vocabulary(json.loads(path.read_text(encoding="utf-8")))


def _name_from_url(url: str) -> str:
    """SkoHub convention: .../vocabs/<name>/index.json -> <name>."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    last = parts[-1] if parts else ""
    if last in ("index.json", "index.jsonld") and len(parts) >= 2:
        return parts[-2]
    return last.removesuffix(".jsonld").removesuffix(".json") or "vocabulary"


@router.get("", summary="List loaded vocabularies")
async def list_vocabs(settings: Settings = Depends(get_settings)) -> dict:
    vocabularies = []
    for path in sorted(_vocab_dir(settings).glob("*.json")):
        vocab = parse_vocabulary(json.loads(path.read_text(encoding="utf-8")))
        vocabularies.append(
            {"name": path.stem, "title": _display_title(vocab),
             "concept_count": len(vocab.concepts), "label_field": _read_field(settings, path.stem)}
        )
    return {"vocabularies": vocabularies}


@router.post("/import", summary="Upload a vocabulary file (SKOS JSON-LD or SkoHub Turtle)")
async def import_vocab(
    file: UploadFile,
    name: str | None = Form(default=None, max_length=100),
    label_field: str | None = Form(default=None, max_length=200),
    settings: Settings = Depends(get_settings),
) -> dict:
    payload = await read_upload_capped(file, settings.max_upload_mb * 1024 * 1024)
    resolved = name or Path(file.filename or "vocabulary").stem
    suffix = Path(file.filename or "").suffix.lower()
    if suffix in (".ttl", ".turtle"):
        try:
            raw = turtle_to_jsonld(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid Turtle: {exc}") from exc
        return _store(settings, resolved, raw, label_field)
    if suffix in (".json", ".jsonld", ""):  # no extension -> treat as JSON (back-compat)
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="File is not valid JSON.") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="File is not a JSON object.")
        return _store(settings, resolved, raw, label_field)
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type {suffix!r}. Use .json, .jsonld or .ttl.",
    )


@router.post("/manual", summary="Create a vocabulary from a pasted concept list")
async def manual_vocab(req: ManualRequest, settings: Settings = Depends(get_settings)) -> dict:
    name = safe_name(req.name, "vocabulary name")  # may 400 — before any write
    base_uri = (req.base_uri or f"urn:dataprep:{name}").strip()
    try:
        raw = manual_to_jsonld(req.text, title=req.title or req.name, lang=req.lang, base_uri=base_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _store(settings, name, raw, req.label_field)


@router.post("/fetch", summary="Fetch a vocabulary from an allowed HTTPS URL")
async def fetch_vocab(req: FetchRequest, settings: Settings = Depends(get_settings)) -> dict:
    try:
        # fetch_json is synchronous (httpx.Client, up to fetch_timeout_seconds);
        # a slow upstream must not hold the event loop for that long.
        raw = await asyncio.to_thread(fetch_json, req.url, settings)
    except FetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _store(settings, req.name or _name_from_url(req.url), raw, req.label_field)


@router.get("/{name}", summary="Vocabulary details with concept tree")
async def vocab_detail(name: str, settings: Settings = Depends(get_settings)) -> dict:
    vocab = _load(settings, name)
    return {
        "name": name,
        "scheme_uri": vocab.scheme_uri,
        "title": _display_title(vocab),
        "concept_count": len(vocab.concepts),
        "label_field": _read_field(settings, name),
        "languages": sorted(vocab.languages()),
        "tree": [
            {"uri": uri, "label": vocab.label(uri), "depth": depth} for uri, depth in vocab.tree()
        ],
    }


@router.delete("/{name}", summary="Delete a vocabulary")
async def delete_vocab(name: str, settings: Settings = Depends(get_settings)) -> dict:
    path = _vocab_path(settings, name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary {name!r} not found.")
    path.unlink()
    _field_path(settings, name).unlink(missing_ok=True)
    return {"deleted": name}
