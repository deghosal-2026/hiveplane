"""Rule packs — versioned, pre-built rule collections."""

from cauterule.packs.create import create_pack, select_rules
from cauterule.packs.deps import parse_dep, read_lockfile, resolve, write_lockfile
from cauterule.packs.format import PackManifest, create_manifest, validate_manifest
from cauterule.packs.install import compare_versions, install_pack, sha256_file
from cauterule.packs.loader import load_pack
from cauterule.packs.manager import list_packs, pack_info
from cauterule.packs.publish import auto_notes, build_asset, lint_readme, publish_pack
from cauterule.packs.readonly import check_readonly
from cauterule.packs.safety import (
    DEFAULT_MIN_SAFETY_SCORE,
    score_pack_safety,
    score_rule_safety,
)
from cauterule.packs.semver import Version, bump, suggest_bump
from cauterule.packs.semver import parse as parse_version
from cauterule.packs.semver import satisfies as satisfies_version
from cauterule.packs.share import import_gist, share_rule
from cauterule.packs.spec import PackSpec, parse_spec

__all__ = [
    "DEFAULT_MIN_SAFETY_SCORE",
    "PackManifest",
    "PackSpec",
    "Version",
    "auto_notes",
    "build_asset",
    "bump",
    "check_readonly",
    "compare_versions",
    "create_manifest",
    "create_pack",
    "import_gist",
    "install_pack",
    "lint_readme",
    "list_packs",
    "load_pack",
    "pack_info",
    "parse_dep",
    "parse_spec",
    "parse_version",
    "publish_pack",
    "read_lockfile",
    "resolve",
    "satisfies_version",
    "score_pack_safety",
    "score_rule_safety",
    "select_rules",
    "sha256_file",
    "share_rule",
    "suggest_bump",
    "validate_manifest",
    "write_lockfile",
]
