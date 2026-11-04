"""The application owns its light theme, including popup and dialog palettes."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def apply_light_theme(app):
    app.setStyle('Fusion')
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette=QPalette()
    colors={
        QPalette.Window:'#ededed',QPalette.WindowText:'#636363',
        QPalette.Base:'#ffffff',QPalette.AlternateBase:'#e6e6e6',
        QPalette.ToolTipBase:'#ffffe5',QPalette.ToolTipText:'#484848',
        QPalette.Text:'#484848',QPalette.Button:'#ededed',QPalette.ButtonText:'#636363',
        QPalette.BrightText:'#ffffff',QPalette.Highlight:'#c9c9c9',QPalette.HighlightedText:'#484848',
        QPalette.Link:'#848484',QPalette.LinkVisited:'#848484',QPalette.PlaceholderText:'#afafaf',
        QPalette.Light:'#ffffff',QPalette.Midlight:'#e0e0e0',QPalette.Dark:'#9e9e9e',
        QPalette.Mid:'#cccccc',QPalette.Shadow:'#989898',QPalette.Accent:'#c9c9c9',
    }
    for role,color in colors.items():
        for group in [QPalette.Active,QPalette.Inactive,QPalette.Disabled]:
            palette.setColor(group,role,QColor(color))
    for role in [QPalette.Text,QPalette.WindowText,QPalette.ButtonText]:
        palette.setColor(QPalette.Disabled,role,QColor('#999999'))
    app.setPalette(palette)
    return palette
