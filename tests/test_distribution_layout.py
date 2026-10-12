"""Offline profile selection after the repository layout migration; no network."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "portable_layout_test_module", ROOT / "scripts/build_portable.py")
portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portable)


class OfflineProfileLayoutTests(unittest.TestCase):
    def write_profile(self, root, name, scipy):
        path = root / "requirements" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "numpy==2.1.2\npillow==12.2.0\npraat-parselmouth==0.4.7\n"
            f"scipy=={scipy}\n", encoding="utf-8")

    def test_each_target_reads_its_grouped_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            self.write_profile(source, "requirements-cloud-cp311.txt", "1.15.3")
            self.write_profile(source, "requirements-cloud-cp312.txt", "1.15.3")
            self.write_profile(source, "requirements-windows-offline-cp312.txt", "1.18.0")
            for abi, operating_system, scipy in (
                ("cp311", "linux", "1.15.3"),
                ("cp312", "linux", "1.15.3"),
                ("cp312", "windows", "1.18.0"),
            ):
                with self.subTest(abi=abi, operating_system=operating_system):
                    result = portable.locked_dependencies(source, abi, operating_system)
                    self.assertEqual(set(result), portable.DEPENDENCIES)
                    self.assertEqual(result["scipy"], scipy)
                    self.assertEqual(result["praat-parselmouth"], "0.4.7")

    def test_stale_root_lock_cannot_override_grouped_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            self.write_profile(source, "requirements-cloud-cp312.txt", "1.15.3")
            (source / "requirements-cloud-cp312.txt").write_text(
                "scipy==99.0\n", encoding="utf-8")
            self.assertEqual(
                portable.locked_dependencies(source, "cp312", "linux")["scipy"],
                "1.15.3")


if __name__ == "__main__":
    unittest.main()
