"""Shared pytest configuration.

The held-out e1g packet tests are never collected by default (see AGENTS.md).
"""
collect_ignore_glob = ["test_e1g_*"]
