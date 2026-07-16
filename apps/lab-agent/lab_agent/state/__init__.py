"""Internal durable-ledger modules backing :mod:`lab_agent.state_store`.

Not a public import surface -- external code should go through
``lab_agent.state_store.StateStore`` (and ``lab_agent.recovery`` for
crash-safe execution), not these submodules directly.
"""
