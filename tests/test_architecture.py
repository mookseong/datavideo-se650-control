"""계층 규칙.

1) 의존: 아래 계층은 위 계층을 import 하지 않는다.
       app -> control -> protocol
                      -> transport        (protocol 과 transport 는 서로 모름)
       i18n 은 모든 계층이 쓰는 기반 모듈이라 어떤 계층도 import 하지 않는다.
2) 번역 키: 각 계층은 자기 네임스페이스 키만 쓴다 (transport 코드 -> "transport.*").
3) 코드에 적은 번역 키는 기준 언어팩(영어)에 반드시 있다.
"""
import ast
import pathlib
import re
import unittest

from se650.i18n.catalog import load_all

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "se650"
LAYERS = ("transport", "protocol", "control", "app")
FORBIDDEN = {
    "transport": {"protocol", "control", "app"},
    "protocol": {"transport", "control", "app"},
    "control": {"app"},
    "i18n": set(LAYERS),
}
KEY_PATTERN = re.compile(r"^(%s)\.[a-z0-9_]+(\.[a-z0-9_]+)+$" % "|".join(LAYERS))


def imported_layers(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layer_parts = path.relative_to(PACKAGE).parts
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:       # 상대 import -> 절대 경로로
                base = list(layer_parts[:-1])[:len(layer_parts) - node.level]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
                if module.startswith("se650."):
                    module = module[len("se650."):]
            yield module.split(".")[0], node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("se650."):
                    yield alias.name.split(".")[1], node.lineno


def translation_keys(path: pathlib.Path):
    """파일 안의 문자열 상수 중 번역 키 모양인 것."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and KEY_PATTERN.match(node.value):
            yield node.value, node.lineno


class LayerTests(unittest.TestCase):
    def test_layers_only_depend_downward(self):
        violations = []
        for layer, forbidden in FORBIDDEN.items():
            for path in (PACKAGE / layer).rglob("*.py"):
                for imported, line in imported_layers(path):
                    if imported in forbidden:
                        violations.append("%s:%d -> %s" % (path.relative_to(PACKAGE), line, imported))
        self.assertEqual(violations, [])

    def test_each_layer_uses_only_its_own_keys(self):
        violations = []
        for layer in LAYERS:
            for path in (PACKAGE / layer).rglob("*.py"):
                for key, line in translation_keys(path):
                    if not key.startswith(layer + "."):
                        violations.append("%s:%d %s" % (path.relative_to(PACKAGE), line, key))
        self.assertEqual(violations, [])

    def test_keys_used_in_code_exist(self):
        reference = load_all()["en"].messages
        prefixes = {key.rsplit(".", 1)[0] + "." for key in reference}    # "app.status." + 변수 같은 조합용
        missing = []
        for path in PACKAGE.rglob("*.py"):
            for key, line in translation_keys(path):
                if key not in reference and key not in prefixes:
                    missing.append("%s:%d %s" % (path.relative_to(PACKAGE), line, key))
        self.assertEqual(missing, [])

    def test_no_hardcoded_korean_in_code(self):
        """화면/로그 문구는 언어팩에만. (주석·docstring 은 허용)"""
        found = []
        for path in PACKAGE.rglob("*.py"):
            if path.name == "simulator.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(n)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                        and re.search("[가-힣]", node.value)):
                    found.append("%s:%d" % (path.relative_to(PACKAGE), node.lineno))
        self.assertEqual(found, [])

    def test_checker_detects_violation(self):
        """검사기 자체가 동작하는지 (상대 import 해석 확인)."""
        modules = {m for m, _ in imported_layers(PACKAGE / "control" / "switcher.py")}
        self.assertIn("protocol", modules)


if __name__ == "__main__":
    unittest.main()
