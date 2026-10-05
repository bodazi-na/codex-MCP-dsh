"""The A2A Agent Card that advertises this DeepSeek Harness bridge."""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from .config import Settings

SKILL_DSH_TASK = "dsh-task"

_BEARER_SCHEME = "bearer"


def _security(settings: Settings):
    """Return (scheme names, requirements, scheme) when a token is configured."""
    if not settings.token:
        return [], [], None
    scheme = HTTPAuthSecurityScheme(
        description=(
            "Send `Authorization: Bearer <token>` on every JSON-RPC / REST "
            "request. The Agent Card stays public so callers can discover how "
            "to authenticate."
        ),
        scheme="bearer",
        bearer_format="opaque",
    )
    requirement = SecurityRequirement(schemes={_BEARER_SCHEME: StringList()})
    return [_BEARER_SCHEME], [requirement], scheme


def build_agent_card(settings: Settings) -> AgentCard:
    """Build the Agent Card served at ``/.well-known/agent-card.json``."""
    base_url = settings.public_url.rstrip("/")

    card = AgentCard(
        name=settings.agent_name,
        description=(
            "Serves the locally installed DeepSeek Harness (dsh) as an A2A "
            "agent. A caller sends a task; the bridge runs "
            f"`dsh --profile {settings.profile}` in {settings.workdir}, streams "
            "back the tool calls it makes and the text it produces, and returns "
            "the final answer as an artifact. Follow-up messages that reuse the "
            "same A2A context resume the same DSH session."
        ),
        version=settings.agent_version,
        provider=AgentProvider(
            organization="DSH A2A bridge",
            url="https://github.com/a2aproject/A2A",
        ),
        documentation_url="https://a2a-protocol.org/",
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url=base_url,
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=SKILL_DSH_TASK,
                name="DeepSeek Harness task",
                description=(
                    "Run one task with the local DeepSeek Harness agent: read "
                    "and change files, run commands, use its skills and memory, "
                    "and report back. Each A2A context maps to one persistent "
                    "DSH session."
                ),
                tags=["dsh", "deepseek-harness", "agent", "automation", "skills"],
                examples=[
                    "Summarize the open TODOs in this workspace",
                    "Write a short report about today's A2A test results",
                    "Explain what this project does",
                ],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
    )

    if settings.token:
        _, requirements, scheme = _security(settings)
        assert scheme is not None
        card.security_schemes[_BEARER_SCHEME].CopyFrom(
            SecurityScheme(http_auth_security_scheme=scheme)
        )
        for requirement in requirements:
            card.security_requirements.append(requirement)
    return card
