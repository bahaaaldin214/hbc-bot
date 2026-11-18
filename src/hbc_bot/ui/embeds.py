"""Job -> Discord embed."""
from __future__ import annotations

import discord

from ..jobs import Job, JobStatus


def _style_for(status: str) -> tuple[str, discord.Color]:
    """Map a job status string to (label, embed color)."""
    try:
        kind = JobStatus(status)
    except ValueError:
        return (status, discord.Color.light_grey())
    if kind is JobStatus.QUEUED:
        return ("⏳ Queued", discord.Color.light_grey())
    if kind is JobStatus.RUNNING:
        return ("🔄 Running", discord.Color.blurple())
    if kind is JobStatus.SUCCEEDED:
        return ("✅ Succeeded", discord.Color.green())
    if kind is JobStatus.FAILED:
        return ("❌ Failed", discord.Color.red())
    if kind is JobStatus.CANCELLED:
        return ("🛑 Cancelled", discord.Color.orange())
    if kind is JobStatus.TIMEOUT:
        return ("⌛ Timed out", discord.Color.orange())
    if kind is JobStatus.INTERRUPTED:
        return ("⚠️ Interrupted (bot restarted)", discord.Color.orange())
    return (status, discord.Color.light_grey())


def bar(percent: float, width: int = 20) -> str:
    """Render a text progress bar for embed descriptions."""
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled) + f" {percent:.0f}%"


def _duration(seconds: float) -> str:
    """Format a duration as h/m/s for embed fields."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"


def _code(text: str, limit: int) -> str:
    """Wrap text in a Discord code block, truncating from the start if needed."""
    # Triple-backticks inside the log would break the fence.
    text = text.replace("```", "'''")
    if len(text) > limit:
        text = "…" + text[-limit:]
    return f"```\n{text}\n```"


def job_embed(job: Job, show_tail: bool = True) -> discord.Embed:
    """Build the live/status embed for one job."""
    label, color = _style_for(job.status)
    embed = discord.Embed(title=f"Job #{job.id} · {job.tool} {job.command}", color=color)
    embed.add_field(name="Status", value=label)
    embed.add_field(name="Requested by", value=f"<@{job.user_id}>")

    if job.started_at and job.ended_at:
        embed.add_field(name="Duration", value=_duration(job.ended_at - job.started_at))
    elif job.started_at:
        # Discord relative timestamp: shows "x minutes ago" in the client.
        embed.add_field(name="Started", value=f"<t:{int(job.started_at)}:R>")
    if job.returncode is not None:
        embed.add_field(name="Exit code", value=str(job.returncode))

    lines = [f"`{job.title[:200]}`"]
    if job.status == JobStatus.SUCCEEDED.value:
        lines.append(bar(100))
    elif job.progress is not None:
        lines.append(bar(job.progress.percent))
        if job.progress.message:
            lines.append(job.progress.message[:200])
    if show_tail and job.tail:
        lines.append(_code("\n".join(job.tail), 1500))
    embed.description = "\n".join(lines)
    return embed


def job_list_embed(items: list[Job]) -> discord.Embed:
    """Build a compact list embed of recent jobs."""
    embed = discord.Embed(title="Recent jobs", color=discord.Color.blurple())
    if not items:
        embed.description = "No jobs yet."
        return embed
    rows = []
    for job in items:
        label = _style_for(job.status)[0]
        rows.append(
            f"**#{job.id}** {label} · `{job.title[:60]}` · {job.user_name} · "
            f"<t:{int(job.created_at)}:R>"
        )
    embed.description = "\n".join(rows)
    return embed


def _discord_md(text: str, limit: int = 3900) -> str:
    """Lightly adapt Markdown so Discord embed formatting actually renders.

    Embeds honor **bold**, *italic*, ``code``, fences, and lists — but ATATX
    headings (``#``) stay literal, so turn those into bold lines.
    """
    lines: list[str] = []
    for line in (text or "No help text.").replace("\r\n", "\n").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # "# Title" / "## Title" → **Title** (Discord has no heading styles).
            lines.append(f"**{stripped.lstrip('#').strip()}**")
        else:
            lines.append(line)
    out = "\n".join(lines).strip() or "No help text."
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out


def help_embed(title: str, text: str) -> discord.Embed:
    """Render HELP.md (or similar) as a Discord embed with live markdown."""
    return discord.Embed(
        title=title,
        description=_discord_md(text),
        color=discord.Color.blurple(),
    )
