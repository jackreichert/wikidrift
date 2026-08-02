"""Pure multi-revision attribution for exact events bounded by stable states.

The ledger describes observable token operations by public account token. It does not infer
identity, coordination, motive, ownership, or misconduct.
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict


EVENT_ATTRIBUTION_SCHEMA_VERSION = 3
EVENT_ATTRIBUTION_POLICY_VERSION = "multi-revision-attribution-v1"
HIDDEN_ACCOUNT = "<hidden>"


def attribute_revision_sequence(article, revisions):
    """Attribute gross operations and net-standing content across an ordered revision sequence."""
    _validate_revisions(revisions)
    states = [_token_state(revision) for revision in revisions]
    initial_ids = set(states[0])
    final_ids = set(states[-1])
    seen_ids = set(initial_ids)
    rows = [_boundary_row(revisions[0])]
    removal_operations = defaultdict(list)
    addition_operations = defaultdict(list)

    for index in range(1, len(revisions)):
        revision = revisions[index]
        previous_ids = set(states[index - 1])
        current_ids = set(states[index])
        removed_ids = previous_ids - current_ids
        added_ids = current_ids - previous_ids
        restored_ids = added_ids & seen_ids
        for token_id in removed_ids:
            removal_operations[token_id].append(index)
        for token_id in added_ids:
            addition_operations[token_id].append(index)
        role, restores_revision_id = _revision_role(index, revisions, states, restored_ids)
        rows.append({
            "revision_id": revision["revision_id"],
            "timestamp": revision["timestamp"],
            "account": revision.get("account", HIDDEN_ACCOUNT),
            "account_type": _account_type(revision),
            "role": role,
            "restores_revision_id": restores_revision_id,
            "parent_id": revision.get("parent_id"),
            "sha1": revision.get("sha1"),
            "edit_summary": revision.get("comment"),
            "tags": sorted(revision.get("tags") or []),
            "size": revision.get("size"),
            "size_delta": _size_delta(revisions[index - 1], revision),
            "gross_removed_tokens": len(removed_ids),
            "gross_added_tokens": len(added_ids),
            "restored_tokens": len(restored_ids),
            "standing_removed_tokens": 0,
            "standing_added_tokens": 0,
        })
        seen_ids.update(current_ids)

    standing_removed = initial_ids - final_ids
    standing_replacements = final_ids - initial_ids
    removal_counts = defaultdict(int)
    replacement_counts = defaultdict(int)

    for token_id in standing_removed:
        operation_index = removal_operations[token_id][-1]
        rows[operation_index]["standing_removed_tokens"] += 1
        removal_counts[rows[operation_index]["account"]] += 1

    revision_index = {
        revision["revision_id"]: index for index, revision in enumerate(revisions)
    }
    for token_id in standing_replacements:
        origin_revision = states[-1][token_id]
        operation_index = revision_index.get(origin_revision)
        if operation_index is None or operation_index == 0:
            operations = addition_operations.get(token_id) or []
            if not operations:
                raise ValueError(
                    f"standing replacement token {token_id} has no in-sequence addition operation"
                )
            operation_index = operations[-1]
        rows[operation_index]["standing_added_tokens"] += 1
        replacement_counts[rows[operation_index]["account"]] += 1

    removal_rows = _account_rows(removal_counts)
    replacement_rows = _account_rows(replacement_counts)
    gross_removed = sum(row["gross_removed_tokens"] for row in rows)
    gross_added = sum(row["gross_added_tokens"] for row in rows)
    restored = sum(row["restored_tokens"] for row in rows)
    removed_tokens = len(standing_removed)
    replacement_tokens = len(standing_replacements)
    participation = _participation(removal_rows, replacement_rows, removed_tokens, replacement_tokens)
    event_status = (
        "reverted" if not removed_tokens and not replacement_tokens and (gross_removed or gross_added)
        else "standing"
    )

    return {
        "schema_version": EVENT_ATTRIBUTION_SCHEMA_VERSION,
        "policy_version": EVENT_ATTRIBUTION_POLICY_VERSION,
        "article": article,
        "before_revid": revisions[0]["revision_id"],
        "before_timestamp": revisions[0]["timestamp"],
        "after_revid": revisions[-1]["revision_id"],
        "after_timestamp": revisions[-1]["timestamp"],
        "event_status": event_status,
        "semantic_role": "event_participation_receipt",
        "revisions": rows,
        "gross": {
            "removed_tokens": gross_removed,
            "added_tokens": gross_added,
            "restored_tokens": restored,
        },
        "net_standing": {
            "removed_tokens": removed_tokens,
            "replacement_tokens": replacement_tokens,
        },
        "removals_by_editor": removal_rows,
        "replacement_by_editor": replacement_rows,
        "participation": participation,
        "removed_tokens": removed_tokens,
        "replacement_tokens": replacement_tokens,
        **participation,
        "note": (
            "Counts describe observable public revision operations and surviving token states; "
            "they do not establish identity, coordination, motive, ownership, or misconduct."
        ),
    }


def _validate_revisions(revisions):
    if len(revisions) < 2:
        raise ValueError("multi-revision attribution requires at least two revisions")
    revision_ids = [int(revision["revision_id"]) for revision in revisions]
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("revision IDs must be unique")
    timestamps = [revision["timestamp"] for revision in revisions]
    if timestamps != sorted(timestamps):
        raise ValueError("revisions must be ordered by timestamp")


def _token_state(revision):
    state = {}
    for token in revision.get("tokens", []):
        token_id = token["token_id"]
        if token_id in state:
            raise ValueError(
                f"revision {revision['revision_id']} contains duplicate token ID {token_id}"
            )
        state[token_id] = token.get("o_rev_id")
    return state


def _boundary_row(revision):
    return {
        "revision_id": revision["revision_id"],
        "timestamp": revision["timestamp"],
        "account": revision.get("account", HIDDEN_ACCOUNT),
        "account_type": _account_type(revision),
        "role": "stable_before",
        "restores_revision_id": None,
        "parent_id": revision.get("parent_id"),
        "sha1": revision.get("sha1"),
        "edit_summary": revision.get("comment"),
        "tags": sorted(revision.get("tags") or []),
        "size": revision.get("size"),
        "size_delta": None,
        "gross_removed_tokens": 0,
        "gross_added_tokens": 0,
        "restored_tokens": 0,
        "standing_removed_tokens": 0,
        "standing_added_tokens": 0,
    }


def _revision_role(index, revisions, states, restored_ids):
    current_ids = set(states[index])
    for previous_index in range(index - 1, -1, -1):
        if current_ids == set(states[previous_index]):
            return "revert", revisions[previous_index]["revision_id"]
    if index == len(states) - 1:
        return "consolidation", None
    if restored_ids:
        for previous_index in range(index - 1, -1, -1):
            if restored_ids <= set(states[previous_index]):
                return "restoration", revisions[previous_index]["revision_id"]
        return "restoration", None
    if index == 1:
        return "initiating_change", None
    return "intermediate_change", None


def _size_delta(previous, current):
    previous_size = previous.get("size")
    current_size = current.get("size")
    if previous_size is None or current_size is None:
        return None
    return current_size - previous_size


def _account_type(revision):
    explicit = revision.get("account_type")
    if explicit:
        return explicit
    account = revision.get("account", HIDDEN_ACCOUNT)
    if account == HIDDEN_ACCOUNT:
        return "hidden"
    try:
        ipaddress.ip_address(account)
        return "anonymous_ip"
    except ValueError:
        pass
    if account.casefold().endswith("bot"):
        return "bot"
    return "registered"


def _account_rows(counts):
    return [
        {"editor": account, "tokens": tokens}
        for account, tokens in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _participation(removal_rows, replacement_rows, removed_tokens, replacement_tokens):
    top_removal = removal_rows[0] if removal_rows else None
    top_replacement = replacement_rows[0] if replacement_rows else None
    return {
        "top_removal_share": (
            round(top_removal["tokens"] / removed_tokens, 6)
            if top_removal and removed_tokens else None
        ),
        "top_replacement_share": (
            round(top_replacement["tokens"] / replacement_tokens, 6)
            if top_replacement and replacement_tokens else None
        ),
        "top_two_removal_share": (
            round(sum(row["tokens"] for row in removal_rows[:2]) / removed_tokens, 6)
            if removed_tokens else None
        ),
        "same_top_editor": bool(
            top_removal and top_replacement and top_removal["editor"] == top_replacement["editor"]
        ),
    }
