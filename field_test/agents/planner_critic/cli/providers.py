"""``plancritic providers`` — config-driven provider management (F-21, F-23).

Adds/lists/removes provider definitions in the TOML config file and binds
roles to providers (distinct planner vs. critic, F-23). Zero paid LLM on the
default path: the default config points ``planner``/``critic`` at a local
OpenAI-compatible endpoint (OMLX/Ollama/vLLM).
"""

from __future__ import annotations

import argparse

from ..llm.registry import DEFAULT_ROLES, ProviderRegistry

DEFAULT_CONFIG_PATH = "plancritic.toml"


def build_providers_parser() -> argparse.ArgumentParser:
    """Build the ``providers`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic providers",
        description="Manage LLM provider definitions (F-21)",
        add_help=False,
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Config file path")
    sub = parser.add_subparsers(dest="action", metavar="ACTION")

    add = sub.add_parser("add", help="Add or replace a provider")
    add.add_argument("name", help="Provider id")
    add.add_argument("--base-url", required=True, help="Endpoint base URL")
    add.add_argument("--model", required=True, help="Model name")
    add.add_argument("--transport", default="openai-compatible")
    add.add_argument("--api-key", default=None)
    add.add_argument("--role", choices=list(DEFAULT_ROLES), default=None, help="Bind to a role")

    sub.add_parser("list", help="List configured providers and role bindings")
    rm = sub.add_parser("rm", help="Remove a provider")
    rm.add_argument("name", help="Provider id")
    return parser


def run_providers(argv: list[str]) -> int:
    """Run a providers subcommand; return a process exit code.

    Args:
        argv: Arguments for the ``providers`` subcommand.

    Returns:
        0 on success, 1 on a config failure.
    """
    args = build_providers_parser().parse_args(argv)
    try:
        registry = ProviderRegistry.load(args.config)
        if args.action == "add":
            registry.add(
                args.name,
                base_url=args.base_url,
                model=args.model,
                transport=args.transport,
                api_key=args.api_key,
                role=args.role,
            )
            registry.save()
            print(f"added provider '{args.name}'")
        elif args.action == "rm":
            if registry.remove(args.name):
                registry.save()
                print(f"removed provider '{args.name}'")
            else:
                print(f"no provider named '{args.name}'")
                return 1
        else:
            _print_registry(registry)
        return 0
    except Exception as err:  # config read/write or save failures
        print(f"providers failed: {err}")
        return 1


def _print_registry(registry: ProviderRegistry) -> None:
    """Render the registry for ``providers list``."""
    print("roles:")
    for role in DEFAULT_ROLES:
        print(f"  {role} -> {registry.roles.get(role, '(unset)')}")
    if not registry.providers:
        print("providers: (none configured)")
        return
    print("providers:")
    for name, spec in registry.providers.items():
        auth = " (api-key set)" if spec.api_key else ""
        print(f"  {name}: {spec.transport} @ {spec.base_url} [{spec.model}]{auth}")
