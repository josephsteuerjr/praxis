"""Editable code map and active skill sources shared by every Praxis turn."""
from pathlib import Path


def _document(path: Path, label: str) -> str:
    # Do not follow a skill link into another person's files or credentials.
    if path.is_symlink() or not path.is_file():
        return f'[{label} — unavailable: expected a regular file]'
    try:
        content = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        return f'[{label} — unreadable: {type(exc).__name__}]'
    return f'[{label}]\n{content}'


def render(base: Path, skills: Path) -> str:
    """Active skills use the capability catalog's on-disk slug convention."""
    parts = [_document(base / 'CODEMAP.md', 'CODEMAP.md')]
    if skills.is_symlink() or skills.parent.is_symlink():
        parts.append('[soul/skills — linked directory]')
    else:
        try:
            entries = sorted(skills.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            parts.append(f'[soul/skills — unreadable: {type(exc).__name__}]')
        else:
            for path in entries:
                if path.suffix == '.md' and path.name != 'INDEX.md':
                    parts.append(_document(path, f'soul/skills/{path.name}'))
    return '\n\n---\nMy working code map and active skills (editable sources)\n\n' + '\n\n'.join(parts)
