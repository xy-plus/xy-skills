"""Define source suffixes and line-count categories for repository maps."""

CODE_SUFFIXES = {
    ".py": "Python",
    ".sh": "Shell",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
}

UNSUPPORTED_CODE_SUFFIXES = {
    ".go", ".java", ".kt", ".swift", ".cpp", ".cc", ".hpp", ".cs",
    ".rb", ".php", ".scala", ".lua", ".css",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
}

TEST_DIRECTORY_NAMES = {"tests", "test", "benches", "test_data", "testdata", "fixtures"}
LINE_NAMES = ("code", "docs", "comments", "tests")
FILE_GROUPS = ("registered", "reached", "directory", "shared", "whole")
MARKER_NAMES = ("等你拍板", "还没实现", "没归到功能的代码或测试")
