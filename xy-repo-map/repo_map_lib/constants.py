"""Define source suffixes and line-count categories for repository maps."""

CODE_SUFFIXES = {
    ".py": "Python",
    ".sh": "Shell",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".ts": "TS/JS",
    ".tsx": "TS/JS",
    ".js": "TS/JS",
    ".jsx": "TS/JS",
    ".mjs": "TS/JS",
    ".cjs": "TS/JS",
}

UNSUPPORTED_CODE_SUFFIXES = {
    ".go", ".java", ".kt", ".swift", ".cpp", ".cc", ".hpp", ".cs",
    ".rb", ".php", ".scala", ".lua",
}

TEST_DIRECTORY_NAMES = {"tests", "test", "benches", "test_data", "testdata", "fixtures"}
LINE_NAMES = ("code", "docs", "comments", "tests")
FILE_GROUPS = ("registered", "reached", "directory", "shared", "whole")
MARKER_NAMES = ("等你拍板", "还没实现", "没归到功能的代码")
