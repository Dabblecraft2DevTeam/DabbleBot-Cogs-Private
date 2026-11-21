"""Interactive Discord UI Views and Modals for HCLink Account Migration Approvals."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional, Union

import discord

from .embeds import mark_embed_approved, mark_embed_rejected

if TYPE_CHECKING:
    from .hclink_approvals import HCLinkApprovals

logger = logging.getLogger("red.dabblebot.hclinkapprovals.views")

MAX_DISCORD_MSG_LEN = 1900


def chunk_plot_names(plots: List[str], max_len: int = MAX_DISCORD_MSG_LEN) -> List[str]:
    """
    Format and chunk a list of plot/ship names into code-block messages <= max_len chars.
    """
    if not plots:
        return ["*No plots or ships on record for this account.*"]

    chunks: List[str] = []
    current_lines: List[str] = []
    total_plots = len(plots)

    prefix_template = "📋 **Plots / Ships on Record ({total} total):**\n```text\n"
    suffix = "\n```"

    header_cost = len(prefix_template.format(total=total_plots)) + len(suffix)
    current_len = header_cost

    for idx, name in enumerate(plots, 1):
        line = f"{idx}. {name}\n"
        line_len = len(line)

        if current_lines and (current_len + line_len > max_len):
            content = prefix_template.format(total=total_plots) + "".join(current_lines) + suffix
            chunks.append(content)
            current_lines = [line]
            current_len = header_cost + line_len
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        content = prefix_template.format(total=total_plots) + "".join(current_lines) + suffix
        chunks.append(content)

    return chunks


async def check_staff_permission(
    interaction: discord.Interaction,
    cog: HCLinkApprovals,
) -> bool:
    """
    Validate that the interaction user holds staff role or mod/admin permissions.
    Responds ephemerally if permission is denied.
    """
    user = interaction.user
    if not interaction.guild or not isinstance(user, discord.Member):
        if not await cog.bot.is_owner(user):
            await interaction.response.send_message(
                "❌ This action can only be executed within a Discord server.",
                ephemeral=True,
            )
            return False
        return True

    # Bot owners, guild administrators, and users with Manage Guild are always permitted
    if await cog.bot.is_owner(user):
        return True

    if user.guild_permissions.administrator or user.guild_permissions.manage_guild:
        return True

    # Check configured staff role
    staff_role_id = await cog.config.staff_role_id()
    if staff_role_id:
        has_role = any(role.id == staff_role_id for role in user.roles)
        if has_role:
            return True

    await interaction.response.send_message(
        "❌ You do not have permission to review migration requests (staff role or administrator required).",
        ephemeral=True,
    )
    return False


class RejectModal(discord.ui.Modal):
    """Modal dialog prompting staff for a rejection reason."""

    def __init__(
        self,
        view: ApprovalView,
        session_token: str,
        target_message: discord.Message,
    ) -> None:
        token_short = session_token[:8] if session_token else "Session"
        super().__init__(title=f"Reject Migration ({token_short})")
        self.approval_view = view
        self.session_token = session_token
        self.target_message = target_message

        self.reason_input = discord.ui.TextInput(
            label="Rejection Reason",
            placeholder="e.g. Identity could not be verified / plot names did not match.",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason = self.reason_input.value.strip() or "Rejected by staff review."
        cog = self.approval_view.cog

        # Acknowledge immediately to avoid interaction timeout
        await interaction.response.defer(ephemeral=True)

        # Disable buttons while in flight
        self.approval_view.set_all_disabled(True)
        try:
            await self.target_message.edit(view=self.approval_view)
        except discord.HTTPException:
            pass

        try:
            await cog.client.reject_migration(
                token=self.session_token,
                discord_id=str(interaction.user.id),
                reason=reason,
            )

            # Update embed to rejected state
            embed = self.target_message.embeds[0] if self.target_message.embeds else None
            if embed:
                mark_embed_rejected(embed, interaction.user, reason=reason)
                await self.target_message.edit(embed=embed, view=self.approval_view)

            # Update mapping store
            await cog.update_session_status(self.session_token, "rejected", interaction.user.id)
            await interaction.followup.send(
                f"❌ Migration session `{self.session_token[:8]}` rejected.",
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("Failed to reject migration session %s: %s", self.session_token, exc)
            self.approval_view.set_all_disabled(False)
            try:
                await self.target_message.edit(view=self.approval_view)
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                f"❌ Failed to reject migration on backend: {exc}",
                ephemeral=True,
            )


class ApprovalView(discord.ui.View):
    """
    Interactive View with View Plots, Approve, and Reject buttons.
    Persistent view with custom_ids tied to the session token.
    """

    def __init__(
        self,
        cog: HCLinkApprovals,
        session_token: str,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.session_token = session_token

        # Configure buttons with custom_ids for persistence
        self.plots_button = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="View Plots",
            custom_id=f"hclink_plots:{session_token}",
            disabled=disabled,
            row=0,
        )
        self.plots_button.callback = self.on_view_plots
        self.add_item(self.plots_button)

        self.approve_button = discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Approve",
            custom_id=f"hclink_approve:{session_token}",
            disabled=disabled,
            row=0,
        )
        self.approve_button.callback = self.on_approve
        self.add_item(self.approve_button)

        self.reject_button = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Reject",
            custom_id=f"hclink_reject:{session_token}",
            disabled=disabled,
            row=0,
        )
        self.reject_button.callback = self.on_reject
        self.add_item(self.reject_button)

    def set_all_disabled(self, disabled: bool) -> None:
        """Enable or disable all buttons in the view."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = disabled

    async def on_view_plots(self, interaction: discord.Interaction) -> None:
        """Fetch plot/ship names and display ephemerally with pagination."""
        if not await check_staff_permission(interaction, self.cog):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            plots = await self.cog.client.get_session_plots(self.session_token)
            chunks = chunk_plot_names(plots)

            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    await interaction.followup.send(chunk, ephemeral=True)
                else:
                    await interaction.followup.send(chunk, ephemeral=True)

        except Exception as exc:
            logger.error("Failed to fetch plots for session %s: %s", self.session_token, exc)
            await interaction.followup.send(
                f"❌ Error retrieving plots from backend: {exc}",
                ephemeral=True,
            )

    async def on_approve(self, interaction: discord.Interaction) -> None:
        """Approve migration request with in-flight race protection."""
        if not await check_staff_permission(interaction, self.cog):
            return

        # Defer and disable all buttons to prevent double-click race
        self.set_all_disabled(True)
        await interaction.response.edit_message(view=self)

        try:
            result = await self.cog.client.approve_migration(
                token=self.session_token,
                discord_id=str(interaction.user.id),
            )

            # Update embed to approved state
            message = interaction.message
            if message and message.embeds:
                embed = message.embeds[0]
                mark_embed_approved(embed, interaction.user)
                await message.edit(embed=embed, view=self)

            await self.cog.update_session_status(self.session_token, "approved", interaction.user.id)
            await interaction.followup.send(
                f"✅ Migration session `{self.session_token[:8]}` approved successfully.",
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("Approval call failed for session %s: %s", self.session_token, exc)
            # Re-enable buttons on failure
            self.set_all_disabled(False)
            if interaction.message:
                try:
                    await interaction.message.edit(view=self)
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                f"❌ Failed to approve migration on backend: {exc}",
                ephemeral=True,
            )

    async def on_reject(self, interaction: discord.Interaction) -> None:
        """Open modal prompting for rejection reason."""
        if not await check_staff_permission(interaction, self.cog):
            return

        modal = RejectModal(
            view=self,
            session_token=self.session_token,
            target_message=interaction.message,
        )
        await interaction.response.send_modal(modal)
