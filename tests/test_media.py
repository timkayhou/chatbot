import io
import unittest

try:
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
    MEDIA_TESTS = True
except ImportError:
    MEDIA_TESTS = False

from workbench.media import MediaUnavailable, analyze_image
from workbench.guard import GuardPolicy, check_text


@unittest.skipUnless(MEDIA_TESTS, "media test extras are not installed")
class MediaTests(unittest.TestCase):
    def test_decodes_real_qr_image(self):
        image = qrcode.make("https://blocked.example/path")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        result = analyze_image(stream.getvalue())
        self.assertIn("https://blocked.example/path", result.qr_payloads)
        self.assertGreater(result.width, 0)

    def test_rejects_invalid_image(self):
        with self.assertRaises((ValueError, MediaUnavailable)):
            analyze_image(b"not an image")

    def test_ocr_text_is_checked_by_guard(self):
        image = Image.new("RGB", (900, 180), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("arial.ttf", 64)
        draw.text((30, 40), "Call 090-1234-5678", font=font, fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        analysis = analyze_image(stream.getvalue())
        result = check_text("safe body", GuardPolicy(), extracted_text=analysis.extracted_text)
        self.assertEqual("block", result.verdict)
        self.assertTrue(any(hit.source == "ocr" for hit in result.hits))


if __name__ == "__main__":
    unittest.main()
