"""Unit tests for application config (Settings).

Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

import pytest

from src.config import Settings


class TestBranchPrefix:
    """branch_prefix default and trailing-slash normalization."""

    def test_default_branch_prefix_is_feature_ai(self) -> None:
        # Arrange & Act — minimal required fields
        s = Settings(
            azure_devops_org_url="https://dev.azure.com/org",
            azure_devops_project="p",
            azure_devops_repository="r",
            devops_auth_mode="system_token",
            system_access_token="token",
        )
        # Assert
        assert s.branch_prefix == "feature_ai"

    def test_trailing_slash_stripped(self) -> None:
        # Arrange & Act
        s = Settings(
            azure_devops_org_url="https://dev.azure.com/org",
            azure_devops_project="p",
            azure_devops_repository="r",
            devops_auth_mode="system_token",
            system_access_token="token",
            branch_prefix="feature_ai/",
        )
        # Assert
        assert s.branch_prefix == "feature_ai"

    def test_no_trailing_slash_unchanged(self) -> None:
        # Arrange & Act
        s = Settings(
            azure_devops_org_url="https://dev.azure.com/org",
            azure_devops_project="p",
            azure_devops_repository="r",
            devops_auth_mode="system_token",
            system_access_token="token",
            branch_prefix="agent",
        )
        # Assert
        assert s.branch_prefix == "agent"
