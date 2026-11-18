"""Slash launch flow (private helper): form → validate → confirm → job.

Discord collects, the shared validator decides:

  slash → ArgsModal (native form built from args.toml) → toolargs.validate
        → confirm button when the schema asks for one → JobManager.submit → live embed
"""
from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
from functools import partial
from pathlib import Path

import discord

from .. import toolargs
from ..jobs import Job
from ..registry import CommandSpec, ToolSpec
from ..ui import embeds
from ..ui.forms import ArgsModal, upload_name
from ..ui.views import JobView, PromptView

log = logging.getLogger(__name__)


class Launch:
    """One slash request for a tool command, from form to submitted job."""

    def __init__(self, bot, tool: ToolSpec, command: CommandSpec):
        """Bind the request to an already-gated tool command (schema must exist)."""
        self.bot, self.tool, self.command = bot, tool, command
        self.schema: toolargs.CommandSchema = command.schema
        self.prompt: PromptView | None = None
        self.stage_dir: Path | None = None

    @property
    def name(self) -> str:
        """Command line prefix shown to the user."""
        return f"bahaa {self.tool.name} {self.command.name}"

    async def open(self, interaction: discord.Interaction) -> None:
        """Entry from the slash command: show the form, or continue if there is nothing to ask."""
        if self.schema.discord_fields():
            await interaction.response.send_modal(self._modal({}))
        else:
            await self.receive(interaction, {})

    def _modal(self, values: dict) -> ArgsModal:
        """Form for the Discord-visible args, prefilled with ``values``."""
        return ArgsModal(self.name, self.schema.discord_fields(), values, self.receive)

    async def receive(self, interaction: discord.Interaction, raw: dict) -> None:
        """Raw form values in: stage uploads, validate, then confirm or start."""
        typed = {name: value for name, value in raw.items() if isinstance(value, str)}
        try:
            staged = await self._stage_uploads(interaction, raw)
            values = toolargs.validate(self.schema, staged)
        except toolargs.ArgsError as exc:
            lines = "\n".join(f"- {problem}" for problem in exc.problems)
            await self._show(interaction, f"Cannot start `{self.name}`:\n{lines}", self._view(interaction, typed))
            return
        argv = toolargs.to_argv(self.schema, values)
        confirm = toolargs.confirm_for(self.schema, values)
        if confirm is None:
            await self._start(interaction, argv)
            return
        text = f"Ready to run `{self.name} {shlex.join(argv)}`".rstrip()
        if confirm.help:
            text += f"\n{confirm.help}"
        await self._show(interaction, text, self._view(interaction, typed, confirm, argv))

    def _view(
        self,
        interaction: discord.Interaction,
        typed: dict,
        confirm: toolargs.ArgSpec | None = None,
        argv: list[str] | None = None,
    ) -> PromptView:
        """Prompt buttons: the confirm action (if any), Edit (if there is a form), Cancel."""
        view = PromptView(interaction.user.id, self.bot.cfg.confirm_timeout_s)
        if confirm is not None:
            style = discord.ButtonStyle.danger if confirm.danger else discord.ButtonStyle.success
            view.add_action(confirm.label or "Start", style, partial(self._start, argv=argv))
        if self.schema.discord_fields():
            # Not final: dismissing the reopened form must leave the prompt usable.
            view.add_action(
                "Edit", discord.ButtonStyle.secondary, partial(self._edit, typed=typed), final=False
            )
        view.add_action("Cancel", discord.ButtonStyle.secondary, self._cancel)
        return view

    async def _edit(self, interaction: discord.Interaction, typed: dict) -> None:
        """Reopen the form with what the user already entered."""
        await interaction.response.send_modal(self._modal(typed))

    async def _cancel(self, interaction: discord.Interaction) -> None:
        """Drop the request without starting anything."""
        self._discard_uploads()
        await self._show(interaction, "Cancelled. Nothing was started.", None)

    async def _show(
        self, interaction: discord.Interaction, content: str, view: PromptView | None
    ) -> None:
        """Show or refresh the private prompt, whichever way this interaction can respond."""
        if self.prompt is not None:
            self.prompt.stop()
        self.prompt = view
        if view is not None:
            view.origin = interaction
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=view)
        elif interaction.message is not None:
            # A button press, or a form opened from a button: update the prompt in place.
            await interaction.response.edit_message(content=content, view=view)
        elif view is None:
            await interaction.response.send_message(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, view=view, ephemeral=True)

    async def _stage_uploads(self, interaction: discord.Interaction, raw: dict) -> dict:
        """Save ``file`` uploads under the staging dir; return raw with paths in their place."""
        uploads = {n: v for n, v in raw.items() if isinstance(v, discord.Attachment)}
        if not uploads:
            return raw
        cfg = self.bot.cfg
        # A download can outlast Discord's 3 s response window.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=interaction.message is None)
        self._discard_uploads()
        self.stage_dir = (cfg.upload_dir / str(interaction.id)).resolve()
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        staged = dict(raw)
        problems = []
        for name, attachment in uploads.items():
            if attachment.size > cfg.max_upload_bytes:
                problems.append(f"{name}: upload is larger than {cfg.max_upload_bytes} bytes")
                continue
            target = self.stage_dir / upload_name(attachment.filename)
            await attachment.save(target)
            staged[name] = str(target)
        if problems:
            raise toolargs.ArgsError(problems)
        return staged

    def _discard_uploads(self) -> None:
        """Remove this request's staged uploads, if any."""
        if self.stage_dir is not None:
            shutil.rmtree(self.stage_dir, ignore_errors=True)
            self.stage_dir = None

    async def _start(self, interaction: discord.Interaction, argv: list[str]) -> None:
        """Submit the job and post its public live embed."""
        cfg = self.bot.cfg
        # argv list only: validated tokens, never a shell string.
        full = [cfg.bash, str(cfg.launcher), self.tool.name, self.command.name, *argv]
        user = interaction.user
        job = self.bot.jobs.submit(
            self.tool.name, self.command.name, shlex.join(argv), full, user.id, user.display_name
        )
        log.info("job %s by %s (%s): %s", job.id, user, user.id, job.title)
        if interaction.response.is_done() or interaction.message is not None:
            await self._show(interaction, f"Started job #{job.id}: `{job.title}`", None)
        await post_job(self.bot, interaction, job, self.stage_dir)


