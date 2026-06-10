"""Composite SOAR operations built on vrcli.api (PLAN.md §4.2).

Every ops command writes an audit JSONL record and, when it produces
evidence, a SHA-256 evidence manifest — chain-of-custody by default.
"""
