"""GitLab policy-enforcement proxy (Warden) for the Claude agent sandbox.

Implements the §6 / 02-warden.md design: API write-filter, git G1 Smart-HTTP
proxy, durable quota state, and auditable JSONL logging — the single trust
boundary between the agent and gitlab.com.
"""

__version__ = "0.1.0"
