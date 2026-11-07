import json
import unittest
from pathlib import Path


class ManifestTests(unittest.TestCase):
    def test_inference_resources_are_pinned(self):
        path = Path(__file__).parents[1] / "manifests" / "inference.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        ids = set()
        for resource in manifest["resources"]:
            self.assertNotIn(resource["id"], ids)
            ids.add(resource["id"])
            self.assertTrue(resource["url"].startswith("https://"))
            self.assertRegex(resource["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(resource["bytes"], 0)
            self.assertFalse(Path(resource["target"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
