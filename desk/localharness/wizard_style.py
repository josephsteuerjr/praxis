"""Native migration surfaces use Hélène's paper, ink and peach palette."""
from pathlib import Path

STYLE='''
QDialog {background:#f7eddf; color:#51463b;}
QWidget {font-family:"Golos Text","Segoe UI",sans-serif; font-size:14px; color:#51463b;}
QLabel {background:transparent;}
QLabel#brand {font-size:12px;color:#93816d;letter-spacing:2px;}
QLabel#headline {font-family:"Zen Kurenaido","Golos Text",sans-serif;font-size:30px;font-weight:400;}
QLabel#steps {font-size:12px;color:#93816d;padding:8px 0;}
QLabel#caption {font-size:12px;color:#93816d;}
QLineEdit,QPlainTextEdit,QComboBox {background:#fcf7ef;border:1px solid #d9cdbb;border-radius:10px;padding:10px;selection-background-color:#ecc8ab;}
QPushButton {background:#fcf7ef;color:#51463b;border:1px solid #d9cdbb;border-radius:16px;padding:12px 22px;min-height:18px;}
QPushButton:hover {background:#f1e1cd;border-color:#b9a58e;}
QPushButton:focus {border:2px solid #ae8062;}
QPushButton[primary="true"] {background:#edc9ac;border-color:#edc9ac;}
QPushButton[primary="true"]:hover {background:#e5ba97;}
QPushButton:disabled {color:#b0a393;background:#eee5d8;border-color:#e1d6c6;}
QProgressBar {background:#e7dccd;border:0;border-radius:3px;max-height:6px;color:transparent;}
QProgressBar::chunk {background:#bf9475;border-radius:3px;}
QScrollArea {background:transparent;border:0;}
QCheckBox {spacing:10px;}
'''


def apply(dialog,root):
    from PySide6.QtGui import QFontDatabase
    # The same offline fonts as the main renderer; failed font admission uses
    # the native system fallback and never blocks migration.
    for path in (Path(root)/'app/static').rglob('*.woff2'):
        if path.name.startswith(('GolosText','ZenKurenaido')):QFontDatabase.addApplicationFont(str(path))
    dialog.setStyleSheet(STYLE)
    screen=dialog.screen().availableGeometry()
    dialog.resize(min(720,screen.width()-48),min(520,screen.height()-64))


def progress_text(value):
    words={'preserving':'Переносим память и код','verifying':'Проверяем сохранённые файлы',
           'unpacking':'Распаковываем программу','starting':'Открываем Hélène'}
    stage=words.get(value.get('stage'),'Подготавливаем перенос')
    done=value.get('bytes',0)/1048576;total=value.get('total_bytes',0)/1048576
    return stage+' · '+(f'{done:.0f} из {total:.0f} МБ' if total else f'{done:.0f} МБ')
