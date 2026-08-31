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

@dataclass(frozen=True)
class CombinedTurnCacheEntry:
    input_sha256: str
    summary: object
    decision_proposals: Tuple[object, ...]
    next_task_cache_entry: object
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

def summary_payload(summary):
    return {"summary_id":summary.summary_id,"turn_id":summary.turn_id,"turn_id_source":summary.turn_id_source,"status":summary.status,"rolled_back":summary.rolled_back,"title":summary.title,"short_summary":summary.short_summary,"details":summary.details,"highlights":[{"text":x.text,"source_message_ids":list(x.source_message_ids)} for x in summary.highlights],"verification":[{"text":x.text,"source_message_ids":list(x.source_message_ids)} for x in summary.verification],"origin":summary.origin,"confidence":summary.confidence,"source_session_ids":list(summary.source_session_ids),"source_message_ids":list(summary.source_message_ids)}

def summary_cache_entries(entries, workspace_id, session_id):
    from tools.change_summary_generator import ChangeSummary, ChangeSummaryCacheEntry, SummaryEvidenceItem
    result=[]
    source_entries = _combined_subentries(entries, "change_summary", "change_summary") + tuple(
        entry for entry in entries if entry.inference_kind == "change_summary"
    )
    for entry in source_entries:
        if entry.workspace_id != workspace_id or entry.session_id != session_id: continue
        value=entry.result.get('payload') if isinstance(entry.result,dict) and entry.result.get('schema_version') == 1 else None
        if not isinstance(value,dict): continue
        try:
            items=lambda name: tuple(SummaryEvidenceItem(str(x['text']),tuple(x['source_message_ids'])) for x in value[name])
            summary=ChangeSummary(value['summary_id'],value['turn_id'],value['turn_id_source'],value['status'],value['rolled_back'],value['title'],value['short_summary'],value['details'],items('highlights'),items('verification'),value['origin'],value['confidence'],tuple(value['source_session_ids']),tuple(value['source_message_ids']))
            result.append(ChangeSummaryCacheEntry(entry.turn_id,entry.input_sha256,summary,None,tuple()))
        except (KeyError,TypeError): continue
    return tuple(result)

def decision_inference_cache(entries, workspace_id):
    """Restore only complete, versioned decision CLI results from the ledger."""
    result={}
    source_entries = tuple(entry for entry in entries if entry.inference_kind == "decision") + _combined_subentries(entries, "decision", "decision")
    for entry in source_entries:
        if entry.workspace_id != workspace_id:
            continue
        try:
            result[entry.input_sha256] = _decision_proposals(entry)
        except ValueError:
            continue
    return result

def decision_inference_entries(entries, workspace_id):
    '''Restore complete individual entries keyed by session, turn, and SHA-256.'''
    from tools.decision_extractor import decision_inference_cache_entry_from_payload

    result = {}
    for entry in entries:
        if entry.workspace_id != workspace_id or entry.inference_kind != 'decision':
            continue
        try:
            cache_entry = decision_inference_cache_entry_from_payload(
                entry.result,
                entry.session_id,
                entry.turn_id,
                entry.input_sha256,
            )
        except (RuntimeError, ValueError):
            continue
        result[(entry.session_id, entry.turn_id, entry.input_sha256)] = cache_entry
    return result


def latest_decision_history(entries, workspace_id):
    '''Restore the newest complete individual or combined decision history.'''
    candidates = []
    for position, entry in enumerate(entries):
        if entry.workspace_id != workspace_id:
            continue
        cache_entry = _complete_decision_cache_entry(entry)
        if cache_entry is None:
            continue
        candidates.append(
            (_generated_at_sort_key(entry.generated_at, position), cache_entry.decisions)
        )
    return max(candidates, default=(None, tuple()), key=lambda item: item[0])[1]


def _complete_decision_cache_entry(entry):
    from tools.decision_extractor import decision_inference_cache_entry_from_payload

    if entry.inference_kind == 'decision':
        source_entry = entry
    elif entry.inference_kind == 'combined_turn':
        subentries = _combined_subentries((entry,), 'decision', 'decision')
        if len(subentries) != 1:
            return None
        source_entry = subentries[0]
    else:
        return None
    try:
        return decision_inference_cache_entry_from_payload(
            source_entry.result,
            source_entry.session_id,
            source_entry.turn_id,
            source_entry.input_sha256,
        )
    except (RuntimeError, ValueError):
        return None


def _generated_at_sort_key(value, position):
    try:
        generated_at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        return (1, generated_at.astimezone(timezone.utc), position)
    except ValueError:
        return (0, datetime.min.replace(tzinfo=timezone.utc), position)


