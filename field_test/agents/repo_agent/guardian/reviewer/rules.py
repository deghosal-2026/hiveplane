import re
from guardian.detector.models import DetectionResult
from guardian.reviewer.models import Violation


KNOWN_STDLIB_MODULES: set[str] = {
    "os", "sys", "json", "re", "math", "datetime", "random", "collections",
    "itertools", "functools", "pathlib", "shutil", "tempfile", "subprocess",
    "hashlib", "base64", "uuid", "csv", "io", "glob", "fnmatch", "socket",
    "http", "urllib", "xml", "argparse", "configparser", "logging", "typing",
    "copy", "decimal", "statistics", "textwrap", "string", "pprint",
}

KNOWN_THIRD_PARTY_MODULES: set[str] = {
    "requests", "pytest", "fastapi", "pydantic", "httpx", "jinja2", "yaml",
    "uvicorn", "prometheus_client", "ruff", "mypy", "click",
}

KNOWN_FUNCTIONS: set[str] = {
    "print", "len", "range", "open", "str", "int", "float", "bool", "dict",
    "list", "set", "tuple", "type", "isinstance", "hasattr", "getattr",
    "setattr", "super", "zip", "map", "filter", "sorted", "reversed",
    "enumerate", "any", "all", "sum", "min", "max", "abs", "round", "format",
    "repr", "hash", "id", "input", "iter", "next", "pow", "hex", "oct", "bin",
    "ord", "chr", "callable", "del", "delattr", "vars", "dir", "locals",
    "globals", "property", "classmethod", "staticmethod",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "ImportError", "ModuleNotFoundError", "RuntimeError", "OSError",
    "FileNotFoundError", "StopIteration", "Exception", "BaseException",
    "object", "property",
}

CODE_PATTERN = re.compile(r"(?:^|[^.a-zA-Z0-9_])([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\(")
BARE_EXCEPT = re.compile(r"^\+\s*except\s*:")
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE KEY-----", "Private key"),
    (r"(?:password|passwd|pwd|secret|token)\s*[:=]\s*['\"][^'\"]+['\"]", "Hardcoded credential"),
    (r"github_pat_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"(?:api[-_]?key|apikey)\s*[:=]\s*['\"][^'\"]+['\"]", "API key"),
]


def _is_known_function(name: str) -> bool:
    parts = name.split(".")
    if len(parts) == 1:
        return name in KNOWN_FUNCTIONS or name in KNOWN_STDLIB_MODULES
    module = parts[0]
    if module in KNOWN_STDLIB_MODULES or module in KNOWN_THIRD_PARTY_MODULES:
        return True
    if module[0].islower():
        return True
    return name in KNOWN_FUNCTIONS


def _get_added_lines_for_file(diff: str, file_path: str) -> list[tuple[int, str]]:
    lines = diff.split("\n")
    in_target = False
    added: list[tuple[int, str]] = []
    diff_line_num = 0
    target_prefix = f"+++ b/{file_path}"

    for line in lines:
        if line.startswith("+++ b/"):
            in_target = line == target_prefix
            diff_line_num = 0
            continue

        if not in_target:
            continue

        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                diff_line_num = int(match.group(1))
            continue

        if line.startswith("+"):
            added.append((diff_line_num, line))
        diff_line_num += 1 if line.startswith(" ") or line.startswith("+") else 0

    return added


def hallucinated_api(
    detections: list[DetectionResult],
    diff: str,
    severity: str,
) -> list[Violation]:
    violations: list[Violation] = []
    ai_files = {d.file_path for d in detections if d.confidence in ("high", "medium")}

    for file_path in ai_files:
        added_lines = _get_added_lines_for_file(diff, file_path)
        for line_num, line in added_lines:
            content = line[1:] if line.startswith("+") else line
            if content.lstrip().startswith(("def ", "class ", "@")):
                continue
            for match in CODE_PATTERN.finditer(content):
                func_name = match.group(1)
                if func_name.startswith("self.") or func_name.startswith("cls."):
                    continue
                if _is_known_function(func_name):
                    continue
                violations.append(Violation(
                    rule_name="hallucinated-api",
                    severity=severity,
                    file_path=file_path,
                    line=line_num,
                    pattern_found=func_name,
                    explanation=f"Function '{func_name}' is not recognized as a known standard library or common third-party API. This may be a hallucinated API call.",
                    remediation=f"Verify that '{func_name}' exists in your project's dependencies or documentation. Replace with a known API if it does not exist.",
                ))
    return violations


def missing_error_handling(
    detections: list[DetectionResult],
    diff: str,
    severity: str,
) -> list[Violation]:
    violations: list[Violation] = []
    ai_files = {d.file_path for d in detections if d.confidence in ("high", "medium")}

    for file_path in ai_files:
        added_lines = _get_added_lines_for_file(diff, file_path)
        for line_num, line in added_lines:
            if BARE_EXCEPT.search(line):
                violations.append(Violation(
                    rule_name="missing-error-handling",
                    severity=severity,
                    file_path=file_path,
                    line=line_num,
                    pattern_found="bare except:",
                    explanation="A bare 'except:' block catches all exceptions, including SystemExit and KeyboardInterrupt. This can mask unexpected errors and make debugging difficult.",
                    remediation="Catch specific exception types (e.g., 'except ValueError:') instead of using a bare except. Add logging or proper error handling in the block.",
                ))
    return violations


def hardcoded_secrets(
    detections: list[DetectionResult],
    diff: str,
    severity: str,
) -> list[Violation]:
    violations: list[Violation] = []
    ai_files = {d.file_path for d in detections if d.confidence in ("high", "medium")}

    for file_path in ai_files:
        if "/test" in file_path or file_path.startswith("test_"):
            continue
        added_lines = _get_added_lines_for_file(diff, file_path)
        for line_num, line in added_lines:
            content = line[1:] if line.startswith("+") else line
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    violations.append(Violation(
                        rule_name="hardcoded-secrets",
                        severity=severity,
                        file_path=file_path,
                        line=line_num,
                        pattern_found=label,
                        explanation=f"A potential {label} was found inlined in source code. Hardcoded credentials are a security risk, especially in AI-generated code.",
                        remediation=f"Replace the hardcoded {label.lower()} with an environment variable (e.g., os.getenv('VARIABLE_NAME')) or a secrets manager. Add the value to your CI/CD secrets.",
                    ))
    return violations
