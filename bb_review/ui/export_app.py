"""Main Textual application for interactive export."""

from textual.app import App


class ExportApp(App):
    """Interactive export application."""

    TITLE = "BB Review Export"

    def __init__(self, analyses: list = None, output_path: str | None = None):
        """Initialize the export app.

        Args:
            analyses: List of AnalysisListItem to show for selection
            output_path: Optional output file path
        """
        super().__init__()
        self.initial_analyses = analyses or []
        self.output_path = output_path
