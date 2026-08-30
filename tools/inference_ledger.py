"""Persist AI inference results safely between collector runs."""
from __future__ import annotations
import json, os, tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Tuple

VERSION = 1
@dataclass(frozen=True)
class InferenceLedgerEntry:
    workspace_id: str
    session_id: str
    turn_id: str
    input_sha256: str
    result: Mapping[str, object]
    generated_at: str
    inference_kind: str

class InvalidInferenceLedgerError(ValueError): pass

def load(path: Path) -> Tuple[InferenceLedgerEntry, ...]:
    if not path.exists(): return tuple()
    try: value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error: raise InvalidInferenceLedgerError('ledger_unreadable') from error
    if not isinstance(value,dict) or value.get('version') != VERSION or not isinstance(value.get('entries'),list): raise InvalidInferenceLedgerError('ledger_invalid')
    return tuple(_entry(item) for item in value['entries'])

def append(path: Path, entry: InferenceLedgerEntry) -> Tuple[InferenceLedgerEntry, ...]:
    entries = tuple(item for item in load(path) if _key(item) != _key(entry)) + (entry,)
    path.parent.mkdir(parents=True, exist_ok=True)
    data=(json.dumps({'version':VERSION,'entries':[_value(item) for item in entries]},ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=str(path.parent)); temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    except BaseException: temp.unlink(missing_ok=True);raise
    return entries

def _key(e): return (e.workspace_id,e.session_id,e.turn_id,e.inference_kind,e.input_sha256)
def _value(e): return {'workspace_id':e.workspace_id,'session_id':e.session_id,'turn_id':e.turn_id,'input_sha256':e.input_sha256,'result':dict(e.result),'generated_at':e.generated_at,'inference_kind':e.inference_kind}
def _entry(v):
    if not isinstance(v,dict): raise InvalidInferenceLedgerError('ledger_entry_invalid')
    keys=('workspace_id','session_id','turn_id','input_sha256','generated_at','inference_kind')
    if not all(isinstance(v.get(k),str) and v[k] for k in keys) or len(v['input_sha256'])!=64 or not isinstance(v.get('result'),dict): raise InvalidInferenceLedgerError('ledger_entry_invalid')
    return InferenceLedgerEntry(**v)
