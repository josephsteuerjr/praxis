"""Ordered transport chunks belonging to one logical assistant send.

Only a complete contiguous batch collapses. Missing/reordered rows are not guessed
from text or message IDs. Metadata is also in capture payloads, binding rendering.
"""


def batch_groups(metadata):
    groups = []
    i = 0
    while i < len(metadata):
        batch = metadata[i]
        n = batch.get('count') if isinstance(batch, dict) else None
        if (isinstance(batch, dict) and set(batch) == {'id', 'index', 'count'}
                and isinstance(batch['id'], str) and batch['id']
                and type(n) is int and n > 0 and batch['index'] == 0
                and i + n <= len(metadata)
                and all(metadata[i + j] == dict(id=batch['id'], index=j, count=n)
                        for j in range(n))):
            groups.append(list(range(i, i + n)))
            i += n
        else:
            groups.append([i])
            i += 1
    return groups
