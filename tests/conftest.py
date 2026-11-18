"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from hbc_bot.config import Config


@pytest.fixture
def make_cfg(tmp_path: Path):
    """Factory for a Config with test defaults; keyword args override fields."""

    def build(**overrides) -> Config:
        """Return a Config for guild 1 / DM user 99, data under tmp_path."""
        fields = dict(
            token="x",
            guild_id=1,
            public_channel_ids=frozenset({111}),
            allow_dm=True,
            dm_user_ids=frozenset({99}),
            host="vosslink",
            tools_dir=Path("."),
            launcher=Path("bahaa"),
            bash="bash",
            data_dir=tmp_path / "data",
            job_timeout_s=None,
            show_log_tail=True,
            embed_edit_interval_s=0.0,
            max_log_upload_bytes=8 * 1024 * 1024,
            max_upload_bytes=1024,
            confirm_timeout_s=300.0,
        )
        fields.update(overrides)
        return Config(**fields)

    return build
