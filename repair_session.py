#!/usr/bin/env python3
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Remove assistant thinking blocks from a Claude CLI session JSONL file.

This helper is intended for resume errors involving stored thinking-block
signatures. Remove thinking and redacted_thinking blocks while retaining other
message content. Drop assistant records with no remaining blocks, and resolve
parentUuid references through previously dropped records.

Usage:
    python3 repair_session.py <in.jsonl> <out.jsonl>
Prints counts of removed blocks and records.
"""
import json
import argparse
import os

THINK = {"thinking", "redacted_thinking"}


def repair(in_path: str, out_path: str) -> dict:
    """Write a copy of a session file without thinking blocks and relink the message chain."""
    with open(in_path) as source:
        lines = [line for line in source if line.strip()]
    out = []
    remap = {}  # Map each dropped record's uuid to its resolved parentUuid.
    dropped = stripped = 0

    def resolve(parent):
        # Follow previously dropped ancestors, stopping if a cycle is found.
        """Return the nearest kept ancestor of a dropped message."""
        seen = set()
        while parent in remap and parent not in seen:
            seen.add(parent)
            parent = remap[parent]
        return parent

    for raw in lines:
        ev = json.loads(raw)
        uuid = ev.get("uuid")
        parent = resolve(ev.get("parentUuid"))
        if "parentUuid" in ev:
            ev["parentUuid"] = parent

        msg = ev.get("message")
        is_asst = isinstance(msg, dict) and msg.get("role") == "assistant"
        content = msg.get("content") if isinstance(msg, dict) else None
        if is_asst and isinstance(content, list):
            kept = [
                b
                for b in content
                if not (isinstance(b, dict) and b.get("type") in THINK)
            ]
            n_removed = len(content) - len(kept)
            if n_removed:
                stripped += n_removed
            if not kept:
                # Drop an assistant record with no remaining blocks; later
                # children resolve its uuid to this record's resolved parent.
                if uuid is not None:
                    remap[uuid] = parent
                dropped += 1
                continue
            msg["content"] = kept
        out.append(json.dumps(ev, ensure_ascii=False))

    descriptor = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as f:
        f.write("\n".join(out) + "\n")
    return {
        "in_lines": len(lines),
        "out_lines": len(out),
        "blocks_stripped": stripped,
        "nodes_dropped": dropped,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="session JSONL file to repair")
    parser.add_argument("output", help="destination JSONL file")
    args = parser.parse_args()
    s = repair(args.input, args.output)
    print(
        f"[repair] {s['in_lines']} -> {s['out_lines']} lines; "
        f"{s['blocks_stripped']} thinking block(s) stripped, "
        f"{s['nodes_dropped']} thinking-only node(s) dropped"
    )
