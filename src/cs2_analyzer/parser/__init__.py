from cs2_analyzer.parser.base import DemoParserBackend, MatchMeta, ParsedDemo


def get_parser(name: str = "demoparser2") -> DemoParserBackend:
    if name == "demoparser2":
        from cs2_analyzer.parser.demoparser2_backend import DemoParser2Backend

        return DemoParser2Backend()
    if name == "cs2cd":
        from cs2_analyzer.parser.cs2cd_backend import CS2CDBackend

        return CS2CDBackend()
    raise ValueError(f"unknown parser backend: {name}")


def backend_for_path(path, default: str = "demoparser2") -> str:
    """``.parquet`` inputs are CS2CD dataset matches; everything else is a demo."""
    return "cs2cd" if str(path).endswith(".parquet") else default


__all__ = ["DemoParserBackend", "MatchMeta", "ParsedDemo", "backend_for_path", "get_parser"]
