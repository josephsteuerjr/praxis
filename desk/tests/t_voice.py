# -*- coding: utf-8 -*-
"""Стенд «голос»: слышит ли агент — и говорит ли продукт правду, когда не слышит.

Запуск:  python tests/t_voice.py

Голос — единственная часть продукта, которой не хватает гигабайта, чтобы
работать: библиотека едет в рантайме, а модель качает владелец. Отсюда и класс
ошибки, который здесь стерегут: «включено» и «работает» — разные вещи, и между
ними три развилки (нет библиотеки, нет модели, скачана не та). Каждая обязана
называться словами, а не превращаться в тихо неработающую расшифровку.

Сети здесь нет: скачивание не проверяется, проверяется РЕШЕНИЕ — что считать
готовым, какие переменные уходят дереву и что показать владельцу.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import voice  # noqa: E402


class Ground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / ".state").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        # Библиотека подменяется НА ВРЕМЯ стенда: голос живёт в рантайме
        # поставки, а стенды гоняются системным питоном, где faster-whisper нет
        # и быть не должно. Проверять здесь среду прогона вместо решения
        # означало бы красный стенд у каждого, кто не поставил себе гигабайт
        # колёс. Ветка «библиотеки нет» проверяется отдельно и явно.
        real = voice.library
        voice.library = lambda: {"present": True, "why": ""}
        self.addCleanup(lambda: setattr(voice, "library", real))

    def install_model(self, name: str = "turbo", size: int = 1600 * 1024 * 1024) -> Path:
        """Сделать вид, что модель скачана: манифест плюс настоящая папка."""
        where = voice.models_dir(self.tree) / f"models--{name}" / "snapshots" / "abc"
        where.mkdir(parents=True)
        (where / "model.bin").write_bytes(b"0")
        voice._write(voice.models_dir(self.tree) / voice.INSTALLED, {
            "schema": voice.SCHEMA, "model": name, "repo": voice.CATALOG[name]["repo"],
            "path": str(where), "bytes": size, "got_utc": "2026-09-10T12:00:00Z"})
        return where


class NoLibrary(Ground):
    """Ветка «в рантайме нет faster-whisper» — та, что видна у поставки без голоса."""

    def test_нет_библиотеки_нет_готовности(self):
        voice.library = lambda: {"present": False, "why": "в рантайме нет faster-whisper"}
        self.install_model("turbo")
        said = voice.state(self.tree, {"voice": {"enabled": True, "model": "turbo"}})
        self.assertFalse(said["ready"])
        self.assertIn("faster-whisper", said["why"])
        self.assertEqual(voice.env_for(self.tree, {"voice": {"enabled": True}}), {})


class WhatIsReady(Ground):
    def test_выключенный_голос_не_готов_и_сказано_почему(self):
        said = voice.state(self.tree, {"voice": {"enabled": False}})
        self.assertFalse(said["ready"])
        self.assertIn("выключен", said["why"])

    def test_включён_но_модели_нет(self):
        said = voice.state(self.tree, {"voice": {"enabled": True}})
        self.assertFalse(said["ready"])
        self.assertIn("не скачана", said["why"])
        self.assertEqual(voice.env_for(self.tree, {"voice": {"enabled": True}}), {},
                         "без модели дереву не ставится НИ ОДНОЙ переменной: "
                         "полупроставленная среда — это глухота без причины")

    def test_модель_на_месте(self):
        self.install_model("turbo")
        cfg = {"voice": {"enabled": True, "model": "turbo"}}
        said = voice.state(self.tree, cfg)
        self.assertTrue(said["ready"], said["why"])
        self.assertEqual(said["why"], "")
        self.assertEqual(said["installed"]["model"], "turbo")

    def test_скачана_не_та_модель_работает_скачанная_и_это_названо(self):
        self.install_model("small")
        said = voice.state(self.tree, {"voice": {"enabled": True, "model": "turbo"}})
        self.assertTrue(said["ready"], "молчать из-за несовпадения выбора — хуже, чем слышать")
        self.assertIn("small", said["why"])
        self.assertIn("turbo", said["why"])

    def test_манифест_есть_а_папки_нет(self):
        where = self.install_model("small")
        import shutil

        shutil.rmtree(where)
        said = voice.state(self.tree, {"voice": {"enabled": True, "model": "small"}})
        self.assertFalse(said["ready"],
                         "манифест без папки — это стёртая модель, а не готовность")

    def test_каталог_отмечает_скачанное(self):
        self.install_model("small")
        said = voice.state(self.tree, {"voice": {"enabled": True, "model": "small"}})
        rows = {row["id"]: row for row in said["catalog"]}
        self.assertTrue(rows["small"]["installed"])
        self.assertFalse(rows["turbo"]["installed"])
        for row in rows.values():
            self.assertTrue(row["repo"] and row["size_mb"] and row["note"])


class WhatTreeGets(Ground):
    def test_переменные_дерева(self):
        where = self.install_model("turbo")
        cfg = {"voice": {"enabled": True, "model": "turbo", "language": "ru", "threads": 6}}
        env = voice.env_for(self.tree, cfg)
        self.assertEqual(env["PRAXIS_STT_MODEL"], str(where),
                         "дереву даётся ПУТЬ, а не имя: имя разрешается через кэш "
                         "Hugging Face и молча промахивается при смене раскладки")
        self.assertEqual(env["PRAXIS_AUDIO_MODEL_DIR"], str(self.tree / "models"))
        self.assertEqual(env["PRAXIS_STT_LOCAL_FILES_ONLY"], "1",
                         "скачивать модель посреди хода нельзя: это минуты молчания")
        self.assertEqual(env["PRAXIS_STT_CPU_THREADS"], "6")
        self.assertEqual(env["PRAXIS_STT_LANGUAGE"], "ru")
        self.assertEqual(env["PRAXIS_STT_KEEP_LOADED"], "0",
                         "дома модель в памяти не держим: 1,5 ГБ за несколько "
                         "голосовых в день")

    def test_память_держим_по_просьбе(self):
        self.install_model("turbo")
        env = voice.env_for(self.tree, {"voice": {"enabled": True, "keep_loaded": True}})
        self.assertEqual(env["PRAXIS_STT_KEEP_LOADED"], "1")

    def test_имена_переменных_те_же_что_у_дерева(self):
        """Переменные читает `live/media_audio.py`; опечатка здесь = тишина там."""
        self.install_model("turbo")
        env = voice.env_for(self.tree, {"voice": {"enabled": True}})
        media = HERE.parent.parent.parent / "live" / "media_audio.py"
        if not media.is_file():
            self.skipTest("дерева агента нет рядом — сверять не с чем")
        text = media.read_text(encoding="utf-8")
        for name in env:
            self.assertIn(name, text, f"{name} дерево не читает — переменная в пустоту")


class Choosing(Ground):
    def test_неизвестная_модель_падает_на_умолчание(self):
        self.assertEqual(voice.chosen_model({"voice": {"model": "гигантская"}}),
                         voice.DEFAULT_MODEL)
        self.assertEqual(voice.chosen_model({}), voice.DEFAULT_MODEL)

    def test_умолчание_есть_в_каталоге(self):
        self.assertIn(voice.DEFAULT_MODEL, voice.CATALOG)


class Downloading(Ground):
    def test_чужая_модель_не_качается(self):
        with self.assertRaises(SystemExit):
            voice.fetch(self.tree, "whisper-огромный", quiet=True)

    def test_ход_скачивания_виден_в_состоянии(self):
        voice._write(self.tree / "memory" / ".state" / voice.PROGRESS, {
            "schema": voice.SCHEMA, "model": "turbo", "state": "running",
            "got_bytes": 42, "total_bytes": 100, "updated_utc": "2026-09-10T12:00:00Z"})
        said = voice.state(self.tree, {"voice": {"enabled": True}})
        self.assertEqual(said["download"]["state"], "running")
        self.assertEqual(said["download"]["got_bytes"], 42)


class ChannelDoor(unittest.TestCase):
    def test_ручка_голоса_есть_и_телефону_туда_нельзя(self):
        sys.path.insert(0, str(HERE.parent))
        import deskapp  # noqa: PLC0415

        route, _ = deskapp.match_route("GET", "/api/voice")
        self.assertIsNotNone(route)
        self.assertNotIn("/api/voice", deskapp._DEVICE_PATHS)

    def test_канал_отдаёт_только_блок_voice(self):
        """Читать конфиг целиком ради одной ручки — вторая дорога к ключам."""
        import os  # noqa: PLC0415

        sys.path.insert(0, str(HERE.parent))
        import deskapp  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "helene.json"
            config.write_text(json.dumps({
                "model": {"key": "sk-живой-ключ", "base_url": "http://127.0.0.1:5011"},
                "telegram": {"bot_token": "8000:живой"},
                "voice": {"enabled": True, "model": "small"},
            }, ensure_ascii=False), encoding="utf-8")
            was = os.environ.get("HELENE_CONFIG")
            os.environ["HELENE_CONFIG"] = str(config)
            try:
                got = deskapp._voice_config()
            finally:
                if was is None:
                    os.environ.pop("HELENE_CONFIG", None)
                else:
                    os.environ["HELENE_CONFIG"] = was
        self.assertEqual(got, {"voice": {"enabled": True, "model": "small"}})
        self.assertNotIn("sk-живой-ключ", json.dumps(got, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
