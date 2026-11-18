"""Button views: Cancel on a running job, and the private confirm/edit prompt."""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from ..jobs import Job, JobManager
from ..policy import Policy


class JobView(discord.ui.View):
    """Cancel button bound to one live job embed."""

    def __init__(self, job: Job, manager: JobManager, policy: Policy):
        """Create a persistent view (timeout=None) for the job's Cancel button."""
        super().__init__(timeout=None)
        self.job, self.manager, self.policy = job, manager, policy

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Handle Cancel clicks; only requester/operators may cancel."""
        if not await self.policy.can_cancel(interaction, self.job):
            await interaction.response.send_message(
                "Only the requester or a server manager can cancel this job.", ephemeral=True
            )
            return
        ok = self.manager.cancel(self.job.id)
        msg = f"Cancelling job #{self.job.id}…" if ok else "That job already finished."
        await interaction.response.send_message(msg, ephemeral=True)


class PromptView(discord.ui.View):
    """Private prompt with action buttons (confirm / edit / cancel) for one requester."""

    def __init__(self, owner_id: int, timeout: float):
        """Create an empty prompt; add buttons with ``add_action``."""
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        # Interaction whose response displays this prompt; lets on_timeout clear it.
        self.origin: discord.Interaction | None = None

    def add_action(
        self,
        label: str,
        style: discord.ButtonStyle,
        handler: Callable[[discord.Interaction], Awaitable[None]],
        *,
        final: bool = True,
    ) -> None:
        """Add a button that awaits ``handler``. ``final`` buttons end the prompt first."""
        button = discord.ui.Button(label=label[:80], style=style)

        async def callback(interaction: discord.Interaction) -> None:
            """Run the action for a click that passed ``interaction_check``."""
            if final:
                self.stop()
            await handler(interaction)

        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the requester may press the buttons (command permissions do not cover them)."""
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who ran the command can use these buttons.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        """Replace the stale prompt so dead buttons are not left behind."""
        if self.origin is None:
            return
        try:
            await self.origin.edit_original_response(
                content="Timed out. Nothing was started.", view=None
            )
        except discord.HTTPException:
            pass
