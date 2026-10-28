# Не запускать `git worktree prune` из другой namespace

Если один и тот же репозиторий виден из контейнера и с хоста под разными абсолютными путями, `git worktree prune` в одной namespace может принять живые worktree другой за исчезнувшие и удалить их metadata.

## Правило

- Для временной ветки: `git worktree add` → работа/применение → `git worktree remove --force <ровно свой путь>`.
- Не завершать такой рецепт глобальным `git worktree prune`, если в репозитории могут жить Forge/proposal worktree из другой namespace.
- Перед удалением смотреть `git worktree list --porcelain` в той же namespace, где worktree создан.
- При уже оборванной metadata сначала сохранить diff/дерево и хэш как evidence; не реконструировать `.git/worktrees/*` вслепую. После переноса в здоровую изоляцию старую задачу честно abandon/lost, а severed `.git` переименовать, сохранив файлы.

## Наблюдавшийся сбой

Хост видел контейнерные пути `/app/.proposals/<id>` как отсутствующие; `prune` удалил `.git/worktrees/<id>`, из-за чего safety-point и тестовые руки получали `not a git repository`.
