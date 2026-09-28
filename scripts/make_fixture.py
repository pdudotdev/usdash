#!/usr/bin/env python3
"""Copy a real Claude Code transcript into tests/fixtures/, with its text removed.

    python3 scripts/make_fixture.py <session-id-prefix> [name]

Keeps what usdash reads (record types, ids, parent links, timestamps, model,
effort, usage, titles, cost-state, compaction sizes, tool names, whether a tool
failed, WebSearch's search counts) and replaces everything people wrote or tools
returned: prompts become "prompt 1", "prompt 2"... (slash commands keep their
name, Claude Code's tagged text its tag), replies and tool output become empty.
The folder becomes /home/user/<folder name>. Subagent transcripts come along.
Check the output before committing it.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usdash.transcripts import default_projects_dir  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
KEEP = {
    "type", "subtype", "uuid", "parentUuid", "sessionId", "timestamp", "isSidechain", "isMeta", "isCompactSummary",
    "isApiErrorMessage", "entrypoint", "version", "cwd", "gitBranch", "requestId", "effort", "agentId",
    "customTitle", "aiTitle", "agentName", "totalCostUSD", "modelUsage",
}


class Redactor:
    def __init__(self) -> None:
        self.prompts = 0

    def text(self, text: str) -> str:
        if text.lstrip().startswith("<"):
            command = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
            if command:
                return f"<command-name>{command.group(1).strip()}</command-name><command-args>args</command-args>"
            tag = re.match(r"\s*<([\w-]+)", text)  # <local-command-stdout>, a caveat…: which one, not what it says
            return f"<{tag.group(1)}>redacted</{tag.group(1)}>" if tag else "<redacted/>"
        self.prompts += 1
        return f"prompt {self.prompts}"

    def message(self, message: dict, kind: str | None) -> dict:
        kept = {key: message[key] for key in ("id", "model", "role", "usage") if key in message}
        content = message.get("content")
        if kind == "user" and isinstance(content, str):
            kept["content"] = self.text(content)
        elif isinstance(content, list):
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if kind == "user" and block.get("type") == "text":
                    blocks.append({"type": "text", "text": self.text(block.get("text", ""))})
                elif block.get("type") == "tool_result":
                    result = {"type": "tool_result", "tool_use_id": block.get("tool_use_id"), "content": ""}
                    if block.get("is_error"):
                        result["is_error"] = True
                    blocks.append(result)
                elif block.get("type") == "tool_use":
                    blocks.append({key: block[key] for key in ("type", "id", "name") if key in block})
                else:
                    blocks.append({"type": block.get("type")})
            kept["content"] = blocks
        if isinstance(kept.get("usage"), dict):
            kept["usage"] = {k: v for k, v in kept["usage"].items() if k != "iterations"}
        return kept

    def record(self, data: dict) -> dict:
        kind = data.get("type")
        out = {key: data[key] for key in KEEP if key in data}
        if "cwd" in out:
            out["cwd"] = f"/home/user/{Path(out['cwd']).name}"
        if isinstance(data.get("message"), dict):
            out["message"] = self.message(data["message"], kind)
        result = data.get("toolUseResult")
        if isinstance(result, dict) and isinstance(result.get("searchCount"), int):
            out["toolUseResult"] = {"searchCount": result["searchCount"]}
        if data.get("quotaLimits") is not None:
            out["quotaLimits"] = {"rateLimitType": data["quotaLimits"].get("rateLimitType")}
        if kind == "last-prompt":
            out["lastPrompt"] = "last prompt"
        if kind == "system":
            metadata = data.get("compactMetadata")
            if isinstance(metadata, dict):
                out["compactMetadata"] = {k: metadata.get(k) for k in ("trigger", "preTokens", "postTokens")}
            if data.get("subtype") == "away_summary":
                out["content"] = "recap"
        return out


def convert(source: Path, target: Path) -> int:
    redactor, lines = Redactor(), []
    for line in source.read_text().splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("type") in ("attachment", "file-history-snapshot", "file-history-delta", "queue-operation"):
            continue
        lines.append(json.dumps(redactor.record(data)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
    return len(lines)


def main() -> None:
    prefix = sys.argv[1]
    matches = list(default_projects_dir().glob(f"*/{prefix}*.jsonl"))
    if len(matches) != 1:
        sys.exit(f"{prefix}: {len(matches)} transcripts match")
    source = matches[0]
    folder = FIXTURES / (sys.argv[2] if len(sys.argv) > 2 else "projects") / "-home-user-project"
    print(source.name, convert(source, folder / source.name), "lines")
    for sub in sorted((source.parent / source.stem / "subagents").glob("*.jsonl")):
        print(" ", sub.name, convert(sub, folder / source.stem / "subagents" / sub.name), "lines")


if __name__ == "__main__":
    main()