def combined_turn_cache_entry(entries, workspace_id, session_id, turn_id, input_sha256):
    """Restore a complete combined result only for the exact prompt SHA-256."""
    for entry in reversed(tuple(entries)):
        if (
            entry.workspace_id != workspace_id
            or entry.session_id != session_id
            or entry.turn_id != turn_id
            or entry.input_sha256 != input_sha256
            or entry.inference_kind != "combined_turn"
        ):
            continue
        subentries = _combined_subentries((entry,), "change_summary", "change_summary")
        decisions = _combined_subentries((entry,), "decision", "decision")
        tasks = _combined_subentries((entry,), "next_task", "next_task")
        if len(subentries) != 1 or len(decisions) != 1 or len(tasks) != 1:
            continue
        summaries = summary_cache_entries(subentries, workspace_id, session_id)
        task_cache = next_task_cache_entry(tasks, workspace_id, session_id)
        if len(summaries) != 1 or task_cache is None:
            continue
        try:
            proposals = _decision_proposals(decisions[0])
        except ValueError:
            continue
        return CombinedTurnCacheEntry(input_sha256, summaries[0].summary, proposals, task_cache)
    return None


def _combined_subentries(entries, payload_name, inference_kind):
    result=[]
    for entry in entries:
        if entry.inference_kind != "combined_turn" or not isinstance(entry.result, dict) or entry.result.get("schema_version") != 1:
            continue
        payload = entry.result.get("payload")
        value = payload.get(payload_name) if isinstance(payload, dict) else None
        if not isinstance(value, dict):
            continue
        result.append(InferenceLedgerEntry(entry.workspace_id, entry.session_id, entry.turn_id, entry.input_sha256, value, entry.generated_at, inference_kind))
    return tuple(result)

def combined_turn_payload(summary, proposals, decision_history, next_task_entry):
    """Return versioned individual payloads for one atomically saved combined turn."""
    from tools.decision_extractor import complete_decision_inference_payload

    return {
        "schema_version": 1,
        "payload": {
            "change_summary": {"schema_version": 1, "payload": summary_payload(summary)},
            "decision": complete_decision_inference_payload(
                proposals, decision_history
            ),
            "next_task": next_task_payload(next_task_entry),
        },
    }


def _decision_proposals(entry):
    """Restore proposals from a complete v2 or legacy proposal-only v1 payload."""
    from tools.decision_extractor import (
        decision_inference_cache_entry_from_payload,
        proposals_from_inference_payload,
    )

    try:
        return decision_inference_cache_entry_from_payload(
            entry.result,
            entry.session_id,
            entry.turn_id,
            entry.input_sha256,
        ).proposals
    except ValueError:
        return proposals_from_inference_payload(entry.result)


def next_task_payload(entry):
    task = entry.task
    assert task is not None
    return {"schema_version": 1, "payload": {"evidence_hash": entry.evidence_hash, "task": {"task_id": task.task_id, "text": task.text, "status": task.status, "origin": task.origin, "confidence": task.confidence, "reason": task.reason, "source_message_ids": list(task.source_message_ids)}, "issues": [item.kind for item in entry.issues]}}

def next_task_cache_entry(entries, workspace_id, session_id):
    """Restore the latest complete successful next-task result for a session."""
    from tools.next_task_extractor import NextTask, NextTaskCacheEntry, NextTaskIssue
    source_entries = tuple(entry for entry in entries if entry.inference_kind == "next_task") + _combined_subentries(entries, "next_task", "next_task")
    for entry in reversed(source_entries):
        if entry.workspace_id != workspace_id or entry.session_id != session_id:
            continue
        value = entry.result.get("payload") if isinstance(entry.result, dict) and entry.result.get("schema_version") == 1 else None
        if not isinstance(value, dict) or not isinstance(value.get("evidence_hash"), str) or not isinstance(value.get("task"), dict) or not isinstance(value.get("issues"), list):
            continue
        task_value = value["task"]
        fields = ("task_id", "text", "status", "origin", "confidence")
        if not all(isinstance(task_value.get(field), str) and task_value[field] for field in fields):
            continue
        reason = task_value.get("reason")
        source_ids = task_value.get("source_message_ids")
        if (reason is not None and not isinstance(reason, str)) or not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids) or not all(isinstance(item, str) for item in value["issues"]):
            continue
        task = NextTask(task_value["task_id"], task_value["text"], task_value["status"], task_value["origin"], task_value["confidence"], reason, tuple(source_ids))
        return NextTaskCacheEntry(value["evidence_hash"], task, None, tuple(NextTaskIssue(item) for item in value["issues"]))
    return None
