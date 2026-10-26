# vscode_bridge: install (global hooks)

1. Backup `~/.claude/settings.json`, then merge: `python vscode_bridge/gen_settings.py --apply C:/Users/<you>/.claude/settings.json`
   (appends our command hooks to the existing arrays, replaces only its own earlier entries; idempotent). Without `--apply` it prints the block.
2. Hooks (all `python vscode_bridge/hook.py <endpoint>`): SessionStart, SessionEnd, Stop, PreToolUse(AskUserQuestion), PermissionRequest. No secret is in settings.json.
3. Safety: the hook always exits 0, prints only a decision JSON, and is a no-op in ~0.1 s when the widget is not running (`~/.voice-bridge/alive` heartbeat older than 12 s) or when `VOICE_BRIDGE_OFF` is set (the widget sets it for its own `claude -p` calls).
   SessionStart writes `~/.voice-bridge/sessions/<sid>.json` (messaging socket + token) even when the widget is down, so the widget finds sessions started earlier. `~/.voice-bridge` is ACL-restricted to the current user; tokens are never logged or passed to events.
4. Hold: permission/ask are held only if the sidecar is up AND the widget is ready, `bridge_hold` is on and speak_mode is immediate; otherwise the normal prompt appears at once. Max hold 45 s (cap 50, hook timeout 60), then it falls through.
5. Sessions started before the hooks were installed are not in the registry (restart them). Prompt injection: `send_to_session(sc, session_id, "text")`.
6. Rollback: restore `settings.json.bak-20261026-voice` or delete the entries whose command contains `vscode_bridge/hook.py`.