async def post_job(
    bot, interaction: discord.Interaction, job: Job, cleanup: Path | None = None
) -> None:
    """Post the public job embed, upgrade to a durable Message, and follow progress."""
    view = JobView(job, bot.jobs, bot.policy)
    embed = embeds.job_embed(job, bot.cfg.show_log_tail)
    if interaction.response.is_done():
        sent = await interaction.followup.send(embed=embed, view=view, wait=True)
    else:
        await interaction.response.send_message(embed=embed, view=view)
        sent = await interaction.original_response()
    message = await _durable_message(interaction, sent, job.id)
    asyncio.create_task(_follow(bot, job, message, view, cleanup), name=f"follow-{job.id}")


async def _durable_message(
    interaction: discord.Interaction, sent: discord.Message, job_id: int
) -> discord.Message:
    """Prefer a channel Message over the interaction/webhook one (15-min token)."""
    try:
        return await interaction.channel.fetch_message(sent.id)
    except (discord.HTTPException, AttributeError):
        log.warning(
            "job %s: cannot fetch message; embed stops updating after 15 minutes",
            job_id,
        )
        return sent


async def _follow(
    bot, job: Job, message: discord.Message, view: JobView, cleanup: Path | None
) -> None:
    """Edit the job embed on each change until the job finishes; then drop staged uploads."""
    show_tail = bot.cfg.show_log_tail
    interval = bot.cfg.embed_edit_interval_s
    while True:
        await job.changed.wait()
        job.changed.clear()
        done = job.finished
        try:
            await _edit_job_message(bot, message, job, view, show_tail, done)
        except discord.HTTPException as exc:
            log.warning("job %s: embed update failed: %s", job.id, exc)
        if done:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
            return
        # Pace edits under chatty tools to reduce Discord rate limits.
        await asyncio.sleep(interval)


async def _edit_job_message(
    bot,
    message: discord.Message,
    job: Job,
    view: JobView,
    show_tail: bool,
    done: bool,
) -> None:
    """Apply one embed update (live view or final log attachment)."""
    embed = embeds.job_embed(job, show_tail)
    if done:
        view.stop()
        await message.edit(embed=embed, view=None, attachments=_log_attachments(bot, job))
        return
    await message.edit(embed=embed, view=view)


def _log_attachments(bot, job: Job) -> list[discord.File]:
    """Attach the job log when finished, if size and settings allow."""
    if not bot.cfg.show_log_tail or not job.log_path:
        return []
    path = Path(job.log_path)
    if not path.is_file():
        return []
    size = path.stat().st_size
    if size == 0 or size > bot.cfg.max_log_upload_bytes:
        return []
    return [discord.File(path, filename=f"job-{job.id}.log")]
