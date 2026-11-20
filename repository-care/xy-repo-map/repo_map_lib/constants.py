CODE_SUFFIXES = {
    ".py": "Python",
    ".sh": "Shell",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
}

UNSUPPORTED_CODE_SUFFIXES = {
    ".go",
    ".java",
    ".kt",
    ".swift",
    ".cpp",
    ".cc",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".scala",
    ".lua",
    ".css",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
}

TEST_DIRECTORY_NAMES = {"tests", "test", "benches", "test_data", "testdata", "fixtures"}
LINE_NAMES = ("code", "docs", "comments", "tests")
