"""`/pipeline` Discord flow against fake interactions (no gateway, no subprocess)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from hbc_bot.commands import pipeline
from hbc_bot.jobs import Job, JobStatus
from hbc_bot.policy import Policy
from hbc_bot.registry import Registry
from hbc_bot.ui.forms import ArgsModal, upload_name
from hbc_bot.ui.views import PromptView


class FakeJobs:
    """Records submissions instead of running them."""

    def __init__(self):
        """Start with no submitted jobs."""
        self.submitted: list[tuple] = []

    def submit(self, tool, command, args, argv, user_id, user_name) -> Job:
        """Return an already-finished job so the follow task exits at once."""
        self.submitted.append((tool, command, args, argv))
        job = Job(len(self.submitted), tool, command, args, user_id, user_name)
        job.status = JobStatus.SUCCEEDED.value
        job.changed.set()
        return job


def _bot(make_cfg, **cfg_overrides) -> SimpleNamespace:
    """Bot stand-in with the pilot tools tree scanned."""
    tools_dir = Path(__file__).resolve().parent / "fixtures" / "tools"
    cfg = make_cfg(tools_dir=tools_dir, launcher=tools_dir / "bahaa", **cfg_overrides)
    registry = Registry(tools_dir)
    registry.scan()
    return SimpleNamespace(cfg=cfg, registry=registry, policy=Policy(cfg), jobs=FakeJobs())


def _interaction(*, guild_id: int | None = 1, user_id: int = 7, message=None) -> MagicMock:
    """Interaction double: sync ``is_done`` tracking plus AsyncMock response methods."""
    interaction = MagicMock()
    interaction.id = 555
    interaction.guild_id = guild_id
    interaction.channel_id = 222
    interaction.message = message
    interaction.user.id = user_id
    interaction.user.display_name = "tester"
    done = {"value": False}

    def mark_done(*args, **kwargs) -> None:
        """Any response call consumes the interaction."""
        done["value"] = True

    response = interaction.response
    response.is_done = MagicMock(side_effect=lambda: done["value"])
    for name in ("send_message", "send_modal", "edit_message", "defer"):
        setattr(response, name, AsyncMock(side_effect=mark_done))
    interaction.edit_original_response = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock(id=900))
    interaction.followup.send = AsyncMock(return_value=MagicMock(id=901))
    interaction.channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return interaction


def _sent_text(mock: AsyncMock) -> str:
    """Text of the last message sent/edited through ``mock``."""
    call = mock.await_args
    return call.kwargs.get("content") or (call.args[0] if call.args else "")


async def _settle() -> None:
    """Let follow tasks run to completion."""
    await asyncio.sleep(0.05)


def _run(coro) -> None:
    """Run one async scenario (views need a running loop)."""
    asyncio.run(coro)


def test_dry_run_starts_without_confirm(make_cfg) -> None:
    """Slash → native form → dry run starts at once with validator-built argv."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        slash = _interaction()
        await pipeline.main(bot, slash, "debrief")
        modal = slash.response.send_modal.await_args.args[0]
        assert isinstance(modal, ArgsModal)
        labels = [(item.text, type(item.component).__name__) for item in modal.children]
        assert labels == [
            ("Mode", "Select"), ("Study", "Select"), ("Subject IDs", "TextInput"),
            ("Max subjects", "TextInput"),
        ]
        mode = modal.children[0].component
        assert [(o.value, o.default) for o in mode.options] == [("dry", True), ("apply", False)]

        submit = _interaction()
        await modal._on_values(submit, {"mode": "dry", "study": "int", "ids": "8080, 9031"})
        await _settle()
        tool, command, args, argv = bot.jobs.submitted[0]
        assert (tool, command) == ("pipeline", "debrief")
        assert args == "--study int --ids 8080,9031 --auto-fitness"
        assert argv[:2] == ["bash", str(bot.cfg.launcher)]
        assert argv[2:] == ["pipeline", "debrief", "--study", "int", "--ids", "8080,9031", "--auto-fitness"]
        # Public live embed is the direct response to the form.
        assert "embed" in submit.response.send_message.await_args.kwargs
        assert submit.response.send_message.await_args.kwargs.get("ephemeral") is None

    _run(go())


