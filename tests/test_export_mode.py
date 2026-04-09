import unittest

from app.main import ExportRequest, resolve_export_mode


class ExportModeTests(unittest.TestCase):
    def test_test_mode_forces_direct_export(self) -> None:
        payload = ExportRequest(export_mode="bulk", test_mode=True)

        self.assertEqual(resolve_export_mode(payload), "direct")

    def test_non_test_mode_keeps_requested_export_mode(self) -> None:
        payload = ExportRequest(export_mode="bulk", test_mode=False)

        self.assertEqual(resolve_export_mode(payload), "bulk")


if __name__ == "__main__":
    unittest.main()
