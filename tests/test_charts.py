import unittest

import charts


class ChartTests(unittest.TestCase):
    def test_trend_filename_cannot_escape_chart_directory(self):
        name = charts._trend_filename("../../outside\\bad:name")
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertTrue(name.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
