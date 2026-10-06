"""pytest entrypoint for test isolation.

Redirects every module's BASE to a disposable sandbox *before* test collection and
fences the checkout's live tree, so her memory/soul tree is never touched. See
_sandbox.py for the why/how and for the 06.07.2026 run this answers for.
"""
import _sandbox

_sandbox.activate_if_testing()
# Пояс поверх подтяжек: если pytest запущен обёрткой, которую `_started_by_test_runner`
# не узнал, `activate_if_testing` вернёт None и забор не встанет сам по себе.
_sandbox.install_live_tree_guard()

# 29.09, урок full4 (478 красных под голым pytest). Пины стенда ставит только вход
# `praxis_test.py` через `_standenv.sanitize()`; под pytest их не было вовсе, и
# `serverd_client` честно шёл на реальный /run/praxis-serverd — 316 connect на
# боевой сокет за прогон, всё ронял _netwatch-забор. Здесь тот же пин, тем же
# инструментом, но для pytest-входа: прогон больше не зависит от того, КТО его
# запустил. Тесты, которым нужен serverd НАМЕРЕННО, патчат SOCK/TOKEN_FILE у себя —
# пин им не мешает (см. комментарий в _standenv.ENV_PIN).
import _standenv

_dropped_by_conftest = _standenv.sanitize()

# Тот же вход, тот же детектор: под praxis_test.py ставится и sanitize, и
# leak-детектор; под pytest до сих пор ставился только sanitize — и два теста
# LeakDetectorIsInstalledAndWorks были красными. Теперь оба входа эквивалентны.
_standenv.install_leak_detector()
