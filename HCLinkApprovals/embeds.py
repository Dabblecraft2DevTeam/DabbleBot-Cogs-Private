"""Embed builder and formatting helpers for HCLink Account Migration Approvals."""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, Union

import discord


def format_balance(balance_raw: Any) -> str:
    """Format balance value. If None or empty, return 'no balance on record'."""
    if balance_raw is None:
        return "no balance on record"

    if isinstance(balance_raw, (int, float)):
        return f"${balance_raw:,.2f}"

    b_str = str(balance_raw).strip()
    if not b_str or b_str.lower() in ("none", "null", "no balance on record"):
        return "no balance on record"

    try:
        cleaned = b_str.replace("$", "").replace(",", "")
        val = float(cleaned)
        return f"${val:,.2f}"
    except (ValueError, TypeError):
        return b_str if b_str.startswith("$") else f"${b_str}"


def format_timestamp(timestamp_raw: Any) -> str:
    """Format timestamp into human-readable and Discord relative timestamp formats."""
    if not timestamp_raw:
        return "Unknown"

    try:
        ts_float = float(timestamp_raw)
        ts_int = int(ts_float)
        return f"<t:{ts_int}:f> (<t:{ts_int}:R>)"
    except (ValueError, TypeError):
        return str(timestamp_raw)


def build_migration_embed(session_data: Dict[str, Any]) -> discord.Embed:
    """
    Construct the staff review approval embed for a pending migration session.
    """
    token = session_data.get("session_token", "")
    token_short = token[:8] if token else "unknown"

    verification = session_data.get("verification") or {}
    if not isinstance(verification, dict):
        verification = {}

    old_name = verification.get("old_name") or session_data.get("old_name") or "Unknown"
    old_uuid = verification.get("old_uuid") or session_data.get("old_uuid") or "Unknown"

    new_name = verification.get("new_name") or session_data.get("new_name") or "Unknown"
    new_uuid = verification.get("new_uuid") or session_data.get("new_uuid") or "Unknown"

    created_at = (
        verification.get("created_at")
        or session_data.get("created_at")
        or verification.get("created_time")
    )
    requested_str = format_timestamp(created_at)

    plot_count = verification.get("plot_count", 0)
    last_played = verification.get("last_played") or "Unknown / Never"
    balance_display = format_balance(verification.get("balance"))

    notes = session_data.get("notes")

    embed = discord.Embed(
        title="Account Migration Request",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    embed.description = (
        "**Advisory Verification (Conversational):**\n"
        "Ask the applicant these questions to judge account ownership:\n"
        "• *Roughly how much money did you have?* (Compare against Balance below)\n"
        "• *Roughly how many plots or ships did you own?* (Compare against Plot Count)\n"
        "• *Can you name one of your ships or plots?* (Click **View Plots** to compare)\n"
        "• *Approximately when did you last play on the server?* (Compare against Last Played)\n\n"
        "> ⚠️ **Staff Note**: If this is a staff account, re-apply LuckPerms staff group manually after approval."
    )

    embed.add_field(
        name="Old Account",
        value=f"**Name**: `{old_name}`\n**UUID**: `{old_uuid}`",
        inline=True,
    )
    embed.add_field(
        name="New Account",
        value=f"**Name**: `{new_name}`\n**UUID**: `{new_uuid}`",
        inline=True,
    )
    embed.add_field(
        name="Requested",
        value=requested_str,
        inline=False,
    )
    embed.add_field(
        name="Plot / Ship Count",
        value=str(plot_count),
        inline=True,
    )
    embed.add_field(
        name="Last Played",
        value=str(last_played),
        inline=True,
    )
    embed.add_field(
        name="Balance",
        value=balance_display,
        inline=True,
    )

    if notes:
        embed.add_field(
            name="Notes",
            value=str(notes)[:1024],
            inline=False,
        )

    embed.set_footer(text=f"Request Token: {token_short} | HCLink Migration")
    return embed


def mark_embed_approved(
    embed: discord.Embed,
    approver: Union[discord.User, discord.Member],
) -> discord.Embed:
    """Update embed to indicate approval."""
    embed.title = "✅ Account Migration Request — APPROVED"
    embed.color = discord.Color.green()
    embed.add_field(
        name="Decision",
        value=f"Approved by {approver.mention} ({approver.display_name})",
        inline=False,
    )
    return embed


def mark_embed_rejected(
    embed: discord.Embed,
    rejecter: Union[discord.User, discord.Member],
    reason: Optional[str] = None,
) -> discord.Embed:
    """Update embed to indicate rejection."""
    embed.title = "❌ Account Migration Request — REJECTED"
    embed.color = discord.Color.red()
    val = f"Rejected by {rejecter.mention} ({rejecter.display_name})"
    if reason:
        val += f"\n**Reason**: {reason}"
    embed.add_field(
        name="Decision",
        value=val,
        inline=False,
    )
    return embed


def mark_embed_resolved(
    embed: discord.Embed,
    reason: str = "Resolved elsewhere or expired",
) -> discord.Embed:
    """Update embed to indicate session is resolved or expired."""
    embed.title = "⚪ Account Migration Request — RESOLVED"
    embed.color = discord.Color.light_grey()
    embed.add_field(
        name="Status",
        value=f"**{reason}**\nThis session is no longer pending staff review.",
        inline=False,
    )
    # Update footer
    footer_text = embed.footer.text or ""
    if "Status: Resolved" not in footer_text:
        embed.set_footer(text=f"{footer_text} | Status: Resolved")
    return embed
