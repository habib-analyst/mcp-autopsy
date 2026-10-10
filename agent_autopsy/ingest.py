"""Trace ingesters: normalize OpenAI / Anthropic / generic-JSONL logs into
the canonical event schema.

Canonical event:
    {"step": int, "role": "user"|"assistant"|"tool",
     "kind": "message"|"tool_call"|"tool_result",
     "text": str, "tool": str|None, "args": dict|None,
     "error": bool, "duration_s": float|None}
"""
import json


def _ev(step, role, kind, text="", tool=None, args=None, error=False,
        duration_s=None):
    return {"step": step, "role": role, "kind": kind, "text": text or "",
            "tool": tool, "args": args, "error": bool(error),
            "duration_s": duration_s}


def ingest_jsonl(path):
    """Generic JSONL: one event per line. Tolerant of extra/missing fields."""
    events = []
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            events.append(_ev(
                step=obj.get("step", i),
                role=obj.get("role", "assistant"),
                kind=obj.get("kind", "message"),
                text=obj.get("text", obj.get("output", obj.get("content", ""))),
                tool=obj.get("tool"),
                args=obj.get("args", obj.get("input")),
                error=obj.get("error", False),
                duration_s=obj.get("duration_s"),
            ))
    events.sort(key=lambda e: e["step"])
    return events


def _as_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "output_text"):
                parts.append(b.get("text", ""))
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                parts.append(_as_text(c) if not isinstance(c, str) else c)
        return "\n".join(p for p in parts if p)
    return str(content)


def ingest_openai(obj):
    """OpenAI chat-completions shape: {"messages": [{role, content,
    tool_calls: [{id, function: {name, arguments}}]}, ...]}. Tool results are
    messages with role "tool" (matched by tool_call_id when present)."""
    events = []
    pending = {}  # tool_call_id -> (step, name)
    step = 0
    for m in obj.get("messages", []):
        role = m.get("role", "assistant")
        if role == "tool":
            name = None
            tcid = m.get("tool_call_id")
            if tcid in pending:
                _, name = pending.pop(tcid)
            events.append(_ev(step, "tool", "tool_result",
                              text=_as_text(m.get("content")),
                              tool=name or m.get("name"),
                              error=False))
            step += 1
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {"_raw": fn.get("arguments")}
            pending[tc.get("id")] = (step, name)
            events.append(_ev(step, "assistant", "tool_call", tool=name,
                              args=args if isinstance(args, dict) else {"_raw": args}))
            step += 1
        text = _as_text(m.get("content"))
        if text or role == "user":
            events.append(_ev(step, role, "message", text=text))
            step += 1
    return events


def ingest_anthropic(obj):
    """Anthropic messages shape: content blocks of type text / tool_use /
    tool_result."""
    events = []
    pending = {}  # tool_use id -> name
    step = 0
    for m in obj.get("messages", []):
        role = m.get("role", "assistant")
        content = m.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        texts = []
        for b in content:
            btype = b.get("type") if isinstance(b, dict) else None
            if btype == "text":
                texts.append(b.get("text", ""))
            elif btype == "tool_use":
                name = b.get("name")
                pending[b.get("id")] = name
                events.append(_ev(step, "assistant", "tool_call", tool=name,
                                  args=b.get("input") if isinstance(
                                      b.get("input"), dict) else {}))
                step += 1
            elif btype == "tool_result":
                name = pending.pop(b.get("tool_use_id"), None)
                c = b.get("content")
                out = c if isinstance(c, str) else _as_text(c)
                events.append(_ev(step, "tool", "tool_result", text=out,
                                  tool=name, error=bool(b.get("is_error"))))
                step += 1
        if texts or role == "user":
            events.append(_ev(step, role, "message",
                              text="\n".join(t for t in texts if t)))
            step += 1
    return events


def ingest(path):
    """Auto-detect format from file content."""
    with open(path) as f:
        raw = f.read().strip()
    if not raw:
        return []
    # JSONL: every non-empty line parses as JSON
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    try:
        objs = [json.loads(ln) for ln in lines]
        if len(objs) > 1 or "messages" not in objs[0]:
            return ingest_jsonl(path)
        obj = objs[0]
    except ValueError:
        obj = json.loads(raw)
    msgs = obj.get("messages", [])
    # provider sniff: anthropic uses content-block lists
    for m in msgs:
        c = m.get("content")
        if isinstance(c, list) and any(
                isinstance(b, dict) and b.get("type") in
                ("tool_use", "tool_result") for b in c):
            return ingest_anthropic(obj)
    return ingest_openai(obj)
