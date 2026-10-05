"""앱 계층 - 진단 로그 파일."""
import logging
import os
import tempfile
import unittest

from se650.app.logfile import FILE_NAME, enable_file_logging


class LogFileTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("se650")
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                handler.close()

    def test_library_logs_written_to_file(self):
        directory = tempfile.mkdtemp()
        path = enable_file_logging(directory)
        self.assertEqual(path, os.path.join(directory, FILE_NAME))
        logging.getLogger("se650.transport.connection").warning("test-marker")
        for handler in logging.getLogger("se650").handlers:
            handler.flush()
        with open(path, encoding="utf-8") as f:
            self.assertIn("test-marker", f.read())

    def test_unwritable_directory_falls_back_to_home(self):
        path = enable_file_logging(os.path.join(tempfile.mkdtemp(), "missing", "dir"))
        self.assertTrue(path.startswith(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
