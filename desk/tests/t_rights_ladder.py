# -*- coding: utf-8 -*-
"""Стенд лестницы прав: песочница и брокер программно НЕ соединены (04.10).

Слово Егора: «пользователю придётся поверить, что песочница программно у нас
не соединена с брокером, иначе будет вранье, ведь так оно и есть». Поверию
здесь не место — место доказательству. Стенд держит три стены разъединения,
по одной на каждую дверь, которой ограда могла бы узнать о брокере:

  1. ТОКЕН. Секрет трубы (`memory/.state/broker-token`) и обмен просьбами
     (`broker-asks/answers.json`) лежат в ЕДИНОМ списке секретов ограды
     (`fence.secret_paths`) — они закрыты и контейнеру, и файловым рукам.
  2. СОКЕТ (Linux). Каталог брокерного сокета `/run` не связывается в
     bubblewrap ни в системный слой, ни в сетевой — в песочнице его просто нет.
  3. ТРУБА (Windows). DACL канала службы даёт доступ только СИСТЕМЕ и
     владельцу: AppContainer-токен рук агента (S-1-15-…) дверь не открывает.

Плюс: подпись верхней ступени лестницы обязана честно говорить, что при
включённой нулевой сессии песочница перестаёт ограничивать агента, — иначе
карточка врала бы в момент самого доверительного выбора.

Запуск:  python tests/t_rights_ladder.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localharness import fence, fence_posix, modes  # noqa: E402

DESK = Path(__file__).resolve().parent.parent


class SandboxAndBrokerAreApart(unittest.TestCase):
    """Три стены разъединения — по одной на каждую возможную дверь."""

    def test_broker_exchange_files_are_in_the_single_secret_list(self):
        """Стена 1 — токен: единый список секретов закрывает и трубу, и обмен.

        Список один для прав контейнера и файловых рук; выкинутый из него файл
        значил бы «контейнер не видит, но рука shell прочитает» — это не стена.
        """
        secrets = fence.secret_paths(Path("/opt/helene"), Path("/data"))
        names = {p.name for p in secrets}
        for name in ("broker-token", "broker-asks.json", "broker-answers.json"):
            self.assertIn(name, names, f"{name} выпал из списка секретов ограды")

    def test_broker_socket_directory_is_not_mounted_into_bubblewrap(self):
        """Стена 2 — сокет: `/run` не связывается в песочницу Linux.

        Будущий root-резидент будет слушать сокет в `/run/helene/`; если `/run`
        (или его символические имена) попадёт в системный или сетевой слой
        bwrap, песочница увидит дверь — проверка покраснеет раньше владельца.
        """
        for layer in (fence_posix.SYSTEM_RO, fence_posix.NETWORK_RO):
            for path in layer:
                self.assertNotIn(
                    "run", Path(path).parts,
                    f"{path} связывается в песочницу — там будет виден сокет брокера",
                )

    def test_service_pipe_dacl_has_no_appcontainer_grant(self):
        """Стена 3 — труба: DACL канала службы не пускает песочницу Windows.

        Руки агента идут с AppContainer-токеном; канал даёт доступ только
        СИСТЕМЕ (SY) и владельцу. Появление в SDDL группы «все пакеты
        приложений» (S-1-15-2/3) открывало бы трубу песочнице — держим
        структурно, по исходнику общего модуля службы и окна.
        """
        src = (DESK / "common" / "broker.rs").read_text(encoding="utf-8")
        sddl = next(
            (line for line in src.splitlines() if '"D:P(A;;GA;;;' in line),
            "",
        )
        self.assertTrue(sddl, "SDDL трубы не найден в common/broker.rs — где дверь?")
        self.assertIn("SY", sddl, "СИСТЕМЕ доступ положен")
        self.assertIn("{}", sddl, "владельцу доступ положен (шаблон SID)")
        for banned in ("S-1-15-2", "S-1-15-3", "AC"):
            # «AC» ловим отдельным словом в гранте, а не в любом месте строки
            if banned == "AC":
                self.assertNotRegex(sddl, r";;;AC[)\s]")
            else:
                self.assertNotIn(banned, sddl,
                                 f"в DACL появился AppContainer-грант {banned}")


class LadderDoesNotLie(unittest.TestCase):
    """Подпись верхней ступени обязана говорить вслух про песочницу."""

    def test_session0_warning_names_the_fence_truth(self):
        self.assertIn("перестаёт", modes.LADDER_SESSION0_WARNING)
        self.assertIn("правами систем", modes.LADDER_SESSION0_WARNING.lower())
        self.assertIn("журнал", modes.LADDER_SESSION0_WARNING)

    def test_catalogue_of_fences_is_untouched_for_the_installer(self):
        """Установщик по-прежнему видит ДВЕ ограды — третья ступень выбор
        настроек, до службы её рано (и в мастере службы ещё нет)."""
        self.assertEqual([c["name"] for c in modes.catalogue()],
                         ["sandbox", "interactive"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
