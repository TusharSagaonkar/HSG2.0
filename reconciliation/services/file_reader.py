class StatementFileReader:
    """Small helper for extracting a file name and raw content."""

    def read(self, file_obj):
        file_obj.seek(0)
        content = file_obj.read()
        file_obj.seek(0)
        return content
