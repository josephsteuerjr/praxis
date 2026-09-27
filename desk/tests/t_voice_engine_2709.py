# -*- coding: utf-8 -*-
"""Стенд «движок голоса отдельно» (1.2.1, 27.09).

Запуск:  python tests/t_voice_engine_2709.py

Егор 27.09: «почему установщик так много весит?» — больше половины был голос. С 1.2.1
движок голоса (faster-whisper, piper, ctranslate2, onnxruntime, av, numpy) не едет в
установщике Windows, а докачивается вместе с моделью одним архивом выпуска. Здесь
стерегутся три обещания:

1. граница по метаданным пакетов: голосу уходит ровно то, что нужно ТОЛЬКО ему, общее
   (typing-extensions) остаётся рантайму, остатки внутри голосовых папок уезжают тоже,
   повторная сборка ничего не двигает;
2. докачка честная: архив сверяется по сумме из паспорта, чужой или битый файл не
   оставляет движка, который выглядит поставленным; движок от другого питона не
   ставится в путь;
3. окно видит «можно докачать», а не «сборка без голоса».

Сети нет: архив собирается здесь же и отдаётся адресом file://.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
sys.path.insert(0, str(DESK / "installer"))
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(DESK / "localharness"))

import build_dist  # noqa: E402
import voice  # noqa: E402

PY = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def dist(site: Path, name: str, version: str, requires: list[str], files: dict[str, str]) -> None:
    """Пакет в папке site-packages: METADATA, RECORD и сами файлы."""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    meta = [f"Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    meta += [f"Requires-Dist: {r}" for r in requires]
    (info / "METADATA").write_text("\n".join(meta) + "\n", encoding="utf-8")
    rows = []
    for rel, text in files.items():
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        rows.append(f"{rel},sha256=x,{len(text)}")
    rows += [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,", f"../../Scripts/{name}.exe,,"]
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def fake_runtime(site: Path) -> None:
    """База (anthropic → httpx → idna) и голос (faster-whisper, piper-tts и их хвост);
    typing-extensions нужен обоим — он обязан остаться рантайму."""
    dist(site, "anthropic", "0.70.0", ["httpx", "typing-extensions"], {"anthropic/__init__.py": "a"})
    dist(site, "httpx", "0.28.1", ["idna"], {"httpx/__init__.py": "h"})
    dist(site, "idna", "3.10", [], {"idna/__init__.py": "i"})
    dist(site, "typing-extensions", "4.15.0", [], {
        "typing_extensions.py": "te", "__pycache__/typing_extensions.cpython-314.pyc": "pyc"})
    dist(site, "faster-whisper", "1.2.1", ["ctranslate2", "huggingface-hub>=0.21",
                                           "tokenizers; extra == 'dev'", "typing-extensions"],
         {"faster_whisper/__init__.py": "fw"})
    dist(site, "ctranslate2", "4.6.0", ["numpy", "pyyaml"], {"ctranslate2/ctranslate2.dll": "dll"})
    dist(site, "numpy", "2.3.4", [], {"numpy/__init__.py": "np", "numpy.libs/openblas.dll": "blas"})
    dist(site, "pyyaml", "6.0.3", [], {"yaml/__init__.py": "y", "_yaml/__init__.py": "_y"})
    dist(site, "huggingface-hub", "0.35.0", ["pyyaml", "packaging", "typing-extensions"],
         {"huggingface_hub/__init__.py": "hf"})
    dist(site, "packaging", "25.0", [], {"packaging/__init__.py": "p"})
    dist(site, "piper-tts", "1.3.0", ["onnxruntime"], {"piper/__init__.py": "pi"})
    dist(site, "onnxruntime", "1.23.0", ["numpy", "protobuf"], {"onnxruntime/capi/onnx.dll": "o"})
    dist(site, "protobuf", "6.32.1", [], {"google/protobuf/__init__.py": "pb"})
    # Скомпилированное после установки — в RECORD его нет, а место ему с голосом.
    (site / "numpy" / "__pycache__").mkdir(parents=True)
    (site / "numpy" / "__pycache__" / "__init__.cpython-314.pyc").write_text("late", encoding="utf-8")


VOICE_ONLY = {"faster-whisper", "ctranslate2", "numpy", "pyyaml", "huggingface-hub",
              "packaging", "piper-tts", "onnxruntime", "protobuf"}


class Split(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name) / "runtime" / "Lib" / "site-packages"
        self.stage = Path(self.tmp.name) / "voice-stage" / "site-packages"
        fake_runtime(self.site)

    def test_голосу_ровно_его_пакеты_общее_остаётся(self):
        got = build_dist.split_voice(self.site, self.stage)
        self.assertEqual(set(got["dists"]), VOICE_ONLY)
        for rel in ("faster_whisper/__init__.py", "numpy.libs/openblas.dll", "yaml/__init__.py",
                    "google/protobuf/__init__.py", "onnxruntime/capi/onnx.dll"):
            self.assertTrue((self.stage / rel).is_file(), rel)
            self.assertFalse((self.site / rel).exists(), rel)
        for rel in ("anthropic/__init__.py", "typing_extensions.py",
                    "__pycache__/typing_extensions.cpython-314.pyc", "idna/__init__.py"):
            self.assertTrue((self.site / rel).is_file(), f"рантайму: {rel}")
        self.assertTrue(any(p.name.startswith("numpy-") for p in self.stage.glob("*.dist-info")))
        self.assertFalse(any(p.name.startswith("numpy-") for p in self.site.glob("*.dist-info")))

    def test_остатки_внутри_голосовых_папок_уезжают(self):
        build_dist.split_voice(self.site, self.stage)
        self.assertTrue((self.stage / "numpy" / "__pycache__" / "__init__.cpython-314.pyc").is_file())
        self.assertFalse((self.site / "numpy").exists(), "пустая папка не должна остаться рантайму")

    def test_pth_ведёт_в_voice_рядом_с_рантаймом(self):
        build_dist.split_voice(self.site, self.stage)
        line = (self.site / build_dist.VOICE_PTH).read_text(encoding="utf-8").strip()
        target = (self.site / line).resolve()
        self.assertEqual(target, (Path(self.tmp.name) / "voice" / "site-packages").resolve())

    def test_вершины_без_pycache_и_dist_info(self):
        got = build_dist.split_voice(self.site, self.stage)
        self.assertIn("numpy.libs", got["tops"])
        self.assertIn("google", got["tops"])
        self.assertNotIn("__pycache__", got["tops"])
        self.assertFalse(any(t.endswith(".dist-info") for t in got["tops"]))

    def test_повторная_сборка_ничего_не_двигает(self):
        build_dist.split_voice(self.site, self.stage)
        again = build_dist.split_voice(self.site, self.stage)
        self.assertEqual(again["moved"], 0)
        self.assertEqual(set(again["dists"]), VOICE_ONLY)

    def test_без_голоса_в_рантайме_сборка_останавливается(self):
        empty = Path(self.tmp.name) / "empty"
        dist(empty, "anthropic", "0.70.0", [], {"anthropic/__init__.py": "a"})
        with self.assertRaises(SystemExit):
            build_dist.split_voice(empty, Path(self.tmp.name) / "stage2")


class Engine(unittest.TestCase):
    """Докачка: архив из настоящего `pack_voice`, адрес file://, корень — временный."""

    def setUp(self):
        if importlib.util.find_spec("faster_whisper") is not None:
            self.skipTest("в питоне прогона стоит настоящий faster-whisper — стенд о его отсутствии")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        site = base / "runtime" / "Lib" / "site-packages"
        stage = base / "voice-stage" / "site-packages"
        fake_runtime(site)
        split = build_dist.split_voice(site, stage)
        self.pack = base / "Helene-voice-9.9.9-windows.tar.zst"
        self.rec = build_dist.pack_voice(stage, self.pack, {"version": "9.9.9", "python": PY,
                                                            "dists": split["dists"]}, level=3)
        self.root = base / "Helene"
        self.root.mkdir()
        self.passport({"version": "9.9.9", "voice_pack": self.rec})
        saved = {k: os.environ.get(k) for k in ("HELENE_ROOT", "HELENE_VOICE_PACK_URL")}
        os.environ["HELENE_ROOT"] = str(self.root)
        os.environ["HELENE_VOICE_PACK_URL"] = self.pack.as_uri()
        path_before = list(sys.path)

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            sys.path[:] = path_before
            for name in [m for m in sys.modules if m.split(".")[0] in ("faster_whisper", "piper")]:
                sys.modules.pop(name, None)

        self.addCleanup(restore)

    def passport(self, data: dict) -> None:
        (self.root / "helene-build.json").write_text(json.dumps(data), encoding="utf-8")

    def test_докачка_ставит_движок_и_он_виден(self):
        before = voice.library()
        self.assertFalse(before["present"])
        self.assertTrue(before["downloadable"])
        self.assertIn("не скачан", before["why"])
        got = voice.fetch_engine()
        self.assertEqual(got["source"], "download")
        self.assertTrue((voice.engine_site() / "numpy" / "__init__.py").is_file())
        self.assertTrue(voice.library()["present"], "после докачки find_spec обязан видеть движок")
        self.assertEqual(voice.engine_installed()["sha256"], self.rec["sha256"])
        leftovers = [p.name for p in voice.engine_home().iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [], "ни .part, ни .new, ни .old")

    def test_чужой_архив_не_оставляет_движка(self):
        self.passport({"version": "9.9.9", "voice_pack": dict(self.rec, sha256="0" * 64)})
        with self.assertRaises(SystemExit):
            voice.fetch_engine()
        self.assertFalse(voice.engine_site().exists())
        self.assertEqual([p.name for p in voice.engine_home().iterdir()], [])

    def test_движок_другого_питона_не_ставится(self):
        stage = Path(self.tmp.name) / "voice-stage" / "site-packages"
        other = Path(self.tmp.name) / "other.tar.zst"
        rec = build_dist.pack_voice(stage, other, {"version": "9.9.9", "python": "2.7.18"}, level=3)
        self.passport({"version": "9.9.9", "voice_pack": rec})
        os.environ["HELENE_VOICE_PACK_URL"] = other.as_uri()
        with self.assertRaises(SystemExit):
            voice.fetch_engine()
        self.assertFalse(voice.engine_site().exists())

    def test_стоящий_движок_другого_питона_убирается_из_пути(self):
        voice.fetch_engine()
        voice._write(voice.engine_home() / voice.INSTALLED, {"python": "2.7.18"})
        self.assertFalse(voice.ensure_engine_path())
        mine = voice._norm(str(voice.engine_site()))
        self.assertTrue(all(voice._norm(p) != mine for p in sys.path))
        self.assertIn("другого питона", voice.library()["why"])

    def test_окно_слышит_можно_докачать(self):
        said = voice.state(self.root / "data", {"voice": {"enabled": True, "model": "small"}})
        self.assertFalse(said["ready"])
        self.assertIn("скачается вместе с моделью", said["why"])
        self.assertTrue(said["library"]["downloadable"])
        self.assertGreaterEqual(said["library"]["size_mb"], 0)
        speech = said["speech"]
        self.assertTrue(speech["library"]["downloadable"])

    def test_без_записи_в_паспорте_качать_нечего(self):
        self.passport({"version": "9.9.9"})
        lib = voice.library()
        self.assertFalse(lib["downloadable"])
        self.assertIn("поставка собрана без голоса", lib["why"])
        with self.assertRaises(SystemExit):
            voice.fetch_engine()

    def test_адрес_выпуска_из_настроек_обновления(self):
        os.environ.pop("HELENE_VOICE_PACK_URL")
        (self.root / "helene.json").write_text(json.dumps({"update": {
            "url": "https://api.github.com/repos/owner/repo/releases/latest"}}), encoding="utf-8")
        url = voice._pack_url(self.root, {"version": "1.2.1", "name": "Helene-voice-1.2.1-windows.tar.zst"})
        self.assertEqual(url, "https://github.com/owner/repo/releases/download/v1.2.1/"
                              "Helene-voice-1.2.1-windows.tar.zst")


class Manifest(unittest.TestCase):
    def test_опись_несёт_голос_для_установщика(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "Helene"
            (out / "runtime").mkdir(parents=True)
            (out / "runtime" / "python.exe").write_text("py", encoding="utf-8")
            got = build_dist.write_payload_manifest(out, "1.2.1", "Helene", voice={
                "dists": ["numpy"], "tops": ["numpy"], "python": "3.14.5"})
            self.assertEqual(got["voice"]["tops"], ["numpy"])
            on_disk = json.loads((out / build_dist.PAYLOAD_MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual(on_disk["voice"]["python"], "3.14.5")

    def test_имя_набора_не_zip(self):
        # Окна 1.1.x/1.2.0 берут из выпуска первый «helene-*.zip»: набор с .zip
        # перехватил бы у них кнопку «Обновить».
        self.assertFalse("Helene-voice-1.2.1-windows.tar.zst".endswith(".zip"))
        src = (DESK / "installer" / "build_dist.py").read_text(encoding="utf-8")
        self.assertIn('f"Helene-voice-{version}-windows.tar.zst"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