def test_apply_needs_confirm_button(make_cfg) -> None:
    """mode=apply shows a private danger button; the job starts only on click."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        slash = _interaction()
        await pipeline.main(bot, slash, "debrief")
        modal = slash.response.send_modal.await_args.args[0]

        submit = _interaction()
        await modal._on_values(submit, {"mode": "apply", "study": "int"})
        assert bot.jobs.submitted == []
        prompt = submit.response.send_message.await_args
        assert prompt.kwargs["ephemeral"] is True
        assert "--apply --land --study int" in prompt.args[0]
        view = prompt.kwargs["view"]
        assert isinstance(view, PromptView)
        buttons = [(b.label, b.style) for b in view.children]
        assert buttons == [
            ("Apply + land PDFs", discord.ButtonStyle.danger),
            ("Edit", discord.ButtonStyle.secondary),
            ("Cancel", discord.ButtonStyle.secondary),
        ]

        # Someone else cannot press it.
        stranger = _interaction(user_id=8, message=MagicMock())
        assert await view.interaction_check(stranger) is False
        assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True

        click = _interaction(message=MagicMock())
        assert await view.interaction_check(click) is True
        await view.children[0].callback(click)
        await _settle()
        assert bot.jobs.submitted[0][2] == "--apply --land --study int --auto-fitness"
        assert "Started job #1" in _sent_text(click.response.edit_message)
        assert click.response.edit_message.await_args.kwargs["view"] is None
        assert "embed" in click.followup.send.await_args.kwargs

    _run(go())


def test_cancel_and_edit_buttons(make_cfg) -> None:
    """Cancel starts nothing; Edit reopens the form prefilled and updates the prompt in place."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        slash = _interaction()
        await pipeline.main(bot, slash, "debrief")
        modal = slash.response.send_modal.await_args.args[0]
        submit = _interaction()
        await modal._on_values(submit, {"mode": "apply", "limit": "3"})
        view = submit.response.send_message.await_args.kwargs["view"]

        edit_click = _interaction(message=MagicMock())
        await view.children[1].callback(edit_click)
        reopened = edit_click.response.send_modal.await_args.args[0]
        assert reopened.children[3].component.default == "3"
        assert [o.default for o in reopened.children[0].component.options] == [False, True]

        resubmit = _interaction(message=MagicMock())
        await reopened._on_values(resubmit, {"mode": "apply", "limit": "5"})
        assert "--limit 5" in _sent_text(resubmit.response.edit_message)
        view2 = resubmit.response.edit_message.await_args.kwargs["view"]
        assert view.is_finished() and not view2.is_finished()

        cancel_click = _interaction(message=MagicMock())
        await view2.children[2].callback(cancel_click)
        assert "Nothing was started" in _sent_text(cancel_click.response.edit_message)
        assert bot.jobs.submitted == []

    _run(go())


def test_invalid_form_values_are_private_errors(make_cfg) -> None:
    """Validator problems come back ephemeral with an Edit button; nothing runs."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        slash = _interaction()
        await pipeline.main(bot, slash, "debrief")
        modal = slash.response.send_modal.await_args.args[0]
        submit = _interaction()
        await modal._on_values(submit, {"mode": "dry", "ids": "80; rm -rf /", "limit": "many"})
        call = submit.response.send_message.await_args
        assert call.kwargs["ephemeral"] is True
        assert "ids:" in call.args[0] and "limit:" in call.args[0]
        assert [b.label for b in call.kwargs["view"].children] == ["Edit", "Cancel"]
        assert bot.jobs.submitted == []

    _run(go())


def test_gates_refuse_privately(make_cfg, tmp_path: Path) -> None:
    """Wrong guild, unlisted DM user, hidden tool, missing schema, wrong host → ephemeral refusal."""

    async def refusal(bot, interaction, command="debrief") -> str:
        """Run /pipeline and return the (ephemeral) refusal text."""
        await pipeline.main(bot, interaction, command)
        call = interaction.response.send_message.await_args
        assert call.kwargs["ephemeral"] is True
        interaction.response.send_modal.assert_not_awaited()
        return call.args[0]

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        assert "home server" in await refusal(bot, _interaction(guild_id=2))
        assert "allowlist" in await refusal(bot, _interaction(guild_id=None, user_id=7))
        assert "Unknown command" in await refusal(bot, _interaction(), command="nope")
        assert "no `args.toml`" in await refusal(bot, _interaction(), command="ggir")
        assert "runs on `vosslink`" in await refusal(_bot(make_cfg, host="argon"), _interaction())
        assert "home server" in await refusal(_bot(make_cfg, guild_id=None), _interaction())
        assert bot.jobs.submitted == []

        # Allowlisted DM user gets the form (pilot config.toml has dm = true).
        dm = _interaction(guild_id=None, user_id=99)
        await pipeline.main(bot, dm, "debrief")
        dm.response.send_modal.assert_awaited_once()

    _run(go())


def test_autocomplete_only_offers_runnable(make_cfg) -> None:
    """Autocomplete lists schema'd pipeline commands only; nothing for strangers."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        bot = _bot(make_cfg)
        inside = _interaction()
        assert [c.value for c in await pipeline.autocomplete_command(bot, inside, "")] == [
            "debrief"
        ]
        outside = _interaction(guild_id=2)
        assert await pipeline.autocomplete_command(bot, outside, "") == []

    _run(go())


def test_job_cancel_button_policy(make_cfg) -> None:
    """Cancel button: requester, or a lab-server member with Manage Server."""

    async def go() -> None:
        """Scenario body (needs a running event loop for discord views)."""
        policy = Policy(make_cfg())
        job = Job(1, "pipeline", "debrief", "", user_id=7, user_name="t")
        assert await policy.can_cancel(_interaction(user_id=7), job)
        manager = _interaction(user_id=8)
        manager.permissions.manage_guild = True
        assert await policy.can_cancel(manager, job)
        member = _interaction(user_id=9)
        member.permissions.manage_guild = False
        member.client.is_owner = AsyncMock(return_value=False)
        assert not await policy.can_cancel(member, job)
        elsewhere = _interaction(user_id=8, guild_id=2)
        elsewhere.permissions.manage_guild = True
        assert not await policy.can_cancel(elsewhere, job)

    _run(go())


def test_upload_name_is_one_safe_segment() -> None:
    """Uploaded file names cannot escape the staging dir or look like flags."""
    assert upload_name("../../etc/passwd") == "passwd"
    assert upload_name("C:\\tmp\\my file (1).json") == "my_file__1_.json"
    assert upload_name("--force") == "force"
    assert upload_name("...") == "upload"
