"""Claude-Code-Hooks für broking.

    python tools/hooks.py pre    # PreToolUse: Exit 2 = blockiert (Grund auf stderr geht an Claude)
    python tools/hooks.py post   # PostToolUse: Aufruf + Ergebnis ins Aktionsprotokoll

Grundsatz: Claude schlägt vor und legt Thesen an, handelt aber nicht. Orders gibt Timo in der
Weboberfläche frei. Deshalb blockiert der Pre-Hook
- jedes schreibende Tool eines Broker-MCPs (Bitpanda, Alpaca, Fusion …),
- direkte Änderungen an Konfiguration, Depot und Journal (Edit/Write und Shell-Umleitungen),
- Lesen der Schlüsseldatei.
Er arbeitet „fail closed“: Was er nicht versteht, blockiert er.
"""
from __future__ import annotations

import json
import re
import sys

try:
    import journal
except Exception as _e:  # Importfehler = Exit 1 = Claude Code ließe durch → im pre-Modus blockieren
    if "pre" in sys.argv[1:2]:
        print(f"Hook kann nicht laden, Aufruf blockiert: {_e}", file=sys.stderr)
        sys.exit(2)
    raise

PROTECTED = ("config.json", "paper_account.json", "portfolio.json", "theses.jsonl", "actions.jsonl",
             "secrets.json", "risk_check.py", "hooks.py", "settings.json")
BROKER_MCP = re.compile(r"^mcp__.*(bitpanda|alpaca|fusion)", re.I)
READ_PREFIXES = ("get_", "list_", "search_", "read_")
_P = "|".join(re.escape(p) for p in PROTECTED)
SHELL_WRITE = re.compile(
    rf">>?\s*['\"]?[^\s;&|]*({_P})"          # Umleitung AUF eine geschützte Datei
    rf"|\b(tee|rm|mv|cp|truncate|chmod|sed\s+-i)\b[^;&|]*({_P})"
    rf"|python3?\s+-c\b[^;&|]*({_P})"
    rf"|<<"                                   # Heredocs nicht prüfbar → blockieren, wenn geschützt erwähnt
)


def _block(msg: str) -> tuple[int, str]:
    return 2, f"Hook blockiert: {msg}"


def pre(event: dict) -> tuple[int, str]:
    """Gibt (exit_code, meldung) zurück."""
    tool = str(event.get("tool_name", ""))
    tin = event.get("tool_input") or {}
    if not isinstance(tin, dict):
        return _block("unlesbare Tool-Eingabe")

    if BROKER_MCP.search(tool):
        short = tool.rsplit("__", 1)[-1].lower()
        if short.startswith(READ_PREFIXES):
            return 0, ""
        journal.log_action({"hook": "pre", "tool": tool, "input": tin, "note": "blockiert"})
        return _block(f"{tool} – Claude handelt nicht. Order-Vorschläge als Tabelle ausgeben, "
                      "Timo gibt sie in der Weboberfläche frei.")

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(tin.get("file_path") or tin.get("notebook_path") or "").replace("\\", "/")
        if path.endswith(PROTECTED) or "/data/" in path or "/journal/" in path:
            return _block(f"{path} ist geschützt (Limits, Depot und Journal ändert nur die App bzw. Timo).")

    if tool == "Read":
        if str(tin.get("file_path", "")).replace("\\", "/").endswith("secrets.json"):
            return _block("Schlüsseldatei wird nicht gelesen.")

    if tool == "Bash":
        cmd = str(tin.get("command", ""))
        if any(p in cmd for p in PROTECTED) and SHELL_WRITE.search(cmd):
            return _block("Schreibzugriff auf Limits, Depot oder Journal per Shell.")
    return 0, ""


def post(event: dict) -> None:
    resp = event.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    journal.log_action({"hook": "post", "tool": event.get("tool_name"),
                        "input": event.get("tool_input"), "response": text[:2000]})


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = None
    if mode == "pre":
        if not isinstance(event, dict):
            print("Hook: ungültiges Hook-JSON", file=sys.stderr)
            sys.exit(2)
        try:
            code, msg = pre(event)
        except Exception as e:  # Absturz = Exit 1 = Claude Code lässt durch → deshalb blockieren
            code, msg = 2, f"Hook-Fehler, Aufruf blockiert: {type(e).__name__}: {e}"
        if msg:
            print(msg, file=sys.stderr)
        sys.exit(code)
    elif mode == "post":
        if isinstance(event, dict):
            try:
                post(event)
            except OSError as e:  # Protokollfehler darf den Lauf nicht stoppen, aber sichtbar sein
                print(f"Journal-Hook: {e}", file=sys.stderr)
    else:
        print("Aufruf: hooks.py pre|post", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
