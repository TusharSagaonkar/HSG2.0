from reconciliation.services.parsers.csv_parser import CSVParser
from reconciliation.services.parsers.xlsx_parser import XLSXParser


class ParserRegistry:
    """Registry mapping file types or format names to parser classes."""

    DEFAULTS = {
        "csv": CSVParser,
        "txt": CSVParser,
        "xlsx": XLSXParser,
        "xls": XLSXParser,
    }

    @classmethod
    def get_parser_class(cls, file_type: str, parser_path: str | None = None):
        if parser_path:
            module_path, class_name = parser_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            return getattr(module, class_name)
        return cls.DEFAULTS.get(file_type.lower())
